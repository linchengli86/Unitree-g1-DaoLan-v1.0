#!/usr/bin/env python3
"""Persistent named guide points. Recording is read-only with respect to ROS."""

import base64
import fcntl
import json
import math
import os
import selectors
import shlex
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from guide_actions import ARM_ACTIONS, action_catalog
from navigation_thread_budget import child_env

PROJECT = Path(__file__).resolve().parent.parent


def decode_board_image(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 1400000:
        raise ValueError("展板照片为空或过大，请重新选择照片")
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception as exc:
        raise ValueError("展板照片编码无效") from exc
    if len(raw) > 1000000 or not raw.startswith(b"\xff\xd8\xff") or not raw.endswith(b"\xff\xd9"):
        raise ValueError("展板照片须为不超过 1 MB 的 JPEG 图片")
    return raw


def validate_board_draft(text):
    text = text.strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    data = json.loads(text)
    if not isinstance(data, dict) or set(data) != {"speech", "needs_review", "uncertainties"}:
        raise ValueError("Omni 未返回完整的导览词草稿，请重试")
    if not isinstance(data["speech"], str) or len(data["speech"].strip()) > 150:
        raise ValueError("Omni 导览词格式错误或超过 150 字，请重试")
    if type(data["needs_review"]) is not bool or not isinstance(data["uncertainties"], list):
        raise ValueError("Omni 识别校验信息格式错误")
    if len(data["uncertainties"]) > 8 or any(not isinstance(s, str) or len(s) > 200 for s in data["uncertainties"]):
        raise ValueError("Omni 识别提示格式错误")
    if not data["speech"].strip() and not data["needs_review"]:
        raise ValueError("Omni 未生成导览词，请重拍或重试")
    data["speech"] = data["speech"].strip()
    if data["uncertainties"]:
        data["needs_review"] = True
    return data


def validate_samples(samples, now):
    if len(samples) < 5:
        raise ValueError("定位采样不足，请停稳后重试")
    for sample in samples:
        if set(sample) != {"x", "y", "z", "yaw", "stamp"} or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
            for v in sample.values()
        ):
            raise ValueError("定位数据无效")
        if now - sample["stamp"] < -0.1:
            raise ValueError("定位数据已过期，请检查重定位和 TF")
    # Freshness applies to the newest TF, independently of the earlier
    # observations in the bounded stationary sampling window.
    if now - samples[-1]["stamp"] > 1.5:
        raise ValueError("定位数据已过期，请检查重定位和 TF")
    if not 0.25 <= samples[-1]["stamp"] - samples[0]["stamp"] <= 1.0 or any(
        second["stamp"] < first["stamp"] for first, second in zip(samples, samples[1:])
    ):
        raise ValueError("定位数据未持续更新，请检查 TF")
    first = samples[0]
    for sample in samples[1:]:
        delta_yaw = math.atan2(math.sin(sample["yaw"] - first["yaw"]),
                               math.cos(sample["yaw"] - first["yaw"]))
        if math.hypot(sample["x"] - first["x"], sample["y"] - first["y"]) > 0.03 or abs(delta_yaw) > 0.05:
            raise ValueError("机器人或定位仍在移动，请停稳后重新添加")
    last = samples[-1]
    return {"x": last["x"], "y": last["y"], "z": 0.0,
            "yaw": math.atan2(math.sin(last["yaw"]), math.cos(last["yaw"]))}


def _finish_helper(process, terminate=False):
    try:
        if terminate and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate(timeout=2)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        # Reap without making a returned pose depend on ROS shutdown delay.
        try:
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass


def run_ros_json(script, payload=None, timeout=12, cancel=None, project=PROJECT):
    """Read the flushed result before ROS atexit; bound and reap the helper."""
    if script not in ("capture_guide_pose.py", "web_relocalize.py"):
        raise ValueError("未知 ROS 辅助程序")
    project = Path(project)
    setup = "source /opt/ros/noetic/setup.bash && source " + shlex.quote(str(
        project / "G1Nav2D" / "devel" / "setup.bash"))
    command = setup + " && exec /usr/bin/python3 -u " + shlex.quote(str(project / "scripts" / script))
    deadline = time.monotonic() + timeout
    process = subprocess.Popen(["bash", "-lc", command], stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL,
        start_new_session=True, env=child_env())
    received = False
    try:
        if payload is not None:
            process.stdin.write(json.dumps(payload, ensure_ascii=False).encode())
            process.stdin.close()
            process.stdin = None
        pending = b""
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                if cancel is not None and cancel.is_set():
                    raise InterruptedError("定位读取已取消，自动运动不会启用")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("定位读取超时，请检查 ROS 定位节点")
                if not selector.select(min(0.1, remaining)):
                    continue
                chunk = os.read(process.stdout.fileno(), 8192)
                if not chunk:
                    raise RuntimeError("定位节点未返回有效结果，请检查 ROS 环境")
                pending += chunk
                if len(pending) > 65536:
                    raise RuntimeError("定位节点返回的数据过大")
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    try:
                        data = json.loads(line.decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if isinstance(data, dict) and data.get("status") in ("success", "error"):
                        received = True
                        return data
    finally:
        if received:
            threading.Thread(target=_finish_helper, args=(process,), name="ros-helper-reaper", daemon=True).start()
        else:
            _finish_helper(process, terminate=True)


def capture_pose():
    data = run_ros_json("capture_guide_pose.py")
    if data.get("status") != "success":
        raise RuntimeError(data.get("message", "读取定位失败"))
    return data


class GuidePointStore:
    def __init__(self, path=None, pose_provider=capture_pose):
        self.path = Path(path) if path else PROJECT / "config" / "guide_points.json"
        self.pose_provider = pose_provider
        self.lock = threading.RLock()

    def _read(self):
        if not self.path.exists():
            return {"version": 1, "points": []}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or not isinstance(data.get("points"), list):
            raise RuntimeError("导览点文件格式错误；已保留原文件，请检查")
        return data

    def list(self):
        with self.lock:
            return self._read()["points"]

    def read_pose(self):
        capture = self.pose_provider()
        if capture.get("localized") is not True or capture.get("frame_id") != "map":
            raise ValueError("重定位未成功，不能读取地图位置；请在已确认位置完成重定位")
        now = time.time()
        pose = validate_samples(capture.get("samples", []), now)
        fingerprint = capture.get("map_fingerprint")
        if not isinstance(fingerprint, str) or len(fingerprint) != 64 or any(c not in "0123456789abcdef" for c in fingerprint):
            raise ValueError("地图校验信息缺失，不能记录导览点")
        return {"pose": pose, "localized": True, "frame_id": "map", "map_fingerprint": fingerprint,
                "tf_age_seconds": max(0.0, now - capture["samples"][-1]["stamp"]), "sample_count": len(capture["samples"])}

    @staticmethod
    def validate_metadata(payload):
        if not isinstance(payload, dict) or set(payload) - {"name", "speech", "gesture"}:
            raise ValueError("导览点只接收名称、讲解和动作；坐标由机器人采集")
        name, speech, gesture = payload.get("name", ""), payload.get("speech", ""), payload.get("gesture", "")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
            raise ValueError("导览点名称须为 1–40 字")
        if not isinstance(speech, str) or len(speech.strip()) > 150:
            raise ValueError("讲解内容不能超过 150 字")
        if not isinstance(gesture, str) or (gesture and gesture not in ARM_ACTIONS):
            raise ValueError("未知导览动作")
        if gesture and not action_catalog()["arm_gestures"][gesture]["verified"]:
            raise ValueError("该动作尚未经过实机验证，不能绑定到导览点")
        return {"name": name.strip(), "speech": speech.strip(), "gesture": gesture}

    def add(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("导览点必须是 JSON 对象")
        payload = dict(payload)
        image_b64 = payload.pop("image_jpeg_base64", None)
        image = decode_board_image(image_b64) if image_b64 is not None else None
        metadata = self.validate_metadata(payload)
        capture = self.read_pose()
        pose, fingerprint = capture["pose"], capture["map_fingerprint"]
        point = {"id": uuid.uuid4().hex, **metadata, "frame_id": "map", "pose": pose,
                 "map_fingerprint": fingerprint,
                 "created_at": datetime.now(timezone.utc).isoformat()}
        if image is not None:
            point["image"] = "/api/points/" + point["id"] + "/image"
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.with_suffix(".lock").open("a") as guard:
                fcntl.flock(guard, fcntl.LOCK_EX)
                data = self._read()
                if any(p["name"].casefold() == metadata["name"].casefold() for p in data["points"]):
                    raise ValueError("名称已存在，请换一个名称；原导览点未被覆盖")
                if len(data["points"]) >= 200:
                    raise ValueError("导览点数量已达到 200 个上限")
                data["points"].append(point)
                fd, temporary = tempfile.mkstemp(prefix=".guide_points-", dir=str(self.path.parent))
                image_path = self.path.parent / "guide_point_images" / (point["id"] + ".jpg")
                committed = False
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as output:
                        json.dump(data, output, ensure_ascii=False, indent=2, allow_nan=False)
                        output.write("\n")
                        output.flush()
                        os.fsync(output.fileno())
                    if image is not None:
                        image_path.parent.mkdir(parents=True, exist_ok=True)
                        with image_path.open("xb") as output:
                            output.write(image)
                            output.flush()
                            os.fsync(output.fileno())
                    os.replace(temporary, self.path)
                    committed = True
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
                    if image is not None and not committed and image_path.exists():
                        image_path.unlink()
        return point

    def image_path(self, point_id):
        if not isinstance(point_id, str) or len(point_id) != 32 or any(c not in "0123456789abcdef" for c in point_id):
            raise ValueError("导览点编号无效")
        if not any(p["id"] == point_id and p.get("image") for p in self.list()):
            raise ValueError("该导览点没有展板照片")
        return self.path.parent / "guide_point_images" / (point_id + ".jpg")
