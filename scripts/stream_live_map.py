#!/usr/bin/env python3
"""One ROS subscriber worker emitting bounded wall-clock live-map snapshots.

Read-only: the sole service call is /slam_reloc_check(code=True). No publisher,
motion SDK, initialization, map mutation, goal, enable, or clear operation.
"""

import argparse
import json
import math
import sys
import threading
import time
from pathlib import Path

from guide_live_map import (DATA_MAX_AGE, SCAN_MAX_AGE, CHECK_MAX_AGE, TF_MAX_AGE,
                            empty_layer, empty_snapshot, MapVersionGuard)
from guide_points import PROJECT


def valid_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def fresh(stamp, now, maximum):
    return valid_number(stamp) and -0.2 <= now - stamp <= maximum


def quaternion_matrix(quaternion):
    """Full normalized quaternion transform; laser projection is not yaw-only."""
    if len(quaternion) != 4 or not all(valid_number(value) for value in quaternion):
        raise ValueError("invalid quaternion")
    x, y, z, w = quaternion
    norm = math.sqrt(x*x + y*y + z*z + w*w)
    if norm < 1e-9:
        raise ValueError("empty quaternion")
    x, y, z, w = [value / norm for value in quaternion]
    return ((1 - 2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)),
            (2*(x*y+z*w), 1 - 2*(x*x+z*z), 2*(y*z-x*w)),
            (2*(x*z-y*w), 2*(y*z+x*w), 1 - 2*(x*x+y*y)))


def quaternion_yaw(quaternion):
    matrix = quaternion_matrix(quaternion)
    return math.atan2(matrix[1][0], matrix[0][0])


def transform_point(point, translation, quaternion):
    if len(translation) != 3 or not all(valid_number(value) for value in translation):
        raise ValueError("invalid translation")
    matrix = quaternion_matrix(quaternion)
    return [translation[row] + sum(matrix[row][column] * point[column] for column in range(3))
            for row in range(3)]


def project_scan(scan, translation, quaternion, limit=400):
    if not all(valid_number(getattr(scan, name)) for name in (
            "angle_min", "angle_increment", "range_min", "range_max")):
        raise ValueError("invalid laser limits")
    if scan.range_min < 0 or scan.range_max <= scan.range_min or len(scan.ranges) > 100_000:
        raise ValueError("invalid laser scan")
    matrix = quaternion_matrix(quaternion)
    if len(translation) != 3 or not all(valid_number(value) for value in translation):
        raise ValueError("invalid scan transform")
    candidates = []
    ceiling = min(6.0, scan.range_max)
    for index, distance in enumerate(scan.ranges):
        if not valid_number(distance) or not 0 < distance or not scan.range_min <= distance <= ceiling:
            continue
        angle = scan.angle_min + index * scan.angle_increment
        x, y = distance * math.cos(angle), distance * math.sin(angle)
        candidates.append([translation[0] + matrix[0][0]*x + matrix[0][1]*y,
                           translation[1] + matrix[1][0]*x + matrix[1][1]*y])
    if len(candidates) <= limit:
        return candidates
    return [candidates[index * len(candidates) // limit] for index in range(limit)]


def project_costmap(grid, translation, quaternion, limit=600):
    info = grid.info
    if (type(info.width) is not int or type(info.height) is not int or
            not 0 < info.width <= 2000 or not 0 < info.height <= 2000 or
            info.width * info.height != len(grid.data) or not valid_number(info.resolution) or
            not 0 < info.resolution <= 10):
        raise ValueError("invalid local costmap")
    origin = info.origin
    origin_xyz = [origin.position.x, origin.position.y, origin.position.z]
    if not all(valid_number(value) for value in origin_xyz):
        raise ValueError("invalid costmap origin")
    origin_yaw = quaternion_yaw([origin.orientation.x, origin.orientation.y,
                                origin.orientation.z, origin.orientation.w])
    cosine, sine = math.cos(origin_yaw), math.sin(origin_yaw)
    # Noetic Costmap2DPublisher translates INSCRIBED_INFLATED_OBSTACLE (253)
    # to OccupancyGrid 99, and LETHAL_OBSTACLE (254) to 100. Only 100 is a
    # sensed lethal obstacle; 99 is a safety inflation boundary, not a wall.
    lethal = [index for index, value in enumerate(grid.data) if value == 100]
    if len(lethal) > limit:
        lethal = [lethal[index * len(lethal) // limit] for index in range(limit)]
    matrix = quaternion_matrix(quaternion)
    if len(translation) != 3 or not all(valid_number(value) for value in translation):
        raise ValueError("invalid costmap transform")
    points = []
    for index in lethal:
        x = (index % info.width + 0.5) * info.resolution
        y = (index // info.width + 0.5) * info.resolution
        local = [origin_xyz[0] + cosine*x - sine*y,
                 origin_xyz[1] + sine*x + cosine*y, origin_xyz[2]]
        points.append([translation[row] + sum(matrix[row][column]*local[column] for column in range(3))
                       for row in range(2)])
    return points


class FingerprintCache:
    """Hash the same assets as capture_guide_pose, only when their stats change."""
    def __init__(self, project, fingerprint=None):
        self.project = Path(project).resolve()
        self.compute = fingerprint or self._compute
        self.signature = None
        self.value = None

    def _signature(self):
        import yaml
        map_yaml = self.project / "map" / "map.yaml"
        map_yaml.resolve(strict=True).relative_to(self.project)
        if map_yaml.stat().st_size > 1024 * 1024:
            raise ValueError("map YAML too large")
        # Parsing a small YAML each 500 ms is cheap; large PCD hashing is cached.
        config = yaml.safe_load(map_yaml.read_text())
        image = (map_yaml.parent / config["image"]).resolve(strict=True)
        paths = [map_yaml.resolve(strict=True), image,
                 (self.project / "G1Nav2D/src/fastlio2/PCD/map.pcd").resolve(strict=True)]
        result = []
        for path in paths:
            path.relative_to(self.project)
            info = path.stat()
            if not path.is_file():
                raise ValueError("map asset is not a file")
            result.append((str(path), info.st_dev, info.st_ino, info.st_size,
                           info.st_mtime_ns, info.st_ctime_ns))
        return tuple(result)

    def _compute(self):
        import capture_guide_pose
        capture_guide_pose.PROJECT = self.project
        return capture_guide_pose.map_fingerprint()

    def get(self):
        signature = self._signature()
        if signature != self.signature:
            self.value = None
            value = self.compute()
            if self._signature() != signature:
                raise RuntimeError("map changed during fingerprint")
            self.value, self.signature = value, signature
        return self.value


class LocalizationCheck:
    """At most one bounded-result probe; a hung ROS call never blocks streaming."""
    def __init__(self, probe, clock=time.time):
        self.probe, self.clock = probe, clock
        self.lock = threading.Lock()
        self.thread = None
        self.last_started = 0.0
        self.checked_at = None
        self.localized = False
        self.simulated = True

    def refresh(self):
        now = self.clock()
        with self.lock:
            if (self.thread and self.thread.is_alive()) or now - self.last_started < 0.75:
                return
            self.last_started = now
            self.thread = threading.Thread(target=self._run, args=(now,), daemon=True)
            self.thread.start()

    def _run(self, started):
        try:
            localized, simulated = self.probe()
            ended = self.clock()
            # A slow response may describe the beginning of a blocked call.
            # Discard it; do not grant a fresh lease after an arbitrary delay.
            valid = ended - started <= 1.0 and type(localized) is bool and type(simulated) is bool
        except Exception:
            valid, localized, simulated = False, False, True
            ended = self.clock()
        with self.lock:
            self.localized = localized if valid else False
            self.simulated = simulated if valid else True
            self.checked_at = ended if valid else None

    def snapshot(self):
        with self.lock:
            return self.localized, self.simulated, self.checked_at


def compose_snapshot(fingerprint, check, pose, scan, costmap, now=None):
    """Moving poses are valid: require freshness, never stationary samples."""
    now = time.time() if now is None else now
    localized, simulated, checked_at = check
    result = empty_snapshot("unlocalized", "尚未重定位；不显示未知坐标和地图障碍")
    result.update(event="live_map", map_fingerprint=fingerprint, updated_at=now,
                  localized=localized, use_sim_time=simulated, localization_checked_at=checked_at)
    if not fresh(checked_at, now, CHECK_MAX_AGE):
        result.update(state="stale", message="定位检查已过期；等待定位服务")
        return result
    if not localized or simulated:
        return result
    if not pose or not fresh(pose.get("stamp"), now, TF_MAX_AGE):
        result.update(state="stale", message="机器人位置已过期；等待新的 TF")
        return result
    if not fingerprint:
        result.update(state="offline", message="地图版本不可用；不显示机器人位置")
        return result
    result.update(state="ready", message="实时二维地图（仅显示，不控制机器人）", pose=pose)
    for name, layer in (("scan", scan), ("costmap", costmap)):
        if layer and layer.get("state") == "ready":
            if fresh(layer.get("stamp"), now, SCAN_MAX_AGE if name == "scan" else DATA_MAX_AGE):
                result[name] = layer
            else:
                result[name] = empty_layer("stale")
        elif layer:
            result[name] = layer
    return result


def stream(project):
    import rospy
    import rosservice
    import tf
    from sensor_msgs.msg import LaserScan
    from nav_msgs.msg import OccupancyGrid

    rospy.init_node("daolan_live_map", anonymous=True, disable_signals=True)
    listener = tf.TransformListener()
    message_lock = threading.Lock()
    messages = {"scan": None, "costmap": None}

    def remember(name, message):
        with message_lock:
            messages[name] = message

    rospy.Subscriber("/scan", LaserScan, lambda message: remember("scan", message),
                     queue_size=1, buff_size=256*1024)
    rospy.Subscriber("/move_base/local_costmap/costmap", OccupancyGrid,
                     lambda message: remember("costmap", message), queue_size=1, buff_size=4*1024*1024)

    def probe():
        # Only this daemon thread can issue RPCs; a stuck master/service cannot
        # stall TF display, spawn more probes, or keep old poses valid.
        simulated = rospy.get_param("/use_sim_time", False)
        rospy.wait_for_service("/slam_reloc_check", timeout=0.3)
        service_type = rosservice.get_service_class_by_name("/slam_reloc_check")
        if service_type is None:
            raise RuntimeError("missing relocation check")
        response = rospy.ServiceProxy("/slam_reloc_check", service_type)(code=True)
        return bool(response.status), simulated

    check = LocalizationCheck(probe)
    fingerprint = FingerprintCache(project)
    map_version = MapVersionGuard()
    while not rospy.is_shutdown():
        began = time.monotonic()
        check.refresh()
        try:
            current_fingerprint = fingerprint.get()
        except Exception:
            current_fingerprint = None
        now = time.time()
        pose = None
        try:
            stamp = listener.getLatestCommonTime("map", "base_link")
            translation, quaternion = listener.lookupTransform("map", "base_link", stamp)
            if all(valid_number(value) for value in translation):
                pose = {"x": translation[0], "y": translation[1],
                        "yaw": quaternion_yaw(quaternion), "stamp": stamp.to_sec()}
        except Exception:
            pass
        layers = {}
        with message_lock:
            cached = dict(messages)
        for name, message in cached.items():
            layer = empty_layer()
            if message is not None:
                stamp = message.header.stamp.to_sec()
                if not fresh(stamp, now, SCAN_MAX_AGE if name == "scan" else DATA_MAX_AGE):
                    layer = empty_layer("stale")
                else:
                    try:
                        frame = message.header.frame_id.lstrip("/")
                        if not frame or len(frame) > 160:
                            raise ValueError("invalid sensor frame")
                        translation, quaternion = listener.lookupTransform("map", frame, message.header.stamp)
                        points = (project_scan(message, translation, quaternion) if name == "scan"
                                  else project_costmap(message, translation, quaternion))
                        layer = {"state": "ready", "points": points, "stamp": stamp,
                                 "age_seconds": max(0.0, now-stamp)}
                    except (tf.LookupException, tf.ConnectivityException, tf.ExtrapolationException):
                        layer = empty_layer("transform_unavailable")
                    except Exception:
                        layer = empty_layer("invalid")
            layers[name] = layer
        raw_check = check.snapshot()
        check_state = map_version.apply(current_fingerprint, raw_check, time.time())
        result = compose_snapshot(current_fingerprint, check_state, pose,
                                  layers["scan"], layers["costmap"], now=time.time())
        result["localization_service_status"] = raw_check[0]
        if map_version.requires_relocalization:
            result["message"] = "地图版本已改变；需重新定位后才显示实时位置"
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")), flush=True)
        time.sleep(max(0.0, 0.5-(time.monotonic()-began)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=str(PROJECT))
    arguments = parser.parse_args()
    try:
        stream(Path(arguments.project))
    except Exception:
        value = empty_snapshot("offline", "实时地图 ROS 订阅不可用")
        value.update(event="live_map", updated_at=time.time())
        print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)
        sys.exit(1)
