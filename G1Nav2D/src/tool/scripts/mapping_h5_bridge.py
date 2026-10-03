#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
建图时由 mapping.sh 自动拉起：为 H5 写入与 nav_ws_bridge 相同的数据文件。

- 订阅 PointCloud2（默认 /body_cloud，frame 一般为 body）
- TF 转到 map 后抽样 x,y -> nav_laser_points.json
- 定时 map<-body 位姿 -> nav_robot_pose.json

单独调试（需已 source 工作空间、roscore 在跑）:
  python3 "$(dirname "$0")/mapping_h5_bridge.py"
"""
from __future__ import annotations

import json
import math
import os
import time

import rospy
import tf
from sensor_msgs.msg import PointCloud2
from sensor_msgs import point_cloud2

BASE_DIR = os.environ.get(
    "BASE_DIR",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")),
)
PID_DIR = os.environ.get("PID_DIR", os.path.join(BASE_DIR, "run", "pids"))
POSE_FILE = os.path.join(PID_DIR, "nav_robot_pose.json")
LASER_FILE = os.path.join(PID_DIR, "nav_laser_points.json")


class MappingH5Bridge:
    def __init__(self):
        rospy.init_node("mapping_h5_bridge", anonymous=False)
        self.tf_listener = tf.TransformListener()
        self.map_frame = rospy.get_param("~map_frame", "map")
        self.body_frame = rospy.get_param("~body_frame", "body")
        self.cloud_topic = rospy.get_param("~cloud_topic", "/body_cloud")
        self.max_points = int(rospy.get_param("~max_points", 1200))
        self.z_min = float(rospy.get_param("~z_min", -2.0))
        self.z_max = float(rospy.get_param("~z_max", 2.5))
        # 雷达倒置安装补偿：绕 X 轴 180°，表现为 y/z 取反
        self.lidar_inverted = bool(rospy.get_param("~lidar_inverted", True))

        self.pose = None
        self.last_pose_write = 0.0

        os.makedirs(PID_DIR, exist_ok=True)

        rospy.Subscriber(self.cloud_topic, PointCloud2, self._cloud_cb, queue_size=1)
        rospy.Timer(rospy.Duration(0.2), self._pose_timer)
        rospy.on_shutdown(self._cleanup_files)
        rospy.loginfo(
            "mapping_h5_bridge: cloud=%s -> map; pose %s<-%s; z_range=[%.2f, %.2f] lidar_inverted=%s; JSON -> %s",
            self.cloud_topic,
            self.map_frame,
            self.body_frame,
            self.z_min,
            self.z_max,
            str(self.lidar_inverted),
            PID_DIR,
        )
        self._last_tf_warn_ts = 0.0

    def _atomic_write_json(self, filepath, payload):
        tmp = filepath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, filepath)

    def _pose_timer(self, _evt):
        try:
            trans, rot = self.tf_listener.lookupTransform(
                self.map_frame, self.body_frame, rospy.Time(0)
            )
            yaw = tf.transformations.euler_from_quaternion(rot)[2]
            self.pose = {
                "x": float(trans[0]),
                "y": float(trans[1]),
                "yaw_rad": float(yaw),
                "yaw": float(math.degrees(yaw)),
            }
            now = time.time()
            if now - self.last_pose_write >= 0.2:
                self._atomic_write_json(
                    POSE_FILE,
                    {
                        "x": self.pose["x"],
                        "y": self.pose["y"],
                        "yaw": self.pose["yaw"],
                        "yaw_rad": self.pose["yaw_rad"],
                        "updated_at": now,
                    },
                )
                self.last_pose_write = now
        except Exception:
            pass

    def _lookup_2d_tf(self, source_frame: str, stamp):
        """获取 map <- source_frame 的二维平面变换 (tx, ty, yaw)。"""
        try:
            self.tf_listener.waitForTransform(
                self.map_frame, source_frame, stamp, rospy.Duration(0.15)
            )
            trans, rot = self.tf_listener.lookupTransform(self.map_frame, source_frame, stamp)
        except Exception:
            self.tf_listener.waitForTransform(
                self.map_frame, source_frame, rospy.Time(0), rospy.Duration(0.15)
            )
            trans, rot = self.tf_listener.lookupTransform(self.map_frame, source_frame, rospy.Time(0))
        yaw = tf.transformations.euler_from_quaternion(rot)[2]
        return float(trans[0]), float(trans[1]), float(yaw)

    def _cloud_cb(self, msg: PointCloud2):
        try:
            tx, ty, yaw = self._lookup_2d_tf(msg.header.frame_id, msg.header.stamp)
            cos_yaw = math.cos(yaw)
            sin_yaw = math.sin(yaw)
        except Exception:
            now = time.time()
            if now - self._last_tf_warn_ts > 2.0:
                rospy.logwarn(
                    "mapping_h5_bridge: TF unavailable for %s -> %s",
                    msg.header.frame_id,
                    self.map_frame,
                )
                self._last_tf_warn_ts = now
            return

        try:
            pts = []
            for x, y, z in point_cloud2.read_points(
                msg, field_names=("x", "y", "z"), skip_nans=True
            ):
                sx = float(x)
                sy = float(y)
                sz = float(z)
                if self.lidar_inverted:
                    sy = -sy
                    sz = -sz
                if not (self.z_min <= sz <= self.z_max):
                    continue
                # source_frame(通常 body) -> map 平面坐标
                mx = tx + sx * cos_yaw - sy * sin_yaw
                my = ty + sx * sin_yaw + sy * cos_yaw
                pts.append((mx, my))
            n = len(pts)
            if n == 0:
                self._atomic_write_json(
                    LASER_FILE,
                    {"points": [], "updated_at": time.time()},
                )
                return
            step = 1
            if n > self.max_points:
                step = max(1, n // self.max_points)
            points = []
            for i in range(0, n, step):
                points.append({"x": round(pts[i][0], 3), "y": round(pts[i][1], 3)})
                if len(points) >= self.max_points:
                    break

            self._atomic_write_json(
                LASER_FILE,
                {"points": points, "updated_at": time.time()},
            )
        except Exception:
            pass

    def _cleanup_files(self):
        for f in (POSE_FILE, LASER_FILE):
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception:
                pass


if __name__ == "__main__":
    MappingH5Bridge()
    rospy.spin()
