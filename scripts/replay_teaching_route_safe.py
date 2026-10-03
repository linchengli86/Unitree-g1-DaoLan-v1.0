#!/usr/bin/env python3
"""Replay a recorded teaching route through move_base and the safe G1 bridge."""

import argparse
import math
import os
import signal
import subprocess
import sys
import threading
import time

import actionlib
import rospy
import tf
from actionlib_msgs.msg import GoalID, GoalStatus
from geometry_msgs.msg import Twist
from move_base_msgs.msg import MoveBaseAction, MoveBaseGoal
from nav_msgs.msg import Path
from std_srvs.srv import Empty, SetBool


class SafeRouteReplay:
    def __init__(self, args):
        self.args = args
        self.lock = threading.Lock()
        self.plan_received = False
        self.nonzero_cmd_received = False
        self.armed = False
        self.cutoff_process = None

        rospy.init_node("safe_teaching_route_replay", anonymous=True)
        self.listener = tf.TransformListener()
        self.client = actionlib.SimpleActionClient("/move_base", MoveBaseAction)
        self.cancel_pub = rospy.Publisher("/move_base/cancel", GoalID, queue_size=1)
        rospy.Subscriber(
            "/move_base/GlobalPlanner/plan", Path, self.plan_callback, queue_size=1
        )
        rospy.Subscriber("/cmd_vel", Twist, self.cmd_callback, queue_size=1)

        rospy.wait_for_service("/unitree_motion_enable", timeout=10.0)
        rospy.wait_for_service("/unitree_emergency_stop", timeout=10.0)
        self.enable_motion = rospy.ServiceProxy("/unitree_motion_enable", SetBool)
        self.emergency_stop = rospy.ServiceProxy("/unitree_emergency_stop", Empty)

    def plan_callback(self, msg):
        if msg.poses:
            with self.lock:
                self.plan_received = True

    def cmd_callback(self, msg):
        magnitude = max(abs(msg.linear.x), abs(msg.linear.y), abs(msg.angular.z))
        if magnitude > 0.005:
            with self.lock:
                self.nonzero_cmd_received = True

    @staticmethod
    def load_route(path):
        routes = []
        current = []
        with open(path, "r", encoding="utf-8") as route_file:
            for line_number, raw_line in enumerate(route_file, 1):
                line = raw_line.strip()
                if not line:
                    continue
                if line == "EOP":
                    if current:
                        routes.append(current)
                        current = []
                    continue
                fields = line.split()
                if len(fields) != 7:
                    raise ValueError(
                        "line {}: expected 7 values, got {}".format(
                            line_number, len(fields)
                        )
                    )
                values = [float(value) for value in fields]
                current.append((values[0], values[1]))
        if current:
            routes.append(current)
        if not routes or len(routes[0]) < 2:
            raise ValueError("route contains fewer than two valid poses")
        return routes[0]

    def current_pose(self):
        self.listener.waitForTransform(
            "map", "base_link", rospy.Time(0), rospy.Duration(5.0)
        )
        translation, rotation = self.listener.lookupTransform(
            "map", "base_link", rospy.Time(0)
        )
        return translation[0], translation[1], tf.transformations.euler_from_quaternion(rotation)[2]

    @staticmethod
    def distance(first, second):
        return math.hypot(first[0] - second[0], first[1] - second[1])

    def orient_and_sample(self, route, current):
        distance_to_start = self.distance(current, route[0])
        distance_to_end = self.distance(current, route[-1])
        nearest_index = min(
            range(len(route)), key=lambda index: self.distance(current, route[index])
        )
        distance_to_route = self.distance(current, route[nearest_index])

        if self.args.destination == "auto":
            nearest = min(distance_to_start, distance_to_end)
            if nearest > self.args.endpoint_tolerance:
                raise RuntimeError(
                    "robot is {:.2f} m from the nearest route endpoint; limit is {:.2f} m".format(
                        nearest, self.args.endpoint_tolerance
                    )
                )
            direction = "to-end"
            if distance_to_end < distance_to_start:
                route = list(reversed(route))
                direction = "to-start"
        else:
            nearest = distance_to_route
            if nearest > self.args.route_tolerance:
                raise RuntimeError(
                    "robot is {:.2f} m from the recorded route; limit is {:.2f} m".format(
                        nearest, self.args.route_tolerance
                    )
                )
            if self.args.destination == "start":
                route = list(reversed(route[: nearest_index + 1]))
                direction = "to-start"
            else:
                route = route[nearest_index:]
                direction = "to-end"

        if len(route) < 2:
            rospy.loginfo("Robot is already at destination=%s", self.args.destination)
            return []

        sampled_indices = []
        accumulated = 0.0
        for index in range(1, len(route)):
            accumulated += self.distance(route[index - 1], route[index])
            if accumulated >= self.args.spacing:
                sampled_indices.append(index)
                accumulated = 0.0
        if not sampled_indices or sampled_indices[-1] != len(route) - 1:
            sampled_indices.append(len(route) - 1)

        goals = []
        previous_target = route[0]
        for index in sampled_indices:
            target = route[index]
            # Use the direction between navigation goals, rather than the last
            # densely recorded segment.  This avoids demanding a sharp final
            # heading change at every intermediate waypoint.
            yaw = math.atan2(
                target[1] - previous_target[1],
                target[0] - previous_target[0],
            )
            goals.append((target[0], target[1], yaw))
            previous_target = target

        rospy.loginfo(
            "Route: %.2f m, direction=%s, sampled_goals=%d, endpoint_distance=%.2f m",
            sum(self.distance(route[i - 1], route[i]) for i in range(1, len(route))),
            direction,
            len(goals),
            nearest,
        )
        return goals

    @staticmethod
    def make_goal(x, y, yaw):
        goal = MoveBaseGoal()
        goal.target_pose.header.frame_id = "map"
        goal.target_pose.header.stamp = rospy.Time.now()
        goal.target_pose.pose.position.x = x
        goal.target_pose.pose.position.y = y
        goal.target_pose.pose.orientation.z = math.sin(yaw / 2.0)
        goal.target_pose.pose.orientation.w = math.cos(yaw / 2.0)
        return goal

    def cancel_all(self):
        try:
            self.client.cancel_all_goals()
            self.cancel_pub.publish(GoalID())
        except Exception:
            pass

    def disarm(self):
        try:
            response = self.enable_motion(False)
            rospy.loginfo("Motion disabled: %s", response.message)
        except Exception as exc:
            rospy.logerr("Failed to disable motion: %s", exc)
        self.armed = False

    def start_hard_cutoff(self):
        command = """
sleep {timeout}
source /opt/ros/noetic/setup.bash
source /home/unitree/robot/DaoLan/G1Nav2D/devel/setup.bash
rosservice call /unitree_emergency_stop \"{{}}\" >/dev/null 2>&1 || true
rostopic pub -1 /move_base/cancel actionlib_msgs/GoalID \
  \"{{stamp: {{secs: 0, nsecs: 0}}, id: ''}}\" >/dev/null 2>&1 || true
""".format(timeout=int(self.args.hard_timeout))
        self.cutoff_process = subprocess.Popen(
            ["bash", "-lc", command],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def stop_hard_cutoff(self):
        if self.cutoff_process and self.cutoff_process.poll() is None:
            try:
                os.killpg(os.getpgid(self.cutoff_process.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass

    def wait_for_preflight(self, timeout=8.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not rospy.is_shutdown():
            with self.lock:
                if self.plan_received and self.nonzero_cmd_received:
                    return True
            rospy.sleep(0.05)
        return False

    def run(self):
        route = self.load_route(self.args.route)
        current = self.current_pose()
        goals = self.orient_and_sample(route, current)

        if not self.client.wait_for_server(rospy.Duration(10.0)):
            raise RuntimeError("move_base action server is unavailable")

        self.disarm()
        self.cancel_all()
        rospy.sleep(0.5)

        if not goals:
            rospy.loginfo("No movement required")
            return

        with self.lock:
            self.plan_received = False
            self.nonzero_cmd_received = False

        first_goal = self.make_goal(*goals[0])
        self.client.send_goal(first_goal)
        rospy.loginfo("Preflight first goal: x=%.2f y=%.2f yaw=%.1f deg", goals[0][0], goals[0][1], math.degrees(goals[0][2]))
        if not self.wait_for_preflight():
            raise RuntimeError("no valid path/velocity received; refusing to enable motion")

        self.start_hard_cutoff()
        response = self.enable_motion(True)
        if not response.success:
            raise RuntimeError("motion enable rejected: {}".format(response.message))
        self.armed = True
        rospy.logwarn("Motion enabled; route replay started")

        for goal_index, goal_data in enumerate(goals):
            if goal_index > 0:
                self.client.send_goal(self.make_goal(*goal_data))
            rospy.loginfo(
                "Goal %d/%d: x=%.2f y=%.2f yaw=%.1f deg",
                goal_index + 1,
                len(goals),
                goal_data[0],
                goal_data[1],
                math.degrees(goal_data[2]),
            )
            if not self.client.wait_for_result(rospy.Duration(self.args.goal_timeout)):
                raise RuntimeError("goal {} timed out".format(goal_index + 1))
            state = self.client.get_state()
            if state != GoalStatus.SUCCEEDED:
                raise RuntimeError(
                    "goal {} failed: state={} text={}".format(
                        goal_index + 1, state, self.client.get_goal_status_text()
                    )
                )

        rospy.loginfo("Teaching route completed successfully")

    def shutdown(self, emergency=False):
        if emergency:
            try:
                self.emergency_stop()
            except Exception:
                pass
        else:
            self.disarm()
        self.cancel_all()
        self.stop_hard_cutoff()


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--route",
        default="/home/unitree/robot/DaoLan/routes/demo_route.txt",
    )
    parser.add_argument("--spacing", type=float, default=0.5)
    parser.add_argument(
        "--destination", choices=("auto", "start", "end"), default="auto"
    )
    parser.add_argument("--endpoint-tolerance", type=float, default=1.0)
    parser.add_argument("--route-tolerance", type=float, default=1.0)
    parser.add_argument("--goal-timeout", type=float, default=120.0)
    parser.add_argument("--hard-timeout", type=float, default=180.0)
    args = parser.parse_args(rospy.myargv(argv=sys.argv)[1:])
    if not 0.5 <= args.spacing <= 2.0:
        parser.error("--spacing must be between 0.5 and 2.0 m")
    return args


def main():
    args = parse_args()
    replay = None
    try:
        replay = SafeRouteReplay(args)
        replay.run()
        return 0
    except (Exception, rospy.ROSInterruptException) as exc:
        rospy.logerr("Route replay stopped: %s", exc)
        if replay:
            replay.shutdown(emergency=True)
        return 1
    finally:
        if replay:
            replay.shutdown(emergency=False)


if __name__ == "__main__":
    sys.exit(main())
