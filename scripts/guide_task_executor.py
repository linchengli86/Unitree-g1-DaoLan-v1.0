#!/usr/bin/env python3
"""Sequential, cancellable execution of the standard DaoLan action contract."""

import copy
import json
import math
import os
import re
import shlex
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

from guide_actions import check_execution_policy, needs_motion, validate_plan, verified_arms
from guide_skills import PROJECT, ROUTE_SCRIPT, guide_assets, robot_status, route_running, stop_navigation


TERMINAL = {"succeeded", "failed", "cancelled", "simulated", "interrupted"}


class TaskExecutor:
    def __init__(self, speech, project=PROJECT, status=robot_status,
                 assets=guide_assets, stop=stop_navigation, conception=None, verify=None):
        self.speech = speech
        self.project = Path(project)
        self.status_callback, self.assets_callback, self.stop_callback = status, assets, stop
        self.conception_callback, self.verify_callback = conception, verify
        self.presentation_provider = None
        self.presentation_cancel = None
        self.presentation_revalidate = None
        self.lock = threading.RLock()
        self.cancel_event = threading.Event()
        self.child = None
        self.current = None
        self.thread = None
        self.directory = self.project / "run" / "tasks"
        self.directory.mkdir(parents=True, exist_ok=True)
        latest = self.directory / "current.json"
        if latest.is_file():
            try:
                self.current = json.loads(latest.read_text())
                if self.current.get("state") not in TERMINAL:
                    self.current["state"] = "interrupted"
                    self.current["message"] = "服务已重启，旧任务不会自动恢复"
                    self._persist()
            except (OSError, ValueError):
                self.current = None

    def _persist(self):
        payload = json.dumps(self.current, ensure_ascii=False, indent=2)
        for target in (self.directory / "current.json", self.directory / (self.current["id"] + ".json")):
            temporary = target.with_suffix(".tmp")
            temporary.write_text(payload)
            os.replace(str(temporary), str(target))

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.current)

    def active(self):
        with self.lock:
            return bool(self.thread and self.thread.is_alive())

    def _preflight(self, plan):
        check_execution_policy(plan, os.environ.get("GUIDE_MOTION_ENABLED") == "1",
            os.environ.get("GUIDE_ARM_ENABLED") == "1", verified_arms())
        if not needs_motion(plan):
            return
        state = self.status_callback()
        self._check_cancel()
        if not state.get("safe_controller"):
            raise RuntimeError("安全控制器未就绪")
        if any(s["action"] == "navigate_route" for s in plan["steps"]):
            if not self.assets_callback().get("route"):
                raise RuntimeError("缺少已录制路线")
            if not all(state.get(k) for k in ("localizer", "planner", "localized")):
                raise RuntimeError("定位或规划器未就绪")
        if any(s["action"] == "navigate_to_point" for s in plan["steps"]):
            assets = self.assets_callback()
            if not all(assets.get(k) for k in ("map_2d", "map_3d")):
                raise RuntimeError("缺少点位导航所需的二维或三维地图")
            if not all(state.get(k) for k in ("localizer", "planner", "localized")):
                raise RuntimeError("定位或规划器未就绪")
        if state.get("route_running") or route_running():
            raise RuntimeError("已有路线正在运行")

    def start(self, raw_plan, dry_run=False, presentation_ticket=None):
        plan = validate_plan(raw_plan)
        if presentation_ticket is not None:
            if not isinstance(presentation_ticket, str) or not re.fullmatch(r"[0-9a-f]{32}", presentation_ticket):
                raise ValueError("预构思票据格式无效")
            if len(plan["steps"]) != 1 or plan["steps"][0]["action"] != "present_point":
                raise ValueError("预构思票据只能绑定一个到点讲解动作")
            if self.presentation_provider is None:
                raise RuntimeError("预构思服务未接入，不能执行带票据的讲解")
        # Reserve the task atomically; slow ROS preflight happens in the worker
        # so the stop endpoint can cancel it before any child starts.
        with self.lock:
            if self.active():
                raise RuntimeError("已有任务正在执行或停止中")
            if not dry_run:
                check_execution_policy(plan, os.environ.get("GUIDE_MOTION_ENABLED") == "1",
                    os.environ.get("GUIDE_ARM_ENABLED") == "1", verified_arms())
            self.cancel_event = threading.Event()
            self.current = {"id": uuid.uuid4().hex, "state": "running", "dry_run": dry_run,
                "plan": plan, "started_at": time.time(), "step_index": 0,
                "message": "模拟执行中" if dry_run else "执行中",
                "steps": [{"state": "pending", "action": s["action"]} for s in plan["steps"]]}
            if presentation_ticket is not None:
                self.current["presentation_ticket"] = presentation_ticket
            self._persist()
            self.thread = threading.Thread(target=self._run, name="guide-task", daemon=True)
            self.thread.start()
            return self.snapshot()

    def _check_cancel(self):
        if self.cancel_event.is_set():
            raise InterruptedError("任务已取消")

    @staticmethod
    def _terminate(process):
        if not process or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGINT)
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)
        except ProcessLookupError:
            pass

    def cancel(self):
        with self.lock:
            active = self.active()
            self.cancel_event.set()
            child = self.child
        self._terminate(child)
        return active

    def _process(self, command, timeout, route=False, json_result=False):
        logfile = self.directory / (self.current["id"] + ".log")
        with self.lock:
            self._check_cancel()
            start_offset = logfile.stat().st_size if logfile.exists() else 0
            with logfile.open("ab") as output:
                process = subprocess.Popen(command, cwd=str(self.project),
                    stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                    start_new_session=True)
            self.child = process
            pidfile = self.project / "run" / "pids" / "guide_route.pid"
            if route:
                try:
                    pidfile.parent.mkdir(parents=True, exist_ok=True)
                    pidfile.write_text(str(process.pid))
                except OSError:
                    # Never leave an untracked motion child running if the
                    # route ownership record cannot be published.
                    self._terminate(process)
                    self.child = None
                    raise
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                self._check_cancel()
                if time.monotonic() >= deadline:
                    raise TimeoutError("动作超时")
                self.cancel_event.wait(0.1)
            self._check_cancel()
            packet = None
            if json_result:
                # Only this command's bounded log tail may provide its result;
                # earlier action JSON in the shared task log is never reused.
                with logfile.open("rb") as output:
                    output.seek(max(start_offset, logfile.stat().st_size - 65536))
                    lines = output.read(65536).decode("utf-8", errors="replace").splitlines()
                for line in reversed(lines):
                    try:
                        candidate = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(candidate, dict) and "status" in candidate:
                        packet = candidate
                        break
            if process.returncode != 0:
                raise RuntimeError((packet or {}).get("error") or (packet or {}).get("message") or
                    "动作进程失败 (code={})，请查看任务日志".format(process.returncode))
            if json_result:
                if not packet or packet.get("status") not in ("success", "succeeded"):
                    raise RuntimeError((packet or {}).get("error") or (packet or {}).get("message") or
                        "动作进程未返回有效成功结果，请查看任务日志")
                return {**packet, "exit_code": 0, "log": logfile.name}
            return {"exit_code": 0, "log": logfile.name}
        finally:
            self._terminate(process)
            with self.lock:
                self.child = None
                if route and pidfile.exists() and pidfile.read_text().strip() == str(process.pid):
                    pidfile.unlink()

    def _point_command(self, point_id, timeout, verify_only=False):
        helper = self.project / "scripts" / "navigate_to_point_safe.py"
        command = ["/usr/bin/python3", "-u", str(helper), "--point-id", point_id,
            "--timeout", str(min(20 if verify_only else 240, timeout))]
        if verify_only:
            command.append("--verify-only")
        setup = "source /opt/ros/noetic/setup.bash && source " + shlex.quote(str(
            self.project / "G1Nav2D" / "devel" / "setup.bash"))
        return ["bash", "-lc", setup + " && exec " + " ".join(shlex.quote(arg) for arg in command)]

    def _verify_arrival(self, point_id, timeout):
        self._check_cancel()
        if timeout <= 0:
            raise TimeoutError("到达核验超时")
        started = time.monotonic()
        timeout = min(timeout, 20)
        if self.verify_callback:
            result = self.verify_callback(point_id=point_id, timeout=timeout, cancel=self.cancel_event)
        else:
            result = self._process(self._point_command(point_id, timeout, verify_only=True),
                timeout, json_result=True)
        self._check_cancel()
        if time.monotonic() - started > timeout:
            raise TimeoutError("到达核验超时")
        if not isinstance(result, dict) or result.get("verified") is not True:
            raise RuntimeError("未确认机器人在该导览点停稳且位置、朝向和定位有效")
        if result.get("point_id", point_id) != point_id:
            raise RuntimeError("到达核验返回了错误的导览点")
        return result

    @staticmethod
    def _validate_point_result(result, point_id):
        if (not isinstance(result, dict) or result.get("status") != "success" or
                result.get("point_id") != point_id or
                any(result.get(key) is not True for key in ("verified", "arrived", "motion_disabled"))):
            raise RuntimeError("点位导航未确认本目标到达核验及运动禁用，请检查任务日志")
        for key in ("position_error_m", "yaw_error_rad"):
            value = result.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 0.20:
                raise RuntimeError("点位导航到达误差无效或超过验收容差，请检查任务日志")
        return result

    def _present_point(self, args, deadline):
        point_id = args["point_id"]
        started = time.monotonic()
        arrival = self._verify_arrival(point_id, deadline - time.monotonic())
        first_verified = time.monotonic()
        self._check_cancel()
        if time.monotonic() >= deadline:
            raise TimeoutError("讲解构思前超时")
        ticket = self.current.get("presentation_ticket")
        if ticket is not None:
            conceived = self.presentation_provider(ticket=ticket, point_id=point_id,
                topic=args["topic"], cancel=self.cancel_event,
                timeout=min(35, deadline - time.monotonic()))
        elif self.conception_callback:
            conceived = self.conception_callback(point_id=point_id, topic=args["topic"], cancel=self.cancel_event)
        else:
            from guide_conception import conceive_point
            conceived = conceive_point(project=self.project, point_id=point_id,
                topic=args["topic"], cancel=self.cancel_event)
        self._check_cancel()
        conception_finished = time.monotonic()
        if time.monotonic() >= deadline:
            raise TimeoutError("讲解构思超时")
        if not isinstance(conceived, dict) or conceived.get("needs_review") is not False:
            raise RuntimeError("讲解信息尚有疑点或需要审核，未播报")
        if ticket is not None and (conceived.get("status") != "success" or conceived.get("point_id") != point_id):
            raise RuntimeError("预构思结果绑定了其他导览点，未播报")
        segments, source_ids = conceived.get("segments"), conceived.get("source_ids")
        if not isinstance(segments, list) or not 1 <= len(segments) <= 4 or any(
                not isinstance(text, str) or not 1 <= len(text.strip()) <= 150 for text in segments):
            raise RuntimeError("动态讲解必须是 1–4 段、每段 1–150 字，未播报")
        if sum(len(text.strip()) for text in segments) > 450:
            raise RuntimeError("动态讲解总长度不能超过 450 字，未播报")
        if not isinstance(source_ids, list) or not 1 <= len(source_ids) <= 32 or any(
                not isinstance(source, str) or not 1 <= len(source) <= 128 for source in source_ids):
            raise RuntimeError("动态讲解缺少有效证据来源，未播报")
        # Conception may take time. Re-check physical arrival before any audio,
        # so manual movement while the model is composing cannot go unnoticed.
        arrival = self._verify_arrival(point_id, deadline - time.monotonic())
        final_verified = time.monotonic()
        segments = [text.strip() for text in segments]
        if sum(len(text) * 0.3 + 1.5 for text in segments) + 12 > deadline - time.monotonic():
            raise TimeoutError("剩余时间不足以完成动态讲解，未播报")
        playback = []
        for text in segments:
            self._check_cancel()
            if ticket is not None:
                if self.presentation_revalidate is None:
                    raise RuntimeError("预构思资料复核未接入，未播报")
                self.presentation_revalidate(ticket=ticket, point_id=point_id, topic=args["topic"])
                self._check_cancel()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("动态讲解播报超时")
            playback.append(self.speech.speak_and_wait(text, remaining, self.cancel_event))
        return {"completion": "conceived_and_presented", "point_id": point_id,
            "arrival": arrival, "segments": segments, "source_ids": source_ids,
            "playback": playback, "prefetched": ticket is not None,
            "timings": {"first_arrival_check_seconds": first_verified - started,
                "conception_wait_seconds": conception_finished - first_verified,
                "final_arrival_check_seconds": final_verified - conception_finished,
                "before_audio_queue_seconds": final_verified - started}}

    def _step(self, step):
        self._check_cancel()
        name, args, timeout = step["action"], step["parameters"], step["timeout"]
        started = time.monotonic()
        if name == "check_status":
            state = self.status_callback()
            self._check_cancel()
            if args["require_localized"] and not state.get("localized"):
                raise RuntimeError("重定位无效")
            result = state
        elif name == "speak":
            result = self.speech.speak_and_wait(args["text"], timeout, self.cancel_event)
        elif name == "wait":
            if self.cancel_event.wait(args["seconds"]):
                self._check_cancel()
            result = {"elapsed_seconds": args["seconds"]}
        elif name == "navigate_route":
            self._preflight({"steps": [step]})
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("路线预检超时")
            result = self._process(["bash", str(ROUTE_SCRIPT), args["destination"]], remaining, route=True)
            result["completion"] = "route_succeeded"
        elif name == "navigate_to_point":
            self._preflight({"steps": [step]})
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("点位导航预检超时")
            result = self._process(self._point_command(args["point_id"], remaining),
                remaining, route=True, json_result=True)
            self._validate_point_result(result, args["point_id"])
            result["completion"] = "point_succeeded"
        elif name == "verify_arrival":
            result = self._verify_arrival(args["point_id"], timeout)
        elif name == "present_point":
            result = self._present_point(args, started + timeout)
        elif name == "arm_gesture":
            self._preflight({"steps": [step]})
            stopped = self.stop_callback()
            self._check_cancel()
            if not stopped.get("stopped"):
                raise RuntimeError("手臂动作前未确认底盘停止")
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("手臂动作预检超时")
            result = self._process([
                os.environ.get("CONTROL_PYTHON", "python3"),
                str(self.project / "scripts" / "guide_arm_action.py"), args["gesture"],
                "--hold", str(args["hold_seconds"]),
            ], remaining)
            result["completion"] = "accepted_and_timed_hold"
        else:
            result = self.stop_callback()
            if not result.get("stopped"):
                raise RuntimeError(result.get("message", "停止未确认"))
        self._check_cancel()
        if time.monotonic() - started > timeout:
            raise TimeoutError("动作超时")
        return result

    def _run(self):
        final_state, message = "succeeded", "全部动作完成"
        index = 0
        try:
            if not self.current["dry_run"]:
                self._preflight(self.current["plan"])
            for index, step in enumerate(self.current["plan"]["steps"]):
                self._check_cancel()
                with self.lock:
                    self.current["step_index"] = index
                    self.current["steps"][index].update(state="running", started_at=time.time())
                    self._persist()
                if self.current["dry_run"]:
                    self.cancel_event.wait(0.02)
                    self._check_cancel()
                    result = {"simulated": True, "parameters": step["parameters"]}
                else:
                    result = self._step(step)
                with self.lock:
                    self._check_cancel()
                    self.current["steps"][index].update(
                        state="simulated" if self.current["dry_run"] else "succeeded",
                        finished_at=time.time(), result=result)
                    self._persist()
            if self.current["dry_run"]:
                final_state, message = "simulated", "模拟完成，未调用机器人接口"
        except InterruptedError as exc:
            final_state, message = "cancelled", str(exc)
        except Exception as exc:
            final_state, message = "failed", str(exc)
        finally:
            ticket = self.current.get("presentation_ticket")
            if ticket is not None and not self.current["dry_run"] and self.presentation_cancel:
                try:
                    self.presentation_cancel(ticket)
                except Exception:
                    pass  # Calculation is also cancelled on server stop/expiry.
            if not self.current["dry_run"] and needs_motion(self.current["plan"]):
                try:
                    stop = self.stop_callback()
                    if not stop.get("stopped") and final_state == "succeeded":
                        final_state, message = "failed", "任务结束后未收到停止确认"
                except Exception as exc:
                    if final_state == "succeeded":
                        final_state, message = "failed", str(exc)
            with self.lock:
                if self.cancel_event.is_set():
                    final_state, message = "cancelled", "任务已取消"
                if self.current["steps"][index]["state"] == "running":
                    self.current["steps"][index].update(state=final_state, message=message)
                for pending in self.current["steps"]:
                    if pending["state"] == "pending":
                        pending["state"] = "skipped"
                self.current.update(state=final_state, message=message, finished_at=time.time())
                self._persist()
