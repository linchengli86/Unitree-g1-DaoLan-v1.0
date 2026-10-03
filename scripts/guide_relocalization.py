#!/usr/bin/env python3
"""Bounded web relocalization; never enables robot motion."""

import copy
import json
import math
import os
import tempfile
import threading
import time
from pathlib import Path

from guide_points import PROJECT, run_ros_json, validate_samples

MANUAL_SEED_NAME = "地图点选当前位置"
COARSE_MODE = "coarse_1m"
PRECISE_MODE = "precise_confirmed"
COARSE_RADIUS = 1.0
COARSE_YAW_UNCERTAINTY = math.pi / 4
COARSE_REPORT_PARAM = "/daolan_coarse_relocalization"
COARSE_MAX_SCORE = 0.0324  # Mean squared bounded inlier error, RMSE <= 0.18 m.
COARSE_MIN_INLIER_RATIO = 0.55
COARSE_MIN_POINTS = 80
COARSE_CANDIDATES = 65


def validate_coarse_report(report, request_id, requested_at, project=PROJECT, now=None):
    """Require request-specific matching and an independently acquired scan.

    A fresh, stable TF and /slam_reloc_check alone cannot establish correct
    localization. The C++ service must complete its full bounded candidate
    search, rule out ambiguity, and verify on a later scan before committing.
    """
    now = time.time() if now is None else now
    if (not isinstance(report, dict) or not isinstance(request_id, str) or
            not 1 <= len(request_id) <= 128 or report.get("request_id") != request_id):
        raise RuntimeError("粗定位报告不属于本次请求，拒绝沿用旧定位结果")
    def finite(value):
        return (not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value))
    start, finish = report.get("started_at"), report.get("finished_at")
    if (not finite(requested_at) or not finite(start) or not finite(finish) or
            start < requested_at - 1.0 or start > requested_at + 1.0 or
            not start <= finish <= now + 0.1 or now - finish > 10 or finish - start > 29):
        raise RuntimeError("粗定位报告时间无效或已过期，请重新定位")
    expected_map = str(Path(project).resolve() / "G1Nav2D" / "src" / "fastlio2" / "PCD" / "map.pcd")
    if report.get("state") != "succeeded" or report.get("map_path") != expected_map:
        raise RuntimeError("粗定位尚未成功或报告地图不匹配")
    if (report.get("pose_verified") is not True or report.get("independent_scan_verified") is not True or
            report.get("ambiguous") is not False):
        raise RuntimeError("粗定位未通过独立扫描核验或存在相似解；自动运动仍禁用")
    if (type(report.get("candidate_count")) is not int or report["candidate_count"] != COARSE_CANDIDATES or
            type(report.get("final_candidates")) is not int or not 1 <= report["final_candidates"] <= 4):
        raise RuntimeError("粗定位候选搜索不完整，不能确认位置")
    for prefix in ("", "validation_"):
        score, ratio, count = (report.get(prefix + key) for key in ("score", "inlier_ratio", "count"))
        if (not finite(score) or not 0 <= score <= COARSE_MAX_SCORE + 1e-9 or
                not finite(ratio) or not COARSE_MIN_INLIER_RATIO <= ratio <= 1 or
                type(count) is not int or count < COARSE_MIN_POINTS):
            raise RuntimeError("粗定位或独立扫描匹配质量不足，不能确认位置")
    pose = report.get("verified_pose")
    if (not isinstance(pose, dict) or set(pose) != {"x", "y", "z", "yaw"} or
            any(not finite(value) for value in pose.values())):
        raise RuntimeError("粗定位报告缺少已独立核验的标准位姿")
    return report


def verify_coarse_tf(pose, report):
    expected = report["verified_pose"]
    yaw_error = abs(math.atan2(math.sin(pose["yaw"] - expected["yaw"]),
                               math.cos(pose["yaw"] - expected["yaw"])))
    if math.hypot(pose["x"] - expected["x"], pose["y"] - expected["y"]) > 0.10 or yaw_error > 0.10:
        raise RuntimeError("当前 TF 与本次独立扫描核验位姿不一致，不能确认定位")


def validate_seed(seed):
    if not isinstance(seed, dict) or seed.get("frame_id") != "map":
        raise ValueError("重定位初值必须来自地图坐标系")
    pose = seed.get("pose", {})
    if not isinstance(pose, dict) or set(pose) != {"x", "y", "z", "yaw"} or any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
        for value in pose.values()
    ):
        raise ValueError("重定位初值无效")
    fingerprint = seed.get("map_fingerprint", "")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64 or any(c not in "0123456789abcdef" for c in fingerprint):
        raise ValueError("重定位初值缺少地图校验信息")
    name = seed.get("name", "最近确认位置")
    if not isinstance(name, str) or not 1 <= len(name) <= 80:
        raise ValueError("重定位位置名称无效")
    validated = {"name": name, "frame_id": "map", "pose": dict(pose), "map_fingerprint": fingerprint}
    mode = seed.get("localization_mode")
    if name == MANUAL_SEED_NAME:
        mode = COARSE_MODE if mode is None else mode
        if mode not in (COARSE_MODE, PRECISE_MODE):
            raise ValueError("地图初值的定位模式无效")
        validated["localization_mode"] = mode
        if mode == COARSE_MODE:
            for key, fixed in (("radius", COARSE_RADIUS), ("yaw_uncertainty", COARSE_YAW_UNCERTAINTY)):
                value = seed.get(key, fixed)
                if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                        not math.isfinite(value) or abs(value - fixed) > 1e-9):
                    raise ValueError("地图粗定位范围固定为 1 m、朝向范围固定为 ±45°")
                validated[key] = fixed
        elif any(key in seed for key in ("radius", "yaw_uncertainty")):
            raise ValueError("已确认精定位初值不能附带粗搜索范围")
    elif mode is not None or any(key in seed for key in ("radius", "yaw_uncertainty")):
        raise ValueError("粗定位仅接受网页地图点选的当前位置")
    return validated


class RelocalizationManager:
    def __init__(self, points, stop, idle=lambda: True, project=PROJECT, runner=None):
        self.project = Path(project)
        self.seed_file = self.project / "config" / "relocalization_seed.json"
        self.points, self.stop, self.idle = points, stop, idle
        self.runner = runner or self._run_ros
        self.lock = threading.RLock()
        self.cancel_event = threading.Event()
        self.thread = None
        self.state = {"state": "idle", "message": "尚未请求网页重定位"}

    def remember(self, seed):
        seed = validate_seed(seed)
        with self.lock:
            self.seed_file.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".relocalization-", dir=str(self.seed_file.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as output:
                    json.dump(seed, output, ensure_ascii=False, allow_nan=False)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, self.seed_file)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

    def choose_seed(self, point_id="auto"):
        points = self.points.list()
        if point_id == "auto":
            start = next((p for p in points if p["name"].strip() == "起点"), None)
            if start:
                return validate_seed(start)
            if self.seed_file.exists():
                return validate_seed(json.loads(self.seed_file.read_text(encoding="utf-8")))
            raise ValueError("没有重定位初值，请先配置起点或确认当前位置")
        point = next((p for p in points if p["id"] == point_id), None)
        if not point:
            raise ValueError("所选导览点不存在")
        return validate_seed(point)

    def active(self):
        with self.lock:
            return self.state["state"] == "running"

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.state)

    def start(self, point_id="auto"):
        seed = self.choose_seed(point_id)
        return self.start_seed(seed)

    def start_seed(self, seed):
        # Manual map initial poses are validated by GuideMap at the HTTP
        # boundary. They are localization seeds, never navigation targets.
        seed = validate_seed(seed)
        with self.lock:
            if self.state["state"] == "running":
                raise RuntimeError("重定位正在进行，请勿重复点击")
            self.cancel_event.clear()
            self.state = {"state": "running", "seed": seed, "started_at": time.time(),
                          "message": "正在取消导航并禁用运动，然后进行重定位"}
            self.thread = threading.Thread(target=self._worker, args=(seed,), name="web-relocalization", daemon=True)
            self.thread.start()
            return copy.deepcopy(self.state)

    def cancel(self):
        with self.lock:
            active = self.state["state"] == "running"
            if active:
                self.cancel_event.set()
            return active

    def _check_cancel(self):
        if self.cancel_event.is_set():
            raise InterruptedError("重定位已取消，自动运动保持禁用")

    def _worker(self, seed):
        result = None
        final, message = "failed", "重定位失败"
        try:
            self._check_cancel()
            stopped = self.stop()
            if not stopped.get("stopped"):
                raise RuntimeError("未得到安全控制器停止确认，不进行重定位")
            deadline = time.monotonic() + 6
            while not self.idle():
                self._check_cancel()
                if time.monotonic() >= deadline:
                    raise RuntimeError("上一个任务尚未结束，请稍后重试")
                time.sleep(0.1)
            self._check_cancel()
            result = self.runner(seed, self.cancel_event)
            self._check_cancel()
            if result.get("status") != "success":
                raise RuntimeError(result.get("message", "重定位未成功"))
            pose = validate_samples(result.get("samples", []), time.time())
            if result.get("localized") is not True or result.get("frame_id") != "map" or result.get("map_fingerprint") != seed["map_fingerprint"]:
                raise RuntimeError("定位状态或地图不匹配，不能确认成功")
            coarse = seed.get("localization_mode") == COARSE_MODE
            if coarse:
                validate_coarse_report(result.get("coarse_report"), result.get("coarse_request_id"),
                                      result.get("coarse_requested_at"), self.project)
                if result["samples"][0]["stamp"] < result["coarse_report"]["finished_at"] - 0.1:
                    raise RuntimeError("定位 TF 尚未更新到本次粗定位结果，请稍后重试")
                verify_coarse_tf(pose, result["coarse_report"])
            allowed_radius = COARSE_RADIUS + 1e-5 if coarse else 0.20
            if math.hypot(pose["x"] - seed["pose"]["x"], pose["y"] - seed["pose"]["y"]) > allowed_radius:
                if coarse:
                    raise RuntimeError("定位结果超出点选位置的 1 m 范围，拒绝扩大搜索；自动运动仍禁用")
                raise RuntimeError("定位结果距所选初值超过 20 cm，请确认位置；不自动扩大搜索")
            if seed["name"] == MANUAL_SEED_NAME:
                yaw_error = abs(math.atan2(math.sin(pose["yaw"] - seed["pose"]["yaw"]),
                                           math.cos(pose["yaw"] - seed["pose"]["yaw"])))
                if yaw_error > (COARSE_YAW_UNCERTAINTY + 1e-5 if coarse else 0.45):
                    raise RuntimeError("定位朝向与地图点选箭头偏差超出允许范围，请核对朝向；自动运动仍禁用")
            remembered = {**seed, "pose": pose}
            if coarse:
                remembered["localization_mode"] = PRECISE_MODE
                remembered.pop("radius", None)
                remembered.pop("yaw_uncertainty", None)
            self.remember(remembered)
            final, message = "succeeded", "重定位成功，定位数据稳定；自动运动仍禁用，请现场核对位置"
        except InterruptedError as exc:
            final, message = "cancelled", str(exc)
        except Exception as exc:
            message = str(exc)
        finally:
            with self.lock:
                self.state.update(state=final, message=message, finished_at=time.time())
                if final == "succeeded":
                    self.state["pose"] = pose
                    if seed.get("localization_mode") == COARSE_MODE:
                        self.state["coarse_report"] = copy.deepcopy(result["coarse_report"])

    def _run_ros(self, seed, cancel):
        data = run_ros_json("web_relocalize.py", payload=seed, timeout=45, cancel=cancel, project=self.project)
        if data.get("status") != "success":
            raise RuntimeError(data.get("message", "重定位失败"))
        return data
