#!/usr/bin/env python3
"""Finite, read-only ROS pose capture. Never publishes or enables motion."""

import hashlib
import json
import sys
import time
from pathlib import Path

from guide_points import PROJECT, validate_samples
from navigation_thread_budget import apply_current_process

def file_digest(path):
    def signature():
        info = path.stat()
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    before = signature()
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    if signature() != before:
        raise RuntimeError("地图文件在校验期间发生变化，请重试")
    return digest.digest()


def map_fingerprint():
    import yaml
    map_yaml = PROJECT / "map" / "map.yaml"
    config = yaml.safe_load(map_yaml.read_text())
    image = (map_yaml.parent / config["image"]).resolve()
    image.relative_to(PROJECT.resolve())
    files = [map_yaml, image, PROJECT / "G1Nav2D" / "src" / "fastlio2" / "PCD" / "map.pcd"]
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(PROJECT)).encode())
        digest.update(file_digest(path))
    return digest.hexdigest()


def sample_tf(listener):
    import tf
    samples = []
    deadline = time.monotonic() + 2
    while len(samples) < 5 and time.monotonic() < deadline:
        stamp = listener.getLatestCommonTime("map", "base_link")
        if not samples or stamp.to_sec() > samples[-1]["stamp"]:
            translation, rotation = listener.lookupTransform("map", "base_link", stamp)
            samples.append({"x": translation[0], "y": translation[1], "z": translation[2],
                            "yaw": tf.transformations.euler_from_quaternion(rotation)[2], "stamp": stamp.to_sec()})
        if len(samples) < 5:
            time.sleep(0.04)
    validate_samples(samples, time.time())
    return samples


def capture(init_ros=True, listener=None):
    apply_current_process()  # Standalone helper; before tf/NumPy imports.
    import rospy
    import rosservice
    import tf
    if init_ros:
        rospy.init_node("capture_guide_point", anonymous=True, disable_signals=True)
    rospy.wait_for_service("/slam_reloc_check", timeout=3)
    service_type = rosservice.get_service_class_by_name("/slam_reloc_check")
    if service_type is None:
        raise RuntimeError("无法查询重定位状态")
    check = rospy.ServiceProxy("/slam_reloc_check", service_type)
    if not check(code=True).status:
        raise RuntimeError("重定位未成功，请先完成重定位")
    if rospy.get_param("/use_sim_time", False):
        raise RuntimeError("当前为仿真时间，不能记录实机导览点")
    fingerprint = map_fingerprint()
    # Navigation already owns a long-lived listener. Reuse its buffer rather
    # than registering another /tf callback for every stationary read.
    if listener is None:
        listener = tf.TransformListener()
    listener.waitForTransform("map", "base_link", rospy.Time(0), rospy.Duration(3))
    if not check(code=True).status or map_fingerprint() != fingerprint:
        raise RuntimeError("采集期间定位状态或地图发生变化，请重试")
    # Finish expensive map checks before collecting the final fresh TF.
    samples = sample_tf(listener)
    if not check(code=True).status:
        raise RuntimeError("采集期间重定位状态发生变化，请重试")
    validate_samples(samples, time.time())
    return {"status": "success", "localized": True, "frame_id": "map",
            "samples": samples, "map_fingerprint": fingerprint}


if __name__ == "__main__":
    try:
        print(json.dumps(capture(), ensure_ascii=False, allow_nan=False), flush=True)
    except Exception as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False), flush=True)
        sys.exit(1)
