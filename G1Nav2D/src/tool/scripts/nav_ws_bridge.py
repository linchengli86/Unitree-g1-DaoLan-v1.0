#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ROS -> WS bridge data producer for navigation map (G1):
- robot pose (map frame)
- laser/radar points (converted to map frame)

G1 差异（相对 Go2）：
- 雷达倒置安装：倒置由 navigation TF（body↔base_link）与 SLAM 处理
- 默认订 /local_cloud（与 RViz localize.rviz 一致，frame=local）
- /local_cloud 已是 local 系点云，默认不做点级 y/z 翻转；
  若改回 /body_cloud，需显式 _lidar_inverted:=true
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


class NavWsBridge:
    def __init__(self):
        rospy.init_node("nav_ws_bridge", anonymous=True)
        self.tf_listener = tf.TransformListener()
        self.map_frame = rospy.get_param("~map_frame", "map")
        # 与 RViz Axes(base_link) 对齐；G1 上 body 相对 base_link 有倒置 TF，用 body 会导致箭头偏转
        self.body_frame = rospy.get_param("~body_frame", "base_link")
        # RViz localize.rviz 实时点云
        self.cloud_topic = rospy.get_param("~cloud_topic", "/local_cloud")
        self.max_points = int(rospy.get_param("~max_points", 1200))
        # 扫描高度窗（local 系，重力对齐后的高度）
        self.z_min = float(rospy.get_param("~z_min", -10.0))
        self.z_max = float(rospy.get_param("~z_max", 1.0))
        # local_cloud 原点不是机器人，默认不过滤近距；body_cloud 时可设 0.25
        self.min_range = float(rospy.get_param("~min_range", 0.0))
        self.grid_cell = float(rospy.get_param("~grid_cell", 0.05))
        # G1 倒置：仅 body 系原始点需要点级翻转。local_cloud 默认 false。
        default_invert = "body_cloud" in self.cloud_topic
        self.lidar_inverted = bool(rospy.get_param("~lidar_inverted", default_invert))
        self.color_by = rospy.get_param("~color_by", "intensity")
        self.pose = None
        self.last_pose_write = 0.0
        self._last_tf_warn_ts = 0.0

        os.makedirs(PID_DIR, exist_ok=True)
        rospy.loginfo(
            "nav_ws_bridge(G1): cloud=%s pose_frame=%s z_range=[%.2f, %.2f] min_range=%.2f "
            "grid=%.3f max_points=%d lidar_inverted=%s color_by=%s",
            self.cloud_topic,
            self.body_frame,
            self.z_min,
            self.z_max,
            self.min_range,
            self.grid_cell,
            self.max_points,
            str(self.lidar_inverted),
            self.color_by,
        )

        rospy.Subscriber(self.cloud_topic, PointCloud2, self._cloud_cb, queue_size=1)
        rospy.Timer(rospy.Duration(0.2), self._pose_timer)
        rospy.on_shutdown(self._cleanup_files)

    def _atomic_write_json(self, filepath, payload):
        tmp = filepath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, filepath)

    def _pose_timer(self, _evt):
        try:
            try:
                trans, rot = self.tf_listener.lookupTransform(
                    self.map_frame, self.body_frame, rospy.Time(0)
                )
            except Exception:
                # base_link 暂不可用时回退 body
                trans, rot = self.tf_listener.lookupTransform(
                    self.map_frame, "body", rospy.Time(0)
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

    def _spatial_downsample(self, pts):
        """按平面栅格保留一点。pts: (mx, my, sz, intensity)"""
        if not pts:
            return []
        cell = max(self.grid_cell, 0.01)
        inv = 1.0 / cell
        grid = {}
        for mx, my, sz, inten in pts:
            key = (int(math.floor(mx * inv)), int(math.floor(my * inv)))
            if key not in grid:
                grid[key] = (mx, my, sz, inten)
                if len(grid) >= self.max_points * 2:
                    break
        keys = list(grid.keys())
        n = len(keys)
        if n <= self.max_points:
            selected = keys
        else:
            step = max(1, n // self.max_points)
            selected = keys[::step][: self.max_points]
        return [
            {
                "x": round(grid[k][0], 3),
                "y": round(grid[k][1], 3),
                "z": round(grid[k][2], 3),
                "i": round(grid[k][3], 2),
            }
            for k in selected
        ]

    def _cloud_cb(self, msg: PointCloud2):
        try:
            tx, ty, tf_yaw = self._lookup_2d_tf(msg.header.frame_id, msg.header.stamp)
            cos_tf = math.cos(tf_yaw)
            sin_tf = math.sin(tf_yaw)
        except Exception:
            now = time.time()
            if now - self._last_tf_warn_ts > 2.0:
                rospy.logwarn(
                    "nav_ws_bridge: TF unavailable for %s -> %s",
                    msg.header.frame_id,
                    self.map_frame,
                )
                self._last_tf_warn_ts = now
            return
        try:
            min_r2 = self.min_range * self.min_range
            pts = []
            for x, y, z, intensity in point_cloud2.read_points(
                msg, field_names=("x", "y", "z", "intensity"), skip_nans=True
            ):
                sx = float(x)
                sy = float(y)
                sz = float(z)
                inten = float(intensity)
                # 仅 body 系原始点需要：G1 倒置雷达的 y/z 取反
                if self.lidar_inverted:
                    sy = -sy
                    sz = -sz
                if not (self.z_min <= sz <= self.z_max):
                    continue
                if self.min_range > 0 and (sx * sx + sy * sy) < min_r2:
                    continue
                mx = tx + sx * cos_tf - sy * sin_tf
                my = ty + sx * sin_tf + sy * cos_tf
                pts.append((mx, my, sz, inten))

            points = self._spatial_downsample(pts)
            i_vals = [p["i"] for p in points] if points else []
            payload = {
                "points": points,
                "z_min": self.z_min,
                "z_max": self.z_max,
                "color_by": self.color_by,
                "updated_at": time.time(),
            }
            if i_vals:
                payload["i_min"] = min(i_vals)
                payload["i_max"] = max(i_vals)
            self._atomic_write_json(LASER_FILE, payload)
        except Exception:
            # intensity 字段缺失时回退 xyz
            try:
                min_r2 = self.min_range * self.min_range
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
                    if self.min_range > 0 and (sx * sx + sy * sy) < min_r2:
                        continue
                    mx = tx + sx * cos_tf - sy * sin_tf
                    my = ty + sx * sin_tf + sy * cos_tf
                    pts.append((mx, my, sz, 0.0))
                points = self._spatial_downsample(pts)
                self._atomic_write_json(
                    LASER_FILE,
                    {
                        "points": points,
                        "z_min": self.z_min,
                        "z_max": self.z_max,
                        "color_by": "z",
                        "updated_at": time.time(),
                    },
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
    bridge = NavWsBridge()
    rospy.spin()
