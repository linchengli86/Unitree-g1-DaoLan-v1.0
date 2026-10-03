#!/usr/bin/env python3
"""Shared, lazy, read-only live-map cache for the standard-library web process.

Only a system-Python child imports ROS. Opening a map subscribes to sensors; it
never initializes services, relocalizes, arms, clears a costmap, or sends goals.
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

MAX_LINE_BYTES = 128 * 1024
IDLE_SECONDS = 60.0
TF_MAX_AGE = 1.5
SCAN_MAX_AGE = 1.5
DATA_MAX_AGE = 2.5
CHECK_MAX_AGE = 2.5


def empty_layer(state="unavailable"):
    return {"state": state, "points": [], "stamp": None, "age_seconds": None}


def empty_snapshot(state="starting", message="正在连接只读地图状态"):
    return {"state": state, "message": message, "frame_id": "map",
            "map_fingerprint": None, "pose": None, "scan": empty_layer(),
            "costmap": empty_layer(), "updated_at": None}


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("实时地图包含无效数值")
    return float(value)


def _age(stamp, now, maximum):
    age = now - _number(stamp)
    if not -0.2 <= age <= maximum:
        raise ValueError("实时地图数据时间已过期")
    return max(0.0, age)


class MapVersionGuard:
    """Persist map-change invalidation across a manager's idle worker restarts."""
    def __init__(self):
        self.fingerprint = None
        self.requires_relocalization = False
        self.changed_at = None
        self.false_checked_at = None

    def apply(self, fingerprint, check_state, now):
        if fingerprint:
            if self.fingerprint is not None and fingerprint != self.fingerprint:
                self.requires_relocalization = True
                self.changed_at, self.false_checked_at = now, None
            self.fingerprint = fingerprint
        if not self.requires_relocalization:
            return check_state
        localized, simulated, checked_at = check_state
        try:
            _age(checked_at, now, CHECK_MAX_AGE)
            valid = checked_at >= self.changed_at
        except (ValueError, TypeError, OverflowError):
            valid = False
        if valid:
            if localized is False:
                self.false_checked_at = checked_at
            elif localized is True and self.false_checked_at is not None and checked_at > self.false_checked_at:
                self.requires_relocalization = False
                return check_state
        return False, simulated, checked_at


def validate_snapshot(value, now=None):
    """Reject malformed subprocess data and independently enforce wall freshness."""
    now = time.time() if now is None else now
    if not isinstance(value, dict) or value.get("frame_id") != "map":
        raise ValueError("实时地图格式无效")
    state = value.get("state")
    if state not in ("ready", "unlocalized", "stale", "starting", "offline"):
        raise ValueError("实时地图状态无效")
    message = value.get("message", "")
    if not isinstance(message, str) or len(message) > 400:
        raise ValueError("实时地图说明无效")
    result = empty_snapshot(state, message)
    fingerprint = value.get("map_fingerprint")
    if fingerprint is not None:
        if (not isinstance(fingerprint, str) or len(fingerprint) != 64 or
                any(character not in "0123456789abcdef" for character in fingerprint)):
            raise ValueError("实时地图指纹无效")
        result["map_fingerprint"] = fingerprint
    updated = value.get("updated_at")
    if updated is not None:
        result["updated_at"] = _number(updated)
        _age(updated, now, TF_MAX_AGE)
    if state != "ready":
        return result
    if result["map_fingerprint"] is None or updated is None:
        raise ValueError("实时地图缺少地图版本或更新时间")
    # The parent's validation does not trust the child's ready label alone.
    if value.get("localized") is not True or value.get("use_sim_time") is not False:
        raise ValueError("实时地图尚未确认实机定位")
    _age(value.get("localization_checked_at"), now, CHECK_MAX_AGE)
    pose = value.get("pose")
    if not isinstance(pose, dict):
        raise ValueError("实时地图缺少位姿")
    result["pose"] = {key: _number(pose.get(key)) for key in ("x", "y", "yaw", "stamp")}
    result["pose"]["age_seconds"] = _age(pose.get("stamp"), now, TF_MAX_AGE)
    for name, limit in (("scan", 400), ("costmap", 600)):
        layer = value.get(name)
        if not isinstance(layer, dict) or layer.get("state") not in (
                "ready", "unavailable", "stale", "transform_unavailable", "invalid"):
            raise ValueError("实时障碍图层格式无效")
        if layer["state"] != "ready":
            result[name] = empty_layer(layer["state"])
            continue
        points = layer.get("points")
        if not isinstance(points, list) or len(points) > limit:
            raise ValueError("实时障碍点数超出上限")
        stamp = _number(layer.get("stamp"))
        try:
            age = _age(stamp, now, SCAN_MAX_AGE if name == "scan" else DATA_MAX_AGE)
        except ValueError:
            # Sensor TTL expiration must not remove a fresh robot pose or
            # terminate its worker. Each overlay expires independently.
            result[name] = empty_layer("stale")
            continue
        clean = []
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError("实时障碍坐标格式无效")
            clean.append([_number(point[0]), _number(point[1])])
        result[name] = {"state": "ready", "points": clean,
                        "stamp": stamp, "age_seconds": age}
    return result


class LiveMapManager:
    def __init__(self, project=PROJECT, runner=None, idle_seconds=IDLE_SECONDS):
        self.project = Path(project)
        self.runner = runner or self._run_stream
        self.idle_seconds = idle_seconds
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.closed = False
        self.last_request = 0.0
        self.retry_after = 0.0
        self.state = empty_snapshot()
        self.raw = None
        self.map_version = MapVersionGuard()

    def snapshot(self):
        with self.lock:
            self.last_request = time.monotonic()
            if self.closed:
                return empty_snapshot("offline", "实时地图已关闭")
            if (self.thread is None or not self.thread.is_alive()) and self.last_request >= self.retry_after:
                self.stop_event = threading.Event()
                self.raw = None
                self.state = empty_snapshot()
                self.thread = threading.Thread(target=self._worker, name="live-map-cache", daemon=True)
                self.thread.start()
            if self.raw is not None:
                try:
                    result = validate_snapshot(self.raw)
                except (ValueError, TypeError, OverflowError):
                    result = empty_snapshot("stale", "实时位置已过期；等待新的定位和传感器数据")
                    result["map_fingerprint"] = self.state.get("map_fingerprint")
                    self.state = result
                else:
                    self.state = result
            return copy.deepcopy(self.state)

    def _emit(self, data):
        with self.lock:
            if self.stop_event.is_set() or self.closed:
                return
            # Validate before trusting metadata or changing the guard. For a
            # bad ready TF time at a 1.5s boundary, suppress all overlays but
            # keep the subscriber alive until its next fresh snapshot.
            try:
                validated = validate_snapshot(data)
            except (ValueError, TypeError, OverflowError):
                self.raw = None
                self.state = empty_snapshot("stale", "实时地图数据无效或已过期；等待新的数据")
                return
            data = copy.deepcopy(data)
            raw_service = data.get("localization_service_status", data.get("localized"))
            self.map_version.apply(data.get("map_fingerprint"),
                (raw_service, data.get("use_sim_time"), data.get("localization_checked_at")), time.time())
            if self.map_version.requires_relocalization:
                data.update(state="unlocalized", message="地图版本已改变；需重新定位后才显示实时位置",
                            pose=None, scan=empty_layer(), costmap=empty_layer())
                validated = validate_snapshot(data)
            self.raw = copy.deepcopy(data)
            self.state = validated

    def _should_stop(self):
        with self.lock:
            idle = time.monotonic() - self.last_request >= self.idle_seconds
        if idle:
            self.stop_event.set()
        return self.stop_event.is_set()

    def _worker(self):
        try:
            self.runner(self.stop_event, self._emit, self._should_stop)
        except Exception:
            # Paths/network exceptions may contain private configuration; do not
            # return subprocess errors verbatim to browsers.
            with self.lock:
                self.raw = None
                self.state = empty_snapshot("offline", "实时地图服务不可用；请检查 ROS 与定位服务")
        finally:
            with self.lock:
                self.retry_after = time.monotonic() + 3.0
                if self.stop_event.is_set():
                    self.raw = None
                    self.state = empty_snapshot("offline", "实时地图订阅已停止")

    def cancel(self):
        with self.lock:
            self.stop_event.set()
            self.raw = None
            self.state = empty_snapshot("offline", "实时地图订阅已停止")

    def close(self):
        with self.lock:
            self.closed = True
            self.stop_event.set()
            thread = self.thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=3)

    def _run_stream(self, stop, emit, should_stop):
        helper = self.project / "scripts" / "stream_live_map.py"
        command = "source " + shlex.quote(str(self.project / "scripts" / "env.sh"))
        command += " && set +u && source \"$ROS_SETUP\" && source \"$NAV_WS/devel/setup.bash\""
        command += " && exec /usr/bin/python3 -u " + shlex.quote(str(helper))
        command += " --project " + shlex.quote(str(self.project))
        process = subprocess.Popen(["bash", "-lc", command], cwd=str(self.project),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            start_new_session=True)
        pending = b""
        last_output = time.monotonic()
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while not should_stop():
                    if time.monotonic() - last_output > 8:
                        raise TimeoutError("live-map helper stopped producing snapshots")
                    if not selector.select(0.1):
                        continue
                    chunk = os.read(process.stdout.fileno(), 8192)
                    if not chunk:
                        raise RuntimeError("live-map helper exited")
                    pending += chunk
                    if len(pending) > MAX_LINE_BYTES:
                        raise ValueError("live-map output exceeded its bound")
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        try:
                            data = json.loads(line.decode("utf-8"))
                        except (ValueError, UnicodeDecodeError):
                            # ROS startup logs may share stdout. JSON-like bad
                            # snapshots are not accepted or kept as fresh.
                            continue
                        if isinstance(data, dict) and data.get("event") == "live_map":
                            emit(data)
                            last_output = time.monotonic()
        finally:
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
