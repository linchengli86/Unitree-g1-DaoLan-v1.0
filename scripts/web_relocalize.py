#!/usr/bin/env python3
"""One bounded local relocalization attempt; no navigation or motion enable."""

import json
import sys
import time

from capture_guide_pose import capture, map_fingerprint, sample_tf
from guide_points import PROJECT, validate_samples
from navigation_thread_budget import apply_current_process
from guide_relocalization import (COARSE_MODE, COARSE_REPORT_PARAM, PRECISE_MODE,
                                 validate_coarse_report, validate_seed, verify_coarse_tf)


def _coarse_request(rospy, rosservice, service_class, check, seed):
    """Request the bounded map->base_link service, never the legacy fallback."""
    try:
        rospy.wait_for_service("/slam_reloc_coarse", timeout=4)
    except Exception as exc:
        raise RuntimeError("1 m 粗定位服务尚未就绪，需要重启更新后的定位模块；不会调用旧朝向接口") from exc
    previous = rospy.get_param(COARSE_REPORT_PARAM, {})
    previous_id = previous.get("request_id") if isinstance(previous, dict) else None
    requested_at = time.time()
    pose = seed["pose"]
    response = rospy.ServiceProxy("/slam_reloc_coarse", service_class)(
        pcd_path=str(PROJECT / "G1Nav2D" / "src" / "fastlio2" / "PCD" / "map.pcd"),
        x=pose["x"], y=pose["y"], z=pose["z"], roll=0, pitch=0, yaw=pose["yaw"])
    if response.status != 1:
        raise RuntimeError("粗定位请求被拒绝：" + str(response.message))
    try:
        accepted = json.loads(response.message)
        request_id = accepted["request_id"]
    except (ValueError, TypeError, KeyError):
        raise RuntimeError("粗定位服务未返回可关联的请求编号，拒绝沿用旧定位结果")
    if (not isinstance(request_id, str) or not 1 <= len(request_id) <= 128 or
            request_id == previous_id):
        raise RuntimeError("粗定位请求编号无效或已使用，拒绝沿用旧定位结果")
    deadline = time.monotonic() + 29
    while time.monotonic() < deadline:
        report = rospy.get_param(COARSE_REPORT_PARAM, {})
        if isinstance(report, dict) and report.get("request_id") == request_id:
            state = report.get("state")
            if state in ("failed", "cancelled"):
                raise RuntimeError(str(report.get("message", "雷达粗定位匹配失败或存在相似解")))
            if state == "succeeded":
                validate_coarse_report(report, request_id, requested_at, PROJECT)
                if not check(code=True).status:
                    raise RuntimeError("粗定位质量报告成功，但定位状态无效；自动运动仍禁用")
                # Let the independently verified offset reach subsequent TF
                # samples; stable pre-request TF alone is never sufficient.
                time.sleep(0.5)
                result = capture(init_ros=False)
                current = rospy.get_param(COARSE_REPORT_PARAM, {})
                validate_coarse_report(current, request_id, requested_at, PROJECT)
                if current != report:
                    raise RuntimeError("采集期间粗定位报告发生变化，请重新初始化")
                samples = result.get("samples", [])
                if not samples or samples[0]["stamp"] < report["finished_at"] - 0.1:
                    raise RuntimeError("定位 TF 尚未更新到本次粗定位结果，请稍后重试")
                verify_coarse_tf(validate_samples(samples, time.time()), report)
                return {**result, "coarse_request_id": request_id,
                        "coarse_requested_at": requested_at, "coarse_report": report}
            if state not in ("matching", "validating"):
                raise RuntimeError("粗定位报告状态无效，拒绝确认定位")
        # Matching another request is not success, even if the old /check is
        # still true. Only this acknowledged request may satisfy the wait.
        time.sleep(0.25)
    raise RuntimeError("1 m 范围内粗定位未在时限内通过独立扫描核验；不会扩大搜索或使能运动")


def relocalize(seed):
    apply_current_process()  # Limit only this finite ROS Python helper.
    import rospy
    import rosservice
    import tf
    from fastlio.srv import SlamReLoc

    seed = validate_seed(seed)
    rospy.init_node("web_relocalization", anonymous=True, disable_signals=True)
    if rospy.get_param("/use_sim_time", False):
        raise RuntimeError("当前为仿真时间，拒绝实机重定位")
    if map_fingerprint() != seed["map_fingerprint"]:
        raise RuntimeError("地图与所选位置不匹配，请更新地图对应的起点")
    precise_service = "/slam_reloc_base" if seed.get("localization_mode") == PRECISE_MODE else "/slam_reloc"
    if seed.get("localization_mode") != COARSE_MODE:
        try:
            rospy.wait_for_service(precise_service, timeout=4)
        except Exception as exc:
            if precise_service == "/slam_reloc_base":
                raise RuntimeError("已确认位置的标准朝向定位服务尚未就绪，需要重启更新后的定位模块") from exc
            raise
    rospy.wait_for_service("/slam_reloc_check", timeout=4)
    service_type = rosservice.get_service_class_by_name("/slam_reloc_check")
    if service_type is None:
        raise RuntimeError("无法查询重定位服务类型")
    check = rospy.ServiceProxy("/slam_reloc_check", service_type)
    listener = tf.TransformListener()
    listener.waitForTransform("map", "base_link", rospy.Time(0), rospy.Duration(3))
    samples = sample_tf(listener)
    validate_samples(samples, time.time())  # Stationary, fresh data; not a trusted map pose yet.
    if seed.get("localization_mode") == COARSE_MODE:
        return _coarse_request(rospy, rosservice, SlamReLoc, check, seed)
    pose = seed["pose"]
    service = rospy.ServiceProxy(precise_service, SlamReLoc)
    response = service(pcd_path=str(PROJECT / "G1Nav2D" / "src" / "fastlio2" / "PCD" / "map.pcd"),
                       x=pose["x"], y=pose["y"], z=pose["z"], roll=0, pitch=0, yaw=pose["yaw"])
    if response.status != 1:
        raise RuntimeError("重定位请求被拒绝：" + str(response.message))
    # Standard confirmed-cache mode uses the same independently verified
    # matcher, while retaining the final 20 cm acceptance bound.
    deadline = time.monotonic() + (29 if precise_service == "/slam_reloc_base" else 24)
    while time.monotonic() < deadline:
        if check(code=True).status:
            time.sleep(0.5)
            return capture(init_ros=False)
        time.sleep(0.4)
    raise RuntimeError("初值附近未能完成重定位，请确认所选位置和朝向；不会扩大搜索或使能运动")


if __name__ == "__main__":
    try:
        print(json.dumps(relocalize(json.loads(sys.stdin.read(6000))), ensure_ascii=False, allow_nan=False), flush=True)
    except Exception as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False), flush=True)
        sys.exit(1)
