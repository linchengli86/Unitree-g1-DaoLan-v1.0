#!/usr/bin/env python3
"""Background PC2 service initialization; never arms or navigates the robot.

The operator must first put the robot on the ground, in the official movement
mode, and at rest. This initializes software/drivers, not hardware power or the
official locomotion mode. An unlocalized robot is a successful software setup
that still requires a manually chosen map pose.
"""

import copy
import json
import math
import os
import selectors
import shlex
import signal
import subprocess
import threading
import time
from pathlib import Path

from guide_points import PROJECT


class InitializationManager:
    def __init__(self, project=PROJECT, runner=None, idle=lambda: True):
        self.project = Path(project)
        self.runner = runner or self._run_services
        self.idle = idle
        self.lock = threading.RLock()
        self.cancel_event = threading.Event()
        self.thread = None
        self.state = {"state": "idle", "message": "尚未初始化；先落地、进入官方运动模式并停稳",
                      "stages": [], "needs_initial_pose": True, "automatic_motion_enabled": False}

    def active(self):
        with self.lock:
            return self.state["state"] == "running"

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.state)

    def start(self, confirmations):
        if not isinstance(confirmations, dict) or set(confirmations) != {"grounded", "motion_mode", "stationary"} or any(
            value is not True for value in confirmations.values()
        ):
            raise ValueError("请确认双脚落地站稳、已进入官方运动模式，且机器人原地无任务")
        with self.lock:
            if self.state["state"] == "running":
                raise RuntimeError("初始化正在进行，请勿重复点击")
            if not self.idle():
                raise RuntimeError("任务或重定位尚未结束，不能初始化")
            self.cancel_event.clear()
            self.state = {"state": "running", "message": "正在检查网络和资料，并准备禁用运动的软件服务",
                          "stages": [], "started_at": time.time(), "needs_initial_pose": True,
                          "automatic_motion_enabled": False}
            self.thread = threading.Thread(target=self._worker, name="robot-initialization", daemon=True)
            self.thread.start()
            return copy.deepcopy(self.state)

    def cancel(self):
        with self.lock:
            active = self.state["state"] == "running"
            if active:
                self.cancel_event.set()
            return active

    def _report(self, stage_id, state, message, details=None):
        if not isinstance(stage_id, str) or not stage_id or len(stage_id) > 40 or state not in ("running", "succeeded", "failed", "skipped"):
            raise ValueError("初始化阶段报告无效")
        if not isinstance(message, str) or len(message) > 600:
            raise ValueError("初始化阶段说明无效")
        # Ensure reports are serializable and not mutable aliases owned by a runner.
        event = json.loads(json.dumps({"id": stage_id, "state": state, "message": message,
                                      "details": details or {}}, ensure_ascii=False, allow_nan=False))
        with self.lock:
            current = next((stage for stage in self.state["stages"] if stage["id"] == stage_id), None)
            if current is None:
                self.state["stages"].append(event)
            else:
                current.update(event)
            self.state["message"] = message

    def _worker(self):
        final, message = "failed", "初始化失败；不会启用自动运动"
        result = None
        try:
            if self.cancel_event.is_set():
                raise InterruptedError("初始化已取消")
            if not self.idle():
                raise RuntimeError("初始化开始前出现其他任务，请停止任务后重试")
            result = self.runner(self.cancel_event, self._report)
            if self.cancel_event.is_set():
                raise InterruptedError("初始化已取消；不会关闭已有服务或启用运动")
            if not isinstance(result, dict) or result.get("status") != "success":
                raise RuntimeError((result or {}).get("message", "初始化辅助程序未成功"))
            if result.get("localized") not in (True, False) or type(result.get("localized")) is not bool:
                raise RuntimeError("初始化未返回有效的定位状态")
            if result.get("motion_disabled") is not True:
                raise RuntimeError("未确认安全控制器禁用运动，不能完成初始化")
            if result.get("needs_initial_pose") is not (not result["localized"]):
                raise RuntimeError("初始化定位状态不一致")
            if result["localized"]:
                pose = result.get("pose")
                if not isinstance(pose, dict) or set(pose) != {"x", "y", "z", "yaw"} or any(
                    type(value) not in (int, float) or not math.isfinite(value) for value in pose.values()
                ):
                    raise RuntimeError("定位虽成功但缺少有效当前位姿，请重新检查 TF")
            final = "succeeded"
            message = ("服务已就绪；请在地图上确认当前位置和朝向，完成重定位后才能导航"
                       if result["needs_initial_pose"] else "服务和定位已就绪；自动运动仍禁用，请现场核对地图位置")
        except InterruptedError as exc:
            final, message = "cancelled", str(exc)
        except Exception as exc:
            message = str(exc)
        finally:
            with self.lock:
                self.state.update(state=final, message=message, finished_at=time.time())
                if final == "succeeded":
                    for key in ("localized", "needs_initial_pose", "frame_id", "map_fingerprint", "services", "motion_disabled"):
                        if key in result:
                            self.state[key] = copy.deepcopy(result[key])
                    if result["localized"]:
                        self.state["pose"] = copy.deepcopy(result["pose"])
                # Cancellation/failure must not leak a previous successful pose.
                elif "pose" in self.state:
                    del self.state["pose"]

    def _run_services(self, cancel, report):
        logs = self.project / "run" / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log_path = logs / "robot_initialization.log"
        helper = self.project / "scripts" / "initialize_robot_services.py"
        command = "source " + shlex.quote(str(self.project / "scripts" / "env.sh"))
        command += " && set +u && source \"$ROS_SETUP\" && source \"$NAV_WS/devel/setup.bash\""
        command += " && exec /usr/bin/python3 -u " + shlex.quote(str(helper))
        # Reserve two seconds for bounded termination/reaping; the complete
        # web invocation, not only its work loop, stays within ~58 seconds.
        deadline = time.monotonic() + 56
        with log_path.open("ab", buffering=0) as log:
            process = subprocess.Popen(["bash", "-lc", command], cwd=str(self.project),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=log, start_new_session=True)
            pending = b""
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while True:
                        if cancel.is_set():
                            raise InterruptedError("初始化已取消；已有服务保持原状，自动运动不会启用")
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError("初始化超过 58 秒，请检查网络及初始化日志；未启用运动")
                        if not selector.select(min(0.1, remaining)):
                            continue
                        chunk = os.read(process.stdout.fileno(), 8192)
                        if not chunk:
                            raise RuntimeError("初始化辅助程序未返回结果，请检查 run/logs/robot_initialization.log")
                        log.write(chunk)
                        pending += chunk
                        if len(pending) > 65536:
                            raise RuntimeError("初始化返回数据过大")
                        while b"\n" in pending:
                            line, pending = pending.split(b"\n", 1)
                            try:
                                data = json.loads(line.decode("utf-8"))
                            except (ValueError, UnicodeDecodeError):
                                continue
                            if isinstance(data, dict) and data.get("event") == "stage":
                                report(data["id"], data["state"], data["message"], data.get("details"))
                            elif isinstance(data, dict) and data.get("status") in ("success", "error"):
                                return data
            finally:
                # This group contains only this invocation and its finite probes.
                # Launched persistent services have their own sessions and are
                # deliberately never killed here; existing services are untouched.
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait(timeout=1)
                process.stdout.close()


# Keep a short alias for callers that used the initial design name.
InitManager = InitializationManager
