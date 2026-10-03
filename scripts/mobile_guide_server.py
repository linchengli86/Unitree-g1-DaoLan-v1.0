#!/usr/bin/env python3
"""Mobile text frontend and Unitree G1 TTS bridge.

The server intentionally uses only Python's standard library plus
``unitree_sdk2py``.  Run it with the Unitree control Python interpreter.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hmac
import json
import os
import queue
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from guide_skills import CATALOG, PROJECT, guide_assets, robot_status, stop_navigation
from guide_actions import action_catalog, needs_motion, validate_plan
from guide_task_executor import TaskExecutor
from guide_points import GuidePointStore, decode_board_image, validate_board_draft
from guide_relocalization import RelocalizationManager
from guide_board_processing import BoardDraftManager, run_board_omni
from guide_knowledge import KnowledgeStore, MAX_DOCUMENT_BYTES
from guide_conception import OBSERVATION_POLICY, conceive_point
from guide_conception_jobs import ConceptionJobs
from guide_initialization import InitializationManager
from guide_map import GuideMap
from guide_live_map import LiveMapManager

from unitree_sdk2py.core.channel import ChannelFactoryInitialize
from unitree_sdk2py.g1.audio.g1_audio_api import ROBOT_API_ID_AUDIO_TTS
from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient


from guide_web import CSS, SCRIPT, WEB_ROUTES, render_page


def json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


class UnitreeSpeechWorker(threading.Thread):
    def __init__(self, network_interface: str, volume: int):
        super().__init__(name="unitree-tts", daemon=True)
        self.network_interface = network_interface
        self.volume = volume
        self.items: queue.Queue[Any] = queue.Queue(maxsize=100)
        self.shutdown_event = threading.Event()
        self.ready = threading.Event()
        self.error: str | None = None
        self.client: AudioClient | None = None
        # The SDK version shipped on this PC2 has a bug in TtsMaker():
        # ``tts_index += tts_index`` keeps an initial index of zero forever.
        # The robot then treats later requests as duplicates.  Keep our own
        # monotonically increasing request index and call the registered API.
        self.tts_index = int(time.time() * 1000) & 0x7FFFFFFF

    def run(self) -> None:
        try:
            ChannelFactoryInitialize(0, self.network_interface)
            self.client = AudioClient()
            self.client.SetTimeout(10.0)
            self.client.Init()
            code = self.client.SetVolume(self.volume)
            if code != 0:
                raise RuntimeError(f"SetVolume returned {code}")
        except Exception as exc:  # pragma: no cover - hardware dependent
            self.error = str(exc)
            self.ready.set()
            return

        self.ready.set()
        while True:
            request = self.items.get()
            if request is None:
                return
            text = request["text"]
            try:
                if request["cancel"].is_set() or (request["external"] and request["external"].is_set()):
                    raise InterruptedError("播报已取消")
                self.tts_index = (self.tts_index + 1) & 0x7FFFFFFF
                parameter = json.dumps(
                    {"index": self.tts_index, "text": text, "speaker_id": 0},
                    ensure_ascii=False,
                )
                submitted_at = time.time()
                submitted_monotonic = time.monotonic()
                code, _ = self.client._Call(ROBOT_API_ID_AUDIO_TTS, parameter)
                accepted_at = time.time()
                accepted_monotonic = time.monotonic()
                if code != 0:
                    raise RuntimeError(f"TTS request failed: code={code}")
                print(f"[TTS] speaking: {text}", flush=True)
                # TtsMaker starts playback asynchronously.  Serialize sentences
                # to keep consecutive replies intelligible.
                duration = max(1.5, len(text) * 0.30 + 1.5)
                if self.shutdown_event.wait(duration):
                    raise InterruptedError("播报服务关闭")
                request["result"] = {"sdk_accepted": True,
                    "completion": "estimated_playback_wait_elapsed", "wait_seconds": duration,
                    "queue_wait_seconds": max(0.0, submitted_monotonic - request["queued_monotonic"]),
                    "sdk_call_seconds": max(0.0, accepted_monotonic - submitted_monotonic),
                    "tts_requested_at": submitted_at, "sdk_accepted_at": accepted_at}
            except Exception as exc:
                request["error"] = str(exc)
                print(f"[TTS] {exc}", file=sys.stderr, flush=True)
            finally:
                request["done"].set()

    def enqueue(self, text: str, cancel_event=None):
        if self.error:
            raise RuntimeError(self.error)
        request = {"text": text, "cancel": threading.Event(), "external": cancel_event,
            "done": threading.Event(), "error": None, "result": None,
            "queued_at": time.time(), "queued_monotonic": time.monotonic()}
        try:
            self.items.put_nowait(request)
        except queue.Full as exc:
            raise RuntimeError("播报队列已满") from exc
        return request

    def speak_and_wait(self, text, timeout, cancel_event):
        request = self.enqueue(text, cancel_event)
        deadline = time.monotonic() + timeout
        while not request["done"].wait(0.05):
            if cancel_event.is_set():
                request["cancel"].set()
                raise InterruptedError("任务播报已取消；已开始的声音可能继续播完")
            if time.monotonic() >= deadline:
                request["cancel"].set()
                raise TimeoutError("播报等待超时")
        if request["error"]:
            raise RuntimeError(request["error"])
        return request["result"]

    def stop(self) -> None:
        self.shutdown_event.set()
        self.discard_pending()
        try:
            self.items.put_nowait(None)
        except queue.Full:
            pass

    def discard_pending(self):
        while True:
            try:
                request = self.items.get_nowait()
            except queue.Empty:
                return
            if request is not None:
                request["error"] = "播报队列已取消"
                request["cancel"].set()
                request["done"].set()


class MobileGuideServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, speech: UnitreeSpeechWorker, dialogue_socket: str):
        super().__init__(address, handler)
        self.speech = speech
        self.dialogue_socket = dialogue_socket
        self.omni_python = os.environ.get(
            "OMNI_PYTHON", str(PROJECT / "run" / "omni-venv" / "bin" / "python")
        )
        self.motion_enabled = os.environ.get("GUIDE_MOTION_ENABLED") == "1"
        self.operator_pin = os.environ.get("GUIDE_OPERATOR_PIN", "")
        self.pending: dict[str, tuple[dict[str, Any], float]] = {}
        self.pending_presentations: dict[str, str] = {}
        self.pending_lock = threading.RLock()
        self.stop_generation = 0
        self.tasks = TaskExecutor(speech)
        self.points = GuidePointStore()
        self.conception_jobs = ConceptionJobs(
            context_factory=lambda point_id: self.knowledge_store().prepare_context(point_id),
            conceive=lambda point_id, topic, cancel: conceive_point(
                point_id=point_id, topic=topic, cancel=cancel, store=self.knowledge_store(),
                query=lambda request, timeout: run_board_omni(request, timeout, self.omni_python, cancel)))
        self.tasks.presentation_provider = self.conception_jobs.consume
        self.tasks.presentation_cancel = self.conception_jobs.cancel
        self.tasks.presentation_revalidate = self.conception_jobs.validate_consumed
        self.relocation = RelocalizationManager(self.points,
            stop=lambda: self.stop_actions(cancel_relocation=False), idle=lambda: not self.tasks.active())
        self.boards = BoardDraftManager(query=self.query_board)
        self.conception_cancel = threading.Event()
        self.map = GuideMap(project=PROJECT)
        self.live_map = LiveMapManager(project=PROJECT)
        self.initialization = InitializationManager(project=PROJECT,
            idle=lambda: not self.tasks.active() and not self.relocation.active())

    def check_operator_pin(self, pin):
        if self.operator_pin and (not isinstance(pin, str) or not hmac.compare_digest(pin, self.operator_pin)):
            raise ValueError("操作员 PIN 错误")

    def start_initialization(self, payload):
        if set(payload) - {"robot_ready", "pin"} or payload.get("robot_ready") is not True:
            raise ValueError("请先确认双脚落地、官方运动模式且已停稳；悬空或零力矩时不能初始化运控")
        self.check_operator_pin(payload.get("pin", ""))
        with self.pending_lock:
            if self.tasks.active() or self.relocation.active():
                raise RuntimeError("任务或重定位进行中，请结束后再初始化")
            state = self.initialization.start(confirmations={"grounded": True, "motion_mode": True, "stationary": True})
            self.pending.clear()
            self.pending_presentations.clear()
            self.conception_jobs.cancel_all()
            self.conception_cancel.set()
            self.stop_generation += 1
        return {"status": "success", "initialization": state,
                "message": "后台初始化已开始；会安全禁用运动，不自动重定位或行走"}

    def start_manual_relocation(self, payload):
        allowed = {"x", "y", "yaw", "map_fingerprint", "confirmed_position", "pin"}
        if set(payload) - allowed or payload.get("confirmed_position") is not True:
            raise ValueError("请确认地图箭头标记的是当前真实站位和朝向，不是导航目的地")
        self.check_operator_pin(payload.get("pin", ""))
        with self.pending_lock:
            if self.initialization.active() or self.tasks.active() or self.relocation.active():
                raise RuntimeError("初始化、任务或重定位进行中，请完成后再点选位置")
            if self.initialization.snapshot().get("state") != "succeeded":
                raise RuntimeError("请先完成一键初始化，确认软件服务和运动禁用状态")
            seed = self.map.validate_manual_seed({key: payload.get(key) for key in ("x", "y", "yaw", "map_fingerprint")})
            state = self.relocation.start_seed(seed)
            self.pending.clear()
            self.pending_presentations.clear()
            self.conception_jobs.cancel_all()
            self.conception_cancel.set()
            self.stop_generation += 1
        return {"status": "success", "relocation": state,
                "message": "已按当前地图站位请求重定位；不是导航目标，自动运动仍禁用"}

    def knowledge_store(self):
        # Derive from the actual point registry, including isolated test stores.
        directory = self.points.path.parent
        project = directory.parent if directory.name == "config" else directory
        return KnowledgeStore(project=project, points=self.points)

    def prepare_point_navigation(self, payload):
        """Select one registry target; preview only, without Omni or speech."""
        if set(payload) != {"point_id"}:
            raise ValueError("点位导航只接收已登记的 point_id，不接收坐标、速度或讲解参数")
        from navigate_to_point_safe import load_target
        with self.pending_lock:
            if self.tasks.active():
                raise RuntimeError("已有任务执行或停止中，请完成后再准备点位导航")
            point = load_target(self.points, payload["point_id"])
            prepared = self.prepare_plan({"title": "前往：" + point["name"], "steps": [
                {"action": "navigate_to_point", "parameters": {"point_id": point["id"]}, "timeout": 180}]})
        return {"status": "success", **prepared, "navigation_only": True,
                "message": "移动任务待确认；沿用当前提速档，不构思、不播报，尚未使能运动"}

    def prepare_agent(self, payload):
        if set(payload) - {"point_ids", "topic"}:
            raise ValueError("Agent 只接收已登记点位和讲解主题")
        identifiers, topic = payload.get("point_ids"), payload.get("topic", "")
        if not isinstance(identifiers, list) or not 1 <= len(identifiers) <= 2 or len(set(map(str, identifiers))) != len(identifiers):
            raise ValueError("单次请选择 1–2 个不同的导览点")
        if not isinstance(topic, str) or len(topic) > 200:
            raise ValueError("讲解主题不能超过 200 字")
        knowledge = self.knowledge_store()
        points = [knowledge.get(point_id)["point"] for point_id in identifiers]
        steps = [{"action": "check_status", "parameters": {"require_localized": True}, "timeout": 12}]
        for point in points:
            steps.extend([
                {"action": "navigate_to_point", "parameters": {"point_id": point["id"]}, "timeout": 180},
                {"action": "verify_arrival", "parameters": {"point_id": point["id"]}, "timeout": 20},
                {"action": "present_point", "parameters": {"point_id": point["id"], "topic": topic.strip()}, "timeout": 240},
            ])
        return {"status": "success", **self.prepare_plan({"title": " → ".join(p["name"] for p in points), "steps": steps})}

    def preview_conception(self, payload):
        if set(payload) - {"point_id", "topic"}:
            raise ValueError("讲解预览只接收已登记点位和主题")
        if not self.omni_ready():
            raise RuntimeError("Omni 尚未配置")
        with self.pending_lock:
            if self.tasks.active() or self.relocation.active() or self.initialization.active():
                raise RuntimeError("任务或重定位进行中，请完成后再预览讲解")
            cancel = threading.Event()
            self.conception_cancel = cancel
        result = conceive_point(point_id=payload.get("point_id"), topic=payload.get("topic", ""),
            store=self.knowledge_store(), cancel=cancel,
            query=lambda request, timeout: run_board_omni(request, timeout, self.omni_python, cancel))
        return {"status": "success", "conception": result,
                "message": "动态讲解已生成，仅预览；未核验实机到达，也未播报或运动"}

    def start_presentation_prefetch(self, payload):
        if set(payload) - {"point_id", "topic"}:
            raise ValueError("讲解预构思只接收已登记点位和主题，不接收客户端台词")
        if not self.omni_ready():
            raise RuntimeError("Omni 尚未配置")
        with self.pending_lock:
            if self.tasks.active() or self.relocation.active() or self.initialization.active():
                raise RuntimeError("初始化、任务或重定位进行中，请完成后再预构思讲解")
            preparation = self.conception_jobs.start(payload.get("point_id"), payload.get("topic", ""))
        return {"status": "success", "preparation": preparation,
                "message": "讲解已后台预构思；仅准备当前点资料，不播报、不运动"}

    def cancel_presentation_prefetch(self, payload):
        if set(payload) != {"id"}:
            raise ValueError("取消预构思只接收准备票据编号")
        ticket = payload["id"]
        if not isinstance(ticket, str) or len(ticket) != 32 or any(char not in "0123456789abcdef" for char in ticket):
            raise ValueError("准备票据编号无效")
        with self.pending_lock:
            cancelled = self.conception_jobs.cancel(ticket)
        return {"status": "success", "cancelled": cancelled}

    def add_document(self, point_id, payload):
        if set(payload) - {"filename", "file_base64", "reviewed", "pin"}:
            raise ValueError("文献上传参数无效")
        pin = payload.get("pin", "")
        if self.operator_pin and (not isinstance(pin, str) or not hmac.compare_digest(pin, self.operator_pin)):
            raise ValueError("操作员 PIN 错误")
        encoded = payload.get("file_base64")
        if not isinstance(encoded, str) or len(encoded) > (MAX_DOCUMENT_BYTES + 2) // 3 * 4:
            raise ValueError("单份文献不能超过 2 MB")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("文献编码无效") from exc
        document = self.knowledge_store().add_document(point_id, payload.get("filename"), raw,
                                                        reviewed=payload.get("reviewed", False))
        return {"status": "success", "document": document,
            "message": "PDF 已保存，等待后续解析；目前不会作为讲解事实" if document["status"] == "pending_extraction"
                else "文献已绑定当前展点；已审核正文可作为先验" if document["reviewed"] else "文献已保存为待审核资料"}

    def query_board(self, payload, timeout):
        if not self.omni_ready():
            raise RuntimeError("Omni 尚未配置：请在 PC2 配置 API Key 和运行环境")
        return run_board_omni(payload, timeout, self.omni_python, self.boards.cancel_event)

    def trigger_assistant(self, text: str) -> dict[str, Any]:
        if not os.path.exists(self.dialogue_socket):
            raise RuntimeError("导览助手尚未启动；请先测试“直接朗读”")
        request = json_bytes({"text": text, "force_wake": False})
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(12.0)
            client.connect(self.dialogue_socket)
            client.sendall(request)
            client.shutdown(socket.SHUT_WR)
            response = client.recv(8192)
        if not response:
            raise RuntimeError("导览助手没有返回结果")
        result = json.loads(response.decode("utf-8"))
        if result.get("status") != "success":
            raise RuntimeError(result.get("message", "导览助手处理失败"))
        return result

    def omni_ready(self) -> bool:
        return bool(os.environ.get("DASHSCOPE_API_KEY")) and os.path.isfile(self.omni_python)

    def ask_omni(self, payload: dict[str, Any], draft_only=False) -> dict[str, Any]:
        if not self.omni_ready():
            raise RuntimeError("Omni 尚未配置：请在 PC2 配置 API Key 和运行环境")
        command = [self.omni_python, str(PROJECT / "scripts" / "omni_client.py")]
        with self.pending_lock:
            generation = self.stop_generation
        environment = dict(os.environ)
        environment["GUIDE_GATEWAY_URL"] = "http://127.0.0.1:{}".format(self.server_address[1])
        payload = dict(payload)
        payload["mode"] = "board_draft" if draft_only else "assistant"
        try:
            process = subprocess.run(
                command, input=json.dumps(payload, ensure_ascii=False), text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=65, check=False, env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Omni 响应超时") from exc
        try:
            result = json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Omni 没有返回有效结果；请查看服务日志") from exc
        if process.returncode or result.get("status") != "success":
            diagnostic = [{"name": call.get("name"), "error": call.get("result", {}).get("error")}
                for call in result.get("tool_calls", [])]
            print("[Omni] request failed: {} tools={}".format(result.get("message"),
                json.dumps(diagnostic, ensure_ascii=False)), file=sys.stderr, flush=True)
            raise RuntimeError(result.get("message", "Omni 请求失败"))
        if draft_only:
            if result.get("actions") or result.get("tool_calls"):
                raise RuntimeError("展板整理不能调用机器人工具")
            return {"status": "success", "draft": validate_board_draft(result.get("text", "")),
                    "message": "导览词草稿已生成；请人工核对后保存，不会自动播报"}
        answer = str(result.get("text", ""))[:500]
        actions = result.get("actions", [])
        proposed = [a for a in actions if a.get("skill") == "request_action_plan"]
        routes = [a for a in actions if a.get("skill") == "request_route"]
        # A preview is not an execution request. Keep the spoken explanation
        # from occupying the TTS queue before the operator confirms the task.
        if answer and not (proposed or routes) and not self.tasks.active():
            self.speech.enqueue(answer)
        with self.pending_lock:
            if generation == self.stop_generation:
                if proposed:
                    result.update(self.prepare_plan(proposed[-1]["plan"]))
                elif routes:
                    result.update(self.prepare_plan(self.route_plan(routes[-1]["destination"])))
            elif actions:
                result["actions"] = []
                result["text"] += "\n任务请求已被停止操作取消。"
        result["message"] = "任务已规划，请检查步骤并确认或模拟" if result.get("confirmation") else "Omni 已回答"
        return result

    @staticmethod
    def route_plan(destination):
        if destination not in ("start", "end"):
            raise ValueError("路线方向只能是 start 或 end")
        return {"title": "回起点" if destination == "start" else "去终点",
            "steps": [{"action": "navigate_route", "parameters": {"destination": destination}}]}

    def prepare_plan(self, raw_plan, presentation_ticket=None):
        if self.initialization.active():
            raise RuntimeError("初始化进行中，请完成后再规划任务")
        if self.relocation.active():
            raise RuntimeError("重定位进行中，请完成后再规划任务")
        plan = validate_plan(raw_plan)
        token = uuid.uuid4().hex
        catalog = action_catalog()
        warnings = []
        if needs_motion(plan) and not self.motion_enabled:
            warnings.append("运动开关关闭；可预览或模拟")
        if any(step["action"] == "navigate_to_point" for step in plan["steps"]) and not catalog["named_navigation_verified"]:
            warnings.append("点位自主导航尚未完成现场验收；当前仅允许预览或模拟")
        for step in plan["steps"]:
            if step["action"] == "arm_gesture":
                gesture = step["parameters"]["gesture"]
                if not catalog["arm_enabled"] or not catalog["arm_gestures"][gesture]["verified"]:
                    warnings.append("手臂动作待实机验证：" + catalog["arm_gestures"][gesture]["label"])
        with self.pending_lock:
            if self.initialization.active():
                raise RuntimeError("初始化进行中，请完成后再规划任务")
            if self.relocation.active():
                raise RuntimeError("重定位进行中，请完成后再规划任务")
            if presentation_ticket is not None:
                if len(plan["steps"]) != 1 or plan["steps"][0]["action"] != "present_point":
                    raise ValueError("准备票据只能绑定单步当前点讲解任务")
                parameters = plan["steps"][0]["parameters"]
                self.conception_jobs.binding(presentation_ticket, parameters["point_id"], parameters["topic"])
            self.pending = {token: (plan, time.monotonic() + 90)}
            self.pending_presentations = {token: presentation_ticket} if presentation_ticket is not None else {}
        return {"confirmation": token, "plan": plan, "requires_pin": needs_motion(plan), "warnings": warnings}

    def prepare_route(self, destination: str) -> str:
        return self.prepare_plan(self.route_plan(destination))["confirmation"]

    def confirm_plan(self, token, pin="", dry_run=False):
        with self.pending_lock:
            if self.initialization.active():
                raise RuntimeError("初始化进行中，不能提交任务")
            if self.relocation.active():
                raise RuntimeError("重定位进行中，不能提交任务")
            pending = self.pending.get(token)
            if not pending or pending[1] < time.monotonic():
                raise ValueError("确认已过期，请重新规划")
            if not dry_run and needs_motion(pending[0]):
                if not self.motion_enabled or not self.operator_pin:
                    raise RuntimeError("网页运动控制未启用；请先配置运动开关和操作员 PIN")
                if not hmac.compare_digest(pin, self.operator_pin):
                    raise ValueError("操作员 PIN 错误")
                if (any(step["action"] == "navigate_to_point" for step in pending[0]["steps"]) and
                        not action_catalog()["named_navigation_verified"]):
                    raise RuntimeError("点位自主导航尚未实机验证；只能预览或模拟")
            presentation_ticket = self.pending_presentations.get(token)
            if presentation_ticket is not None:
                parameters = pending[0]["steps"][0]["parameters"]
                self.conception_jobs.binding(presentation_ticket, parameters["point_id"], parameters["topic"])
                task = self.tasks.start(pending[0], dry_run=dry_run, presentation_ticket=presentation_ticket)
            else:
                task = self.tasks.start(pending[0], dry_run=dry_run)
            self.pending.pop(token, None)
            self.pending_presentations.pop(token, None)
        return {"status": "success", "task": task,
            "message": "模拟任务已启动" if dry_run else "任务已提交；请查看逐步执行状态"}

    def confirm_route(self, token: str, pin: str) -> dict[str, Any]:
        return self.confirm_plan(token, pin)

    def stop_actions(self, cancel_relocation=True):
        with self.pending_lock:
            self.stop_generation += 1
            self.pending.clear()
            self.pending_presentations.clear()
            self.conception_jobs.cancel_all()
            self.conception_cancel.set()
            cancelled = self.tasks.cancel()
            cancelled = self.initialization.cancel() or cancelled
            if cancel_relocation:
                cancelled = self.relocation.cancel() or cancelled
        self.speech.discard_pending()
        stopped = stop_navigation()
        return {"status": "success" if stopped["stopped"] or cancelled else "error",
            "task_cancelled": cancelled, "stopped": stopped["stopped"], "message":
                ("任务取消已收到；" if cancelled else "") + stopped["message"]}

    def add_point(self, payload):
        payload = dict(payload)
        pin = payload.pop("pin", "")
        if self.operator_pin and (not isinstance(pin, str) or not hmac.compare_digest(pin, self.operator_pin)):
            raise ValueError("操作员 PIN 错误")
        if self.relocation.active():
            raise RuntimeError("重定位进行中，请完成后再记录点位")
        if self.initialization.active():
            raise RuntimeError("初始化进行中，请完成后再记录点位")
        if self.tasks.active():
            raise RuntimeError("导览任务执行中，请结束任务并停稳后记录点位")
        reviewed = payload.pop("board_reviewed", False)
        if "image_jpeg_base64" in payload and reviewed is not True:
            raise ValueError("请先核对展板图片与导览词，并勾选确认")
        point = self.points.add(payload)
        try:
            self.relocation.remember(point)
        except OSError as exc:
            print("[Relocation] unable to save last seed: {}".format(exc), file=sys.stderr, flush=True)
        return {"status": "success", "point": point,
                "message": "已保存“{}”的位置、朝向和讲解；机器人不会自动运动。".format(point["name"])}

    def read_point_pose(self):
        if self.initialization.active():
            raise RuntimeError("初始化进行中，请完成后再读取位置")
        if self.relocation.active():
            raise RuntimeError("重定位进行中，请完成后再读取位置")
        if self.tasks.active():
            raise RuntimeError("任务执行中，请结束任务并停稳后读取位置")
        return {"status": "success", **self.points.read_pose(),
                "message": "定位有效；已读取当前位置与朝向，不移动机器人"}

    def start_relocation(self, payload):
        if set(payload) - {"point_id", "confirmed_position", "pin"}:
            raise ValueError("重定位不接收任意坐标或命令，只能选择已确认位置")
        if payload.get("confirmed_position") is not True:
            raise ValueError("请先确认机器人在所选位置附近、朝向正确且已停稳")
        pin = payload.get("pin", "")
        if self.operator_pin and (not isinstance(pin, str) or not hmac.compare_digest(pin, self.operator_pin)):
            raise ValueError("操作员 PIN 错误")
        point_id = payload.get("point_id", "auto")
        if not isinstance(point_id, str):
            raise ValueError("导览点编号无效")
        with self.pending_lock:
            if self.initialization.active():
                raise RuntimeError("初始化进行中，请完成后再重定位")
            state = self.relocation.start(point_id)
            self.pending.clear()
            self.pending_presentations.clear()
            self.conception_jobs.cancel_all()
            self.conception_cancel.set()
            self.stop_generation += 1
        return {"status": "success", "relocation": state, "message": "重定位已请求；自动运动不会启用"}

    def template(self, name):
        templates = {
            "speech_demo": {"title": "讲解任务演示", "steps": [
                {"action": "speak", "parameters": {"text": "欢迎使用 DaoLan 导览助手。下面演示标准动作任务的顺序执行。"}},
                {"action": "wait", "parameters": {"seconds": 2}},
                {"action": "speak", "parameters": {"text": "讲解演示已完成，您可以在网页查看每一步的执行结果。"}},
            ]},
            "round_trip": {"title": "往返导览", "steps": [
                {"action": "check_status", "parameters": {"require_localized": True}},
                {"action": "speak", "parameters": {"text": "欢迎参加导览，我们将沿已录制的路线前往终点。"}},
                {"action": "navigate_route", "parameters": {"destination": "end"}},
                {"action": "speak", "parameters": {"text": "我们已到达路线终点。此处讲解内容可根据场地资料配置。"}},
                {"action": "wait", "parameters": {"seconds": 3}},
                {"action": "navigate_route", "parameters": {"destination": "start"}},
                {"action": "speak", "parameters": {"text": "我们已返回起点，本次导览结束。"}},
            ]},
            "wave_preview": {"title": "挥手动作预览", "steps": [
                {"action": "arm_gesture", "parameters": {"gesture": "wave", "hold_seconds": 3}},
            ]},
        }
        if name not in templates:
            raise ValueError("未知任务模板")
        return self.prepare_plan(templates[name])

    def shutdown(self):
        # Prepared evidence never survives a server lifecycle boundary.
        self.conception_jobs.cancel_all()
        super().shutdown()

    def server_close(self):
        # HTTPServer also calls server_close if its constructor cannot bind.
        jobs = getattr(self, "conception_jobs", None)
        if jobs is not None:
            jobs.cancel_all()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    server: MobileGuideServer

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[HTTP] {self.address_string()} {fmt % args}", flush=True)

    def send_payload(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        assets = {"/assets/guide.css": (CSS, "text/css; charset=utf-8"),
                  "/assets/guide.js": (SCRIPT, "application/javascript; charset=utf-8")}
        if self.path in assets:
            content, mime_type = assets[self.path]
            body = content.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/init":
            self.send_payload(HTTPStatus.OK, {"status": "success", "initialization": self.server.initialization.snapshot(),
                                             "requires_pin": bool(self.server.operator_pin)})
            return
        if self.path == "/api/map/live":
            try:
                self.send_payload(HTTPStatus.OK, {"status": "success", "live": self.server.live_map.snapshot()})
            except Exception as exc:
                self.send_payload(HTTPStatus.SERVICE_UNAVAILABLE, {"status": "error", "message": str(exc)})
            return
        if self.path == "/api/map":
            try:
                self.send_payload(HTTPStatus.OK, {"status": "success", "map": self.server.map.metadata()})
            except Exception as exc:
                self.send_payload(HTTPStatus.SERVICE_UNAVAILABLE, {"status": "error", "message": str(exc)})
            return
        if self.path.split("?", 1)[0] == "/api/map/image":
            try:
                body = self.server.map.image_png()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:
                self.send_payload(HTTPStatus.SERVICE_UNAVAILABLE, {"status": "error", "message": str(exc)})
            return
        if self.path == "/api/agent/points":
            try:
                self.send_payload(HTTPStatus.OK, {"status": "success", "points": self.server.knowledge_store().list_points(),
                    "observation_policy": OBSERVATION_POLICY, "named_navigation_verified": action_catalog()["named_navigation_verified"]})
            except Exception as exc:
                self.send_payload(HTTPStatus.SERVICE_UNAVAILABLE, {"status": "error", "message": str(exc)})
            return
        parts = self.path.split("/")
        if len(parts) == 5 and parts[1:3] == ["api", "points"] and parts[4] == "knowledge":
            try:
                self.send_payload(HTTPStatus.OK, {"status": "success", "knowledge": self.server.knowledge_store().get(parts[3])})
            except (ValueError, OSError) as exc:
                self.send_payload(HTTPStatus.NOT_FOUND, {"status": "error", "message": str(exc)})
            except Exception as exc:
                self.send_payload(HTTPStatus.SERVICE_UNAVAILABLE, {"status": "error", "message": str(exc)})
            return
        if len(parts) == 6 and parts[1:3] == ["api", "points"] and parts[4] == "documents":
            try:
                path = self.server.knowledge_store().document_path(parts[3], parts[5])
                body = path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/pdf" if path.suffix == ".pdf" else "text/plain; charset=utf-8")
                self.send_header("Content-Disposition", 'attachment; filename="{}"'.format(path.name))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (ValueError, OSError) as exc:
                self.send_payload(HTTPStatus.NOT_FOUND, {"status": "error", "message": str(exc)})
            except Exception as exc:
                self.send_payload(HTTPStatus.SERVICE_UNAVAILABLE, {"status": "error", "message": str(exc)})
            return
        if self.path.startswith("/api/points/draft/"):
            try:
                job_id = self.path[len("/api/points/draft/"):]
                self.send_payload(HTTPStatus.OK, {"status": "success", "job": self.server.boards.snapshot(job_id)})
            except ValueError as exc:
                self.send_payload(HTTPStatus.NOT_FOUND, {"status": "error", "message": str(exc)})
            return
        if self.path == "/api/points/pose":
            try:
                self.send_payload(HTTPStatus.OK, self.server.read_point_pose())
            except Exception as exc:
                self.send_payload(HTTPStatus.SERVICE_UNAVAILABLE, {"status": "error", "message": str(exc)})
            return
        if self.path == "/api/relocation":
            try:
                seeds = []
                try:
                    seeds.append({"id": "auto", **self.server.relocation.choose_seed()})
                except ValueError:
                    pass
                for point in self.server.points.list():
                    seeds.append({"id": point["id"], "name": point["name"], "pose": point["pose"]})
                self.send_payload(HTTPStatus.OK, {"status": "success", "seeds": seeds,
                    "relocation": self.server.relocation.snapshot(), "requires_pin": bool(self.server.operator_pin)})
            except Exception as exc:
                self.send_payload(HTTPStatus.SERVICE_UNAVAILABLE, {"status": "error", "message": str(exc)})
            return
        if self.path == "/api/points":
            try:
                self.send_payload(HTTPStatus.OK, {"status": "success", "points": self.server.points.list(),
                                                 "requires_pin": bool(self.server.operator_pin)})
            except Exception as exc:
                self.send_payload(HTTPStatus.SERVICE_UNAVAILABLE, {"status": "error", "message": str(exc)})
            return
        if self.path.startswith("/api/points/") and self.path.endswith("/image"):
            try:
                point_id = self.path[len("/api/points/"):-len("/image")]
                body = self.server.points.image_path(point_id).read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (ValueError, OSError) as exc:
                self.send_payload(HTTPStatus.NOT_FOUND, {"status": "error", "message": str(exc)})
            return
        if self.path in WEB_ROUTES:
            body = render_page(self.path).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/health":
            assistant = os.path.exists(self.server.dialogue_socket)
            omni = self.server.omni_ready()
            message = "网页与机器人扬声器已连接"
            message += "；Omni 已配置" if omni else "；Omni 未配置"
            self.send_payload(HTTPStatus.OK, {"status": "success", "assistant": assistant,
                "omni": omni, "motion_enabled": self.server.motion_enabled,
                "message": message})
            return
        if self.path == "/api/skills":
            self.send_payload(HTTPStatus.OK, {"status": "success", "skills": CATALOG})
            return
        if self.path == "/api/actions":
            self.send_payload(HTTPStatus.OK, {"status": "success", **action_catalog()})
            return
        if self.path == "/api/tasks/current":
            self.send_payload(HTTPStatus.OK, {"status": "success", "task": self.server.tasks.snapshot()})
            return
        if self.path == "/api/assets":
            self.send_payload(HTTPStatus.OK, {"status": "success", "assets": guide_assets()})
            return
        if self.path == "/api/status":
            self.send_payload(HTTPStatus.OK, {"status": "success", "robot": robot_status()})
            return
        self.send_payload(HTTPStatus.NOT_FOUND, {"status": "error", "message": "Not found"})

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            parts = self.path.split("/")
            document_upload = len(parts) == 5 and parts[1:3] == ["api", "points"] and parts[4] == "documents"
            if length <= 0 or length > (2900000 if document_upload else 1800000):
                raise ValueError("请求内容为空或过大")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("请求必须是 JSON 对象")
            text = str(payload.get("text", "")).strip()
            if len(text) > 500:
                raise ValueError("单次内容不能超过 500 个字符")

            if self.path == "/api/init/start":
                result = self.server.start_initialization(payload)
            elif self.path == "/api/relocation/manual":
                result = self.server.start_manual_relocation(payload)
            elif self.path == "/api/agent/prepare":
                result = self.server.prepare_agent(payload)
            elif self.path == "/api/navigation/prepare":
                result = self.server.prepare_point_navigation(payload)
            elif self.path == "/api/agent/conceive":
                result = self.server.preview_conception(payload)
            elif self.path == "/api/agent/prefetch":
                result = self.server.start_presentation_prefetch(payload)
            elif self.path == "/api/agent/prefetch/cancel":
                result = self.server.cancel_presentation_prefetch(payload)
            elif document_upload:
                result = self.server.add_document(parts[3], payload)
            elif self.path == "/api/relocation/start":
                result = self.server.start_relocation(payload)
            elif self.path == "/api/points":
                result = self.server.add_point(payload)
            elif self.path == "/api/points/draft":
                if set(payload) != {"image_jpeg_base64"}:
                    raise ValueError("展板整理只接收照片")
                decode_board_image(payload["image_jpeg_base64"])
                if not self.server.omni_ready():
                    raise RuntimeError("Omni 尚未配置：请在 PC2 配置 API Key 和运行环境")
                result = {"status": "success", "job": self.server.boards.start(payload["image_jpeg_base64"]),
                    "message": "展板后台处理已开始，不会播报或移动"}
            elif self.path == "/api/speak":
                if not text:
                    raise ValueError("文字内容不能为空")
                if self.server.tasks.active():
                    raise RuntimeError("任务执行中，请在任务中安排播报或先停止任务")
                self.server.speech.enqueue(text)
                result = {"status": "success", "message": "已加入机器人播报队列"}
            elif self.path == "/api/assistant":
                if not text:
                    raise ValueError("文字内容不能为空")
                self.server.trigger_assistant(text)
                result = {"status": "success", "message": "已发送给导览助手"}
            elif self.path in ("/api/omni", "/api/intent"):
                result = self.server.ask_omni(payload)
            elif self.path == "/api/plans/prepare":
                if set(payload) - {"plan", "presentation_ticket"}:
                    raise ValueError("规划参数无效")
                if "presentation_ticket" in payload:
                    if not isinstance(payload["presentation_ticket"], str):
                        raise ValueError("准备票据编号无效")
                    result = {"status": "success", **self.server.prepare_plan(
                        payload.get("plan"), presentation_ticket=payload["presentation_ticket"])}
                else:
                    result = {"status": "success", **self.server.prepare_plan(payload.get("plan"))}
            elif self.path == "/api/plans/template":
                result = {"status": "success", **self.server.template(payload.get("name"))}
            elif self.path in ("/api/plans/confirm", "/api/plans/dry-run"):
                result = self.server.confirm_plan(str(payload.get("token", "")),
                    str(payload.get("pin", "")), dry_run=self.path.endswith("dry-run"))
            elif self.path == "/api/route/prepare":
                token = self.server.prepare_route(payload.get("destination", ""))
                result = {"status": "success", "token": token, "message": "路线待确认；机器人尚未运动"}
            elif self.path == "/api/route/confirm":
                result = self.server.confirm_route(str(payload.get("token", "")), str(payload.get("pin", "")))
            elif self.path == "/api/stop":
                result = self.server.stop_actions()
            else:
                self.send_payload(HTTPStatus.NOT_FOUND, {"status": "error", "message": "Not found"})
                return
            self.send_payload(HTTPStatus.OK, result)
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_payload(HTTPStatus.BAD_REQUEST, {"status": "error", "message": str(exc)})
        except Exception as exc:
            self.send_payload(HTTPStatus.SERVICE_UNAVAILABLE, {"status": "error", "message": str(exc)})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mobile frontend and Unitree TTS bridge")
    parser.add_argument("--interface", default=os.environ.get("CONTROL_IFACE", "eth0"))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--volume", type=int, default=100)
    parser.add_argument("--dialogue-socket", default="/tmp/dialogue_trigger.sock")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    speech = UnitreeSpeechWorker(args.interface, max(0, min(100, args.volume)))
    speech.start()
    if not speech.ready.wait(timeout=15):
        print("Unitree audio initialization timed out", file=sys.stderr)
        return 1
    if speech.error:
        print(f"Unitree audio initialization failed: {speech.error}", file=sys.stderr)
        return 1

    server = MobileGuideServer((args.host, args.port), Handler, speech, args.dialogue_socket)

    def shutdown(_signum, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    print(f"Mobile guide ready: http://{args.host}:{args.port} (DDS={args.interface})", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.live_map.close()
        server.initialization.cancel()
        server.conception_cancel.set()
        server.conception_jobs.cancel_all()
        server.boards.close()
        server.relocation.cancel()
        if server.relocation.thread:
            server.relocation.thread.join(timeout=3)
        task = server.tasks.snapshot()
        if server.tasks.cancel() and task and not task["dry_run"] and needs_motion(task["plan"]):
            try:
                stop_navigation()
            except Exception as exc:
                print(f"[Task] shutdown stop failed: {exc}", file=sys.stderr, flush=True)
        server.server_close()
        speech.stop()
        if server.tasks.thread:
            server.tasks.thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
