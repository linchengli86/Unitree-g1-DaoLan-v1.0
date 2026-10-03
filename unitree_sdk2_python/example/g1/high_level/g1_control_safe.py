#!/usr/bin/env python3
"""Fail-safe bridge from ROS velocity commands to the Unitree G1 SDK.

The controller starts disarmed.  Motion is possible only after the
``/unitree_motion_enable`` service is called with ``data: true`` and a
non-empty global plan has been received.  Any stale/zero command, empty plan,
explicit disable, emergency stop, or normal process shutdown sends StopMove.
"""

import sys
import threading

import rospy
from geometry_msgs.msg import Twist
from nav_msgs.msg import Path
from std_srvs.srv import Empty, EmptyResponse, SetBool, SetBoolResponse

from unitree_sdk2py.core.channel import ChannelFactoryInitialize
from unitree_sdk2py.g1.loco.g1_loco_client import LocoClient


class SafeCmdVelController:
    def __init__(self, network_interface):
        self.lock = threading.RLock()

        self.cmd_topic = rospy.get_param("~cmd_topic", "/cmd_vel_smooth")
        self.path_topic = rospy.get_param(
            "~path_topic", "/move_base/GlobalPlanner/plan"
        )
        self.max_vx = abs(float(rospy.get_param("~max_vx", 0.60)))
        self.max_vy = abs(float(rospy.get_param("~max_vy", 0.0)))
        self.max_wz = abs(float(rospy.get_param("~max_wz", 0.70)))
        self.watchdog_timeout = max(
            0.1, float(rospy.get_param("~watchdog_timeout", 0.30))
        )
        self.zero_epsilon = abs(float(rospy.get_param("~zero_epsilon", 0.005)))

        self.armed = False
        self.has_path = False
        self.moving = False
        self.last_cmd_time = None

        rospy.loginfo("Initializing Unitree LocoClient on %s", network_interface)
        ChannelFactoryInitialize(0, network_interface)
        self.sport_client = LocoClient()
        self.sport_client.SetTimeout(2.0)
        self.sport_client.Init()

        self.cmd_sub = rospy.Subscriber(
            self.cmd_topic, Twist, self.cmd_vel_callback, queue_size=1
        )
        self.path_sub = rospy.Subscriber(
            self.path_topic, Path, self.path_callback, queue_size=1
        )
        self.enable_service = rospy.Service(
            "/unitree_motion_enable", SetBool, self.enable_callback
        )
        self.emergency_service = rospy.Service(
            "/unitree_emergency_stop", Empty, self.emergency_stop_callback
        )
        self.watchdog_timer = rospy.Timer(
            rospy.Duration(0.05), self.watchdog_callback
        )
        rospy.on_shutdown(self.shutdown_callback)

        self.stop_robot("startup", force=True)
        rospy.logwarn(
            "Safe controller ready but DISARMED. Enable with: "
            "rosservice call /unitree_motion_enable 'data: true'"
        )
        rospy.loginfo(
            "Input=%s limits: vx=%.2f m/s, vy=%.2f m/s, wz=%.2f rad/s, "
            "watchdog=%.2f s",
            self.cmd_topic,
            self.max_vx,
            self.max_vy,
            self.max_wz,
            self.watchdog_timeout,
        )

    @staticmethod
    def clamp(value, limit):
        if limit <= 0.0:
            return 0.0
        return max(-limit, min(limit, value))

    def stop_robot(self, reason, force=False):
        with self.lock:
            if not force and not self.moving:
                return
            try:
                self.sport_client.StopMove()
                rospy.logwarn("StopMove sent (%s)", reason)
            except Exception as exc:
                rospy.logerr("StopMove failed (%s): %s", reason, exc)
            finally:
                self.moving = False

    def path_callback(self, msg):
        with self.lock:
            self.has_path = bool(msg.poses)
            if not self.has_path:
                self.stop_robot("global path empty")

    def cmd_vel_callback(self, msg):
        now = rospy.Time.now()
        vx = self.clamp(msg.linear.x, self.max_vx)
        vy = self.clamp(msg.linear.y, self.max_vy)
        wz = self.clamp(msg.angular.z, self.max_wz)

        with self.lock:
            self.last_cmd_time = now

            if not self.armed:
                self.stop_robot("controller disarmed")
                return
            if not self.has_path:
                self.stop_robot("no global path")
                rospy.logwarn_throttle(2.0, "Ignoring velocity: no global path")
                return
            if max(abs(vx), abs(vy), abs(wz)) <= self.zero_epsilon:
                self.stop_robot("zero velocity command")
                return

            try:
                self.sport_client.Move(vx, vy, wz)
                self.moving = True
                rospy.loginfo_throttle(
                    1.0, "Safe cmd: vx=%.3f vy=%.3f wz=%.3f", vx, vy, wz
                )
            except Exception as exc:
                rospy.logerr("Move command failed: %s", exc)
                self.stop_robot("Move command failure", force=True)

    def watchdog_callback(self, _event):
        with self.lock:
            if not self.moving:
                return
            if self.last_cmd_time is None:
                self.stop_robot("no velocity timestamp", force=True)
                return
            age = (rospy.Time.now() - self.last_cmd_time).to_sec()
            if age > self.watchdog_timeout:
                self.stop_robot(
                    "velocity watchdog timeout ({:.3f}s)".format(age), force=True
                )

    def enable_callback(self, request):
        with self.lock:
            self.armed = bool(request.data)
            if not self.armed:
                self.stop_robot("motion disabled", force=True)
                return SetBoolResponse(success=True, message="motion disabled; StopMove sent")
            self.last_cmd_time = rospy.Time.now()
            rospy.logwarn("Motion ENABLED; waiting for a valid global path and velocity")
            return SetBoolResponse(success=True, message="motion enabled")

    def emergency_stop_callback(self, _request):
        with self.lock:
            self.armed = False
            self.has_path = False
            self.stop_robot("EMERGENCY STOP", force=True)
        return EmptyResponse()

    def shutdown_callback(self):
        self.armed = False
        self.has_path = False
        self.stop_robot("controller shutdown", force=True)


def main():
    if len(sys.argv) != 2:
        print("Usage: g1_control_safe.py <network_interface>")
        return 2

    rospy.init_node("unitree_safe_controller", anonymous=False)
    try:
        SafeCmdVelController(sys.argv[1])
        rospy.spin()
    except Exception as exc:
        rospy.logfatal("Safe controller failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
