#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将 /projected_map (nav_msgs/OccupancyGrid) 写入运行时 JSON，供后端 WS 轮询广播。

输出文件:
  /home/ztx/robot/DaoLan/run/pids/nav_projected_map.json

默认使用 RLE 压缩 data，降低文件体积:
  payload["encoding"] = "rle"
  payload["data"] = [value0, count0, value1, count1, ...]
"""

import json
import math
import os
import time

import rospy
from nav_msgs.msg import OccupancyGrid


BASE_DIR = os.environ.get(
    "BASE_DIR",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")),
)
PID_DIR = os.environ.get("PID_DIR", os.path.join(BASE_DIR, "run", "pids"))
PROJECTED_MAP_FILE = os.path.join(PID_DIR, "nav_projected_map.json")


class ProjectedMapWsBridge:
    def __init__(self):
        rospy.init_node("projected_map_ws_bridge", anonymous=False)
        self.map_topic = rospy.get_param("~map_topic", "/projected_map")
        self.use_rle = bool(rospy.get_param("~use_rle", True))
        self.min_publish_interval = float(rospy.get_param("~min_publish_interval", 0.2))
        self.last_publish_ts = 0.0
        self.last_fingerprint = ""

        os.makedirs(PID_DIR, exist_ok=True)
        rospy.Subscriber(self.map_topic, OccupancyGrid, self._map_cb, queue_size=1)
        rospy.on_shutdown(self._cleanup_file)
        rospy.loginfo("projected_map_ws_bridge: topic=%s, output=%s", self.map_topic, PROJECTED_MAP_FILE)

    def _atomic_write_json(self, filepath, payload):
        tmp = filepath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, filepath)

    def _rle_encode(self, data):
        if not data:
            return []
        out = []
        prev = int(data[0])
        count = 1
        for v in data[1:]:
            vi = int(v)
            if vi == prev and count < 65535:
                count += 1
            else:
                out.append(prev)
                out.append(count)
                prev = vi
                count = 1
        out.append(prev)
        out.append(count)
        return out

    def _orientation_to_yaw(self, q):
        # yaw from quaternion (x,y,z,w)
        siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
        cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        return math.atan2(siny_cosp, cosy_cosp)

    def _fingerprint(self, msg, encoded_data_len):
        info = msg.info
        return f"{msg.header.seq}:{info.width}x{info.height}:{info.resolution:.6f}:{encoded_data_len}"

    def _map_cb(self, msg: OccupancyGrid):
        now = time.time()
        if now - self.last_publish_ts < self.min_publish_interval:
            return

        info = msg.info
        data = list(msg.data)
        encoded = self._rle_encode(data) if self.use_rle else [int(v) for v in data]
        fp = self._fingerprint(msg, len(encoded))
        if fp == self.last_fingerprint:
            return

        origin = info.origin
        payload = {
            "width": int(info.width),
            "height": int(info.height),
            "resolution": float(info.resolution),
            "origin": [
                float(origin.position.x),
                float(origin.position.y),
                float(origin.position.z),
            ],
            "origin_yaw": float(self._orientation_to_yaw(origin.orientation)),
            "encoding": "rle" if self.use_rle else "raw",
            "data": encoded,
            "updated_at": now,
            "frame_id": msg.header.frame_id or "map",
        }
        self._atomic_write_json(PROJECTED_MAP_FILE, payload)
        self.last_publish_ts = now
        self.last_fingerprint = fp

    def _cleanup_file(self):
        try:
            if os.path.exists(PROJECTED_MAP_FILE):
                os.remove(PROJECTED_MAP_FILE)
        except Exception:
            pass


if __name__ == "__main__":
    ProjectedMapWsBridge()
    rospy.spin()
