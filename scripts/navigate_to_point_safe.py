#!/usr/bin/env python3
"""Navigate to one registered guide point; never accepts arbitrary coordinates.

Run with the system ROS Python after sourcing Noetic and the workspace. The
caller must already have obtained operator authorization for an actual move.
Check/verify modes only read ROS state: they do not publish goals, cancel goals,
or change the motion-enable service. ROS imports are deliberately lazy.

The SSH-only --commissioning-confirmed option is an explicit operator-owned
first field trial, not a production verification flag. It uses all normal
preflight, owned-plan, watchdog and arrival checks; it never changes the web
gate or grants Omni permission to run subsequent navigation tasks.
"""

import argparse
import json
import math
import os
import re
import signal
import sys
import threading
import time
import urllib.error
import urllib.request

from guide_points import PROJECT, GuidePointStore
from navigation_readiness import navigation_tf_limit, validate_navigation_odom, validate_tf_age
from navigation_thread_budget import apply_current_process

XY_TOLERANCE = 0.20
YAW_TOLERANCE = 0.20
ACTIVE_STATES = {0, 1, 6, 7}  # PENDING, ACTIVE, PREEMPTING, RECALLING
TERMINAL_FAILURES = {2, 4, 5, 8, 9}
SETTLING_TIMEOUT = 8.0
MOVING_CAPTURE_ERROR = "机器人或定位仍在移动，请停稳后重新添加"


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_target(point):
    if not isinstance(point, dict) or not re.fullmatch(r"[0-9a-f]{32}", str(point.get("id", ""))):
        raise ValueError("导览点编号无效")
    if point.get("frame_id") != "map":
        raise ValueError("导览点不在 map 坐标系")
    if not re.fullmatch(r"[0-9a-f]{64}", str(point.get("map_fingerprint", ""))):
        raise ValueError("导览点缺少有效地图指纹")
    pose = point.get("pose")
    if not isinstance(pose, dict) or set(pose) != {"x", "y", "z", "yaw"} or not all(
        finite_number(value) for value in pose.values()
    ):
        raise ValueError("导览点位姿无效")
    if abs(pose["z"]) > 0.01:
        raise ValueError("导览点必须是二维地面导航位置")
    return {**point, "pose": {**pose, "yaw": math.atan2(math.sin(pose["yaw"]), math.cos(pose["yaw"]))}}


def load_target(store, point_id):
    if not isinstance(point_id, str) or not re.fullmatch(r"[0-9a-f]{32}", point_id):
        raise ValueError("只能使用已登记的导览点编号，不能输入任意坐标")
    matches = [point for point in store.list() if point.get("id") == point_id]
    if len(matches) != 1:
        raise ValueError("导览点不存在或编号重复；未发送导航目标")
    return validate_target(matches[0])


def arrival_result(target, actual):
    for pose in (target, actual):
        if not isinstance(pose, dict) or any(not finite_number(pose.get(key)) for key in ("x", "y", "yaw")):
            raise ValueError("到达验证位姿无效")
    xy_error = math.hypot(actual["x"] - target["x"], actual["y"] - target["y"])
    yaw_error = abs(math.atan2(math.sin(actual["yaw"] - target["yaw"]), math.cos(actual["yaw"] - target["yaw"])))
    return {"arrived": xy_error <= XY_TOLERANCE and yaw_error <= YAW_TOLERANCE,
            "position_error_m": xy_error, "yaw_error_rad": yaw_error,
            "position_tolerance_m": XY_TOLERANCE, "yaw_tolerance_rad": YAW_TOLERANCE,
            "achieved_pose": actual}


def validate_capture(point, capture):
    if capture.get("localized") is not True or capture.get("frame_id") != "map":
        raise RuntimeError("定位状态无效，拒绝导航")
    if capture.get("map_fingerprint") != point["map_fingerprint"]:
        raise RuntimeError("导览点与当前地图版本不一致，请复核点位")
    age = capture.get("tf_age_seconds")
    if not finite_number(age) or not 0 <= age <= 1.5:
        raise RuntimeError("TF 已过期，拒绝导航")
    arrival_result(point["pose"], capture.get("pose"))
    return capture


def grid_cell(point, grid):
    """Validate a registered target against a live map, including rotated origins."""
    if grid.header.frame_id != "map":
        raise RuntimeError("导航地图不在 map 坐标系")
    info = grid.info
    if not finite_number(info.resolution) or info.resolution <= 0 or info.width <= 0 or info.height <= 0:
        raise RuntimeError("导航地图尺寸无效")
    origin = info.origin
    values = (origin.position.x, origin.position.y, origin.orientation.x, origin.orientation.y,
              origin.orientation.z, origin.orientation.w)
    if not all(finite_number(value) for value in values):
        raise RuntimeError("导航地图原点无效")
    q = origin.orientation
    if abs(q.x) > 1e-6 or abs(q.y) > 1e-6 or abs(q.z * q.z + q.w * q.w - 1) > 0.01:
        raise RuntimeError("导航地图原点姿态不是有效二维旋转")
    yaw = 2 * math.atan2(q.z, q.w)
    dx = point["x"] - origin.position.x
    dy = point["y"] - origin.position.y
    x = int(math.floor((math.cos(yaw) * dx + math.sin(yaw) * dy) / info.resolution))
    y = int(math.floor((-math.sin(yaw) * dx + math.cos(yaw) * dy) / info.resolution))
    if not (0 <= x < info.width and 0 <= y < info.height):
        raise RuntimeError("导览点位于导航地图边界外")
    if len(grid.data) != info.width * info.height:
        raise RuntimeError("导航地图数据长度无效")
    occupancy = grid.data[y * info.width + x]
    if occupancy < 0 or occupancy >= 65:
        raise RuntimeError("导览点在未知区域或障碍物内，拒绝导航")
    return x, y


def bounded_call(callback, timeout, cancel=None):
    """Bound service calls even if a ROS TCP peer stops responding."""
    if cancel is not None and cancel.is_set():
        raise InterruptedError("导览点导航已取消")
    completed = threading.Event()
    result = []

    def invoke():
        try:
            result.append((True, callback()))
        except Exception as exc:
            result.append((False, exc))
        finally:
            completed.set()

    threading.Thread(target=invoke, name="point-navigation-call", daemon=True).start()
    deadline = time.monotonic() + timeout
    while not completed.wait(min(0.05, max(0, deadline - time.monotonic()))):
        if cancel is not None and cancel.is_set():
            raise InterruptedError("导览点导航已取消")
        if time.monotonic() >= deadline:
            raise TimeoutError("导航服务响应超时")
    if cancel is not None and cancel.is_set():
        raise InterruptedError("导览点导航已取消")
    if not result[0][0]:
        raise result[0][1]
    return result[0][1]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("本机讲解服务不能重定向到其他地址")


class PresentationClient:
    """Hand off speech-only work to the existing local web task executor.

    This never submits a navigation action or opens a deployment gate. The
    executor checks physical arrival again both before and after conception.
    A submitted task is not a claim that conception or playback succeeded.
    """

    def __init__(self, cancel):
        port = int(os.environ.get("MOBILE_GUIDE_PORT", "8765"))
        if not 1 <= port <= 65535:
            raise ValueError("导览网页端口无效")
        self.address = "http://127.0.0.1:{}".format(port)
        self.cancel = cancel
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        self.preparation_id = None

    def _request(self, path, payload=None):
        if self.cancel.is_set():
            raise InterruptedError("导览点导航已取消；未提交讲解")
        request = urllib.request.Request(self.address + path,
            data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=5) as response:
                packet = response.read(65537)
        except urllib.error.HTTPError as exc:
            with exc:
                packet = exc.read(65537)
        if len(packet) > 65536:
            raise RuntimeError("本机讲解服务响应过大")
        result = json.loads(packet.decode("utf-8"))
        if not isinstance(result, dict) or result.get("status") != "success":
            raise RuntimeError((result if isinstance(result, dict) else {}).get("message") or "本机讲解服务未就绪")
        return result

    def ensure_ready(self):
        if self._request("/api/health").get("omni") is not True:
            raise RuntimeError("Omni 未配置；本次未发送导航目标")
        task = self._request("/api/tasks/current").get("task")
        if task and task.get("state") not in {"succeeded", "failed", "cancelled", "simulated", "interrupted"}:
            raise RuntimeError("网页已有任务，拒绝提交另一个导览任务")

    def prepare(self, point_id):
        """Pure computation overlaps navigation; no audio or motion request."""
        self.ensure_ready()
        result = self._request("/api/agent/prefetch", {"point_id": point_id, "topic": ""})
        prepared = result.get("preparation", {})
        if not re.fullmatch(r"[0-9a-f]{32}", str(prepared.get("id", ""))):
            raise RuntimeError("讲解预构思回执异常；本次未导航")
        self.preparation_id = prepared["id"]

    def cancel_preparation(self):
        if self.preparation_id is None:
            return
        # Cancelling calculation must also work after the navigation's cancel
        # event is set. It can never cancel a robot goal or emit audio.
        request = urllib.request.Request(self.address + "/api/agent/prefetch/cancel",
            data=json.dumps({"id": self.preparation_id}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=2) as response:
                response.read(65537)
        except Exception:
            pass  # Server stop/expiry also cancels unconsumed computation.
        self.preparation_id = None

    def submit(self, point_id):
        self.ensure_ready()
        plan = {"title": "到达后动态讲解", "steps": [
            {"action": "present_point", "parameters": {"point_id": point_id, "topic": ""}, "timeout": 240}]}
        payload = {"plan": plan}
        if self.preparation_id is not None:
            payload["presentation_ticket"] = self.preparation_id
        prepared = self._request("/api/plans/prepare", payload)
        if prepared.get("plan", {}).get("steps") != plan["steps"] or not re.fullmatch(
                r"[0-9a-f]{32}", str(prepared.get("confirmation", ""))):
            raise RuntimeError("讲解任务契约异常；未确认执行")
        accepted = self._request("/api/plans/confirm", {"token": prepared["confirmation"]})
        task = accepted.get("task", {})
        if task.get("plan", {}).get("steps") != plan["steps"] or not re.fullmatch(
                r"[0-9a-f]{32}", str(task.get("id", ""))):
            raise RuntimeError("讲解任务回执异常，请在网页检查任务状态")
        self.preparation_id = None  # The accepted task now owns consumption.
        return {"status": "submitted", "task_id": task["id"], "completion_verified": False,
                "message": "运动已禁用；动态讲解任务已提交，请在网页查看构思与播报结果"}


class PointNavigator:
    """Hardware-independent policy, with explicit mutation ownership."""

    def __init__(self, backend, point, timeout=150, cancel=None, clock=time.monotonic, sleep=time.sleep):
        if not finite_number(timeout) or not 1 <= timeout <= 240:
            raise ValueError("导航超时须为 1–240 秒")
        self.backend = backend
        self.point = validate_target(point)
        self.timeout = timeout
        self.cancel = cancel if cancel is not None else threading.Event()
        self.clock, self.sleep = clock, sleep

    def _settled_capture(self, deadline):
        # StopMove is acknowledged before this read-only wait. A transient
        # moving sample is not an arrival failure, but stale TF, lost
        # localization or changed maps must still fail immediately.
        settle_deadline = min(deadline, self.clock() + SETTLING_TIMEOUT)
        while True:
            if self.cancel.is_set():
                raise InterruptedError("导览点导航已取消")
            remaining = settle_deadline - self.clock()
            if remaining <= 0:
                raise TimeoutError("停稳核验超时，运动保持禁用；请检查机器人或定位漂移")
            try:
                capture = bounded_call(self.backend.stationary_capture, remaining, self.cancel)
                if self.clock() >= settle_deadline:
                    raise TimeoutError("停稳核验超时，运动保持禁用")
                return validate_capture(self.point, capture)
            except ValueError as exc:
                if str(exc) != MOVING_CAPTURE_ERROR:
                    raise
                remaining = settle_deadline - self.clock()
                if remaining > 0:
                    self.sleep(min(0.25, remaining))

    def run(self, check_only=False, verify_only=False):
        if check_only and verify_only:
            raise ValueError("预检与到达验证不能同时启用")
        deadline = self.clock() + self.timeout
        issued = False
        motion_touched = False
        disabled = False
        result = None
        failure = None
        try:
            capture = validate_capture(self.point, self.backend.preflight(self.point, deadline, verify_only, check_only))
            if self.cancel.is_set():
                raise InterruptedError("导览点导航已取消")
            if self.clock() >= deadline:
                raise TimeoutError("导览点预检或验证超时")
            if verify_only:
                verified = arrival_result(self.point["pose"], capture["pose"])
                if not verified["arrived"]:
                    raise RuntimeError("机器人尚未到达导览点的位置或朝向")
                return {"status": "success", "mode": "verify_only", "point_id": self.point["id"],
                        "verified": True, **verified}
            if check_only:
                return {"status": "success", "mode": "check_only", "point_id": self.point["id"],
                        "path_feasibility_checked": False,
                        "message": "只读预检通过；未规划路径、未发送目标、未使能或禁用运动"}

            # Establish disarmed state before submitting this task's goal. Never
            # issue the goal if a concurrent external navigation appeared.
            self.backend.ensure_idle()
            motion_touched = True
            self.backend.set_motion(False)
            latest = validate_capture(self.point, self.backend.quick_capture(self.point))
            verified = arrival_result(self.point["pose"], latest["pose"])
            if not verified["arrived"]:
                self.backend.ensure_idle()
                if self.clock() >= deadline:
                    raise TimeoutError("导览点导航超时，未发送目标")
                issued = True  # send_goal may raise after publishing; cancel remains necessary.
                self.backend.send_goal(self.point["pose"])
                self.backend.wait_owned_plan(min(deadline, self.clock() + 6))
                validate_capture(self.point, self.backend.quick_capture(self.point))
                if self.cancel.is_set():
                    raise InterruptedError("导览点导航已取消")
                self.backend.ensure_owned()
                if self.clock() >= deadline:
                    raise TimeoutError("导览点导航超时，未使能运动")
                self.backend.set_motion(True)
                best = float("inf")
                progress_at = self.clock()
                while True:
                    if self.cancel.is_set():
                        raise InterruptedError("导览点导航已取消")
                    if self.clock() >= deadline:
                        raise TimeoutError("导览点导航超时，已请求停止")
                    self.backend.ensure_owned()
                    fresh = validate_capture(self.point, self.backend.quick_capture(self.point))
                    state = self.backend.goal_state()
                    if state == 3:  # actionlib_msgs/GoalStatus.SUCCEEDED
                        break
                    if state in TERMINAL_FAILURES:
                        raise RuntimeError("导航未成功：" + self.backend.goal_text())
                    errors = arrival_result(self.point["pose"], fresh["pose"])
                    metric = errors["position_error_m"] + 0.15 * errors["yaw_error_rad"]
                    if metric < best - 0.04:
                        best, progress_at = metric, self.clock()
                    if self.clock() - progress_at > 45:
                        raise TimeoutError("45 秒内未取得导航进展，已请求停止")
                    self.sleep(0.20)

            self.backend.set_motion(False)
            disabled = True
            fresh = self._settled_capture(deadline)
            verified = arrival_result(self.point["pose"], fresh["pose"])
            if not verified["arrived"]:
                raise RuntimeError("导航结果未通过实际位置和朝向验证：位置误差 {:.3f} m，"
                                   "朝向误差 {:.1f}°；运动保持禁用".format(
                                       verified["position_error_m"], math.degrees(verified["yaw_error_rad"])))
            result = {"status": "success", "mode": "navigate", "point_id": self.point["id"],
                      "motion_disabled": True, "verified": True, **verified}
        except Exception as exc:
            failure = exc
        finally:
            # No mutations whatsoever occur in check/verify modes.
            if issued:
                try:
                    self.backend.cancel_owned_goal()
                except Exception as exc:
                    if failure is None:
                        failure = RuntimeError("未确认本任务目标取消：" + str(exc))
            if motion_touched and not disabled:
                try:
                    self.backend.set_motion(False)
                except Exception as exc:
                    failure = RuntimeError("未确认运动禁用，请操作员立即停止机器人：" + str(exc))
        if failure is not None:
            raise failure
        return result


class RosBackend:
    def __init__(self, cancel):
        # This backend runs in its own helper process. Limit BLAS workers
        # before tf imports NumPy; do not apply this to the ROS-launch parent.
        apply_current_process()
        import actionlib
        import rospy
        import tf
        from geometry_msgs.msg import PoseStamped
        from move_base_msgs.msg import MoveBaseAction
        from nav_msgs.msg import Odometry, Path
        from std_srvs.srv import SetBool

        self.rospy, self.tf, self.PoseStamped = rospy, tf, PoseStamped
        self.cancel = cancel
        rospy.init_node("navigate_registered_guide_point", anonymous=True, disable_signals=True)
        self.listener = tf.TransformListener()
        self.client = actionlib.SimpleActionClient("/move_base", MoveBaseAction)
        self.enable = rospy.ServiceProxy("/unitree_motion_enable", SetBool)
        self.localization_check = None
        self.tf_max_age = 1.5  # verify-only keeps the existing capture policy.
        self.navigation_feedback_required = False
        self.odom_lock = threading.Lock()
        self.navigation_odom = None
        self.odom_sub = rospy.Subscriber("/navigation_odom", Odometry, self._odom, queue_size=1)
        self.goal_id = None
        self.goal_pose = None
        self.goal_sent_at = None
        self.plans = []
        self.plan_lock = threading.Lock()
        plan_topic = rospy.get_param("/unitree_safe_controller/path_topic", "/move_base/GlobalPlanner/plan")
        self.plan_sub = rospy.Subscriber(plan_topic, Path, self._plan, queue_size=1)
        self.store = GuidePointStore(PROJECT / "config" / "guide_points.json", pose_provider=self._capture)
        self.deadline = None

    def _read(self, callback, timeout=3):
        if self.deadline is not None:
            timeout = min(timeout, self.deadline - time.monotonic())
        if timeout <= 0:
            raise TimeoutError("导览点导航或验证超时")
        return bounded_call(callback, timeout, self.cancel)

    def _capture(self):
        from capture_guide_pose import capture
        return self._read(lambda: capture(init_ros=False, listener=self.listener), 10)

    def stationary_capture(self):
        return self.store.read_pose()

    def _plan(self, msg):
        with self.plan_lock:
            self.plans = [(time.monotonic(), msg)]

    def _odom(self, msg):
        with self.odom_lock:
            self.navigation_odom = msg

    def _navigation_freshness(self):
        self._fresh_pose()
        if self.navigation_feedback_required:
            with self.odom_lock:
                message = self.navigation_odom
            validate_navigation_odom(message, time.time(), self.tf_max_age)

    def _status(self):
        from actionlib_msgs.msg import GoalStatusArray
        return self._read(lambda: self.rospy.wait_for_message("/move_base/status", GoalStatusArray, timeout=2)).status_list

    def ensure_idle(self):
        if any(status.status in ACTIVE_STATES for status in self._status()):
            raise RuntimeError("move_base 已有其他活动导航任务，拒绝抢占")

    def ensure_owned(self):
        if not self.goal_id:
            raise RuntimeError("无法确认本任务导航目标所有权")
        if any(status.status in ACTIVE_STATES and status.goal_id.id != self.goal_id for status in self._status()):
            raise RuntimeError("检测到其他导航任务，本任务立即停止")

    def _pose_message(self, pose):
        message = self.PoseStamped()
        message.header.frame_id = "map"
        message.header.stamp = self.rospy.Time.now()
        message.pose.position.x, message.pose.position.y = pose["x"], pose["y"]
        message.pose.orientation.z = math.sin(pose["yaw"] / 2)
        message.pose.orientation.w = math.cos(pose["yaw"] / 2)
        return message

    def _scan(self):
        from sensor_msgs.msg import LaserScan
        scan = self._read(lambda: self.rospy.wait_for_message("/scan", LaserScan, timeout=2))
        age = time.time() - scan.header.stamp.to_sec()
        if not -0.1 <= age <= 1.5 or not scan.ranges or not finite_number(scan.range_max) or scan.range_max <= 0:
            raise RuntimeError("避障激光数据为空或已过期")

    @staticmethod
    def _map_signatures():
        import yaml
        path = PROJECT / "map" / "map.yaml"
        config = yaml.safe_load(path.read_text())
        image = (path.parent / config["image"]).resolve()
        image.relative_to(PROJECT.resolve())
        files = [path, image, PROJECT / "G1Nav2D" / "src" / "fastlio2" / "PCD" / "map.pcd"]
        return tuple((str(item), item.stat().st_size, item.stat().st_mtime_ns, item.stat().st_ctime_ns) for item in files)

    def preflight(self, point, deadline, verify_only=False, check_only=False):
        import rosnode
        from nav_msgs.msg import OccupancyGrid
        from nav_msgs.srv import GetPlan

        self.deadline = deadline
        if self.rospy.get_param("/use_sim_time", False):
            raise RuntimeError("不能使用仿真时间执行实机导航")
        if not verify_only:
            self.tf_max_age = navigation_tf_limit(
                self.rospy.get_param("/move_base/global_costmap/transform_tolerance", None),
                self.rospy.get_param("/move_base/local_costmap/transform_tolerance", None))
            topic = self.rospy.get_param("/move_base/TebLocalPlannerROS/odom_topic", None)
            if topic not in ("navigation_odom", "/navigation_odom"):
                raise RuntimeError("规划器尚未接入 /navigation_odom 实际速度反馈；拒绝导航")
            self.navigation_feedback_required = True
        nodes = self._read(rosnode.get_node_names)
        required = {"/localizer_node"} if verify_only else {"/localizer_node", "/move_base", "/unitree_safe_controller"}
        if not required.issubset(nodes):
            raise RuntimeError("缺少导航或安全控制节点：" + ", ".join(sorted(required - set(nodes))))
        before = self._map_signatures()
        capture = self.stationary_capture()
        validate_capture(point, capture)
        self.signatures = self._map_signatures()
        if before != self.signatures:
            raise RuntimeError("预检期间地图发生变化，请重新验证导览点")
        if verify_only:
            return capture
        self._navigation_freshness()
        self.ensure_idle()
        if not self._read(lambda: self.client.wait_for_server(self.rospy.Duration(3)), 4):
            raise RuntimeError("move_base 导航接口不可用")
        self._read(lambda: self.rospy.wait_for_service("/unitree_motion_enable", timeout=2))
        self._scan()
        map_topic = self.rospy.get_param("/move_base/global_costmap/static_layer/map_topic", "/map_2d")
        grid = self._read(lambda: self.rospy.wait_for_message(map_topic, OccupancyGrid, timeout=3), 4)
        grid_cell(point["pose"], grid)
        self._navigation_freshness()  # Re-read after scan/map transport delays.
        if check_only:
            # Noetic make_plan clears costmap windows by default. It must not
            # be called from a strictly read-only inspection, even when idle.
            return capture
        self._read(lambda: self.rospy.wait_for_service("/move_base/make_plan", timeout=2))
        planner = self.rospy.ServiceProxy("/move_base/make_plan", GetPlan)
        start = self._pose_message(capture["pose"])
        target = self._pose_message(point["pose"])
        plan = self._read(lambda: planner(start=start, goal=target, tolerance=0.0)).plan
        if not plan.poses or any(pose.header.frame_id != "map" for pose in plan.poses):
            raise RuntimeError("目标当前不可达：全局规划未返回有效路径")
        last = plan.poses[-1].pose.position
        if not all(finite_number(value) for value in (last.x, last.y)) or math.hypot(
            last.x - point["pose"]["x"], last.y - point["pose"]["y"]
        ) > XY_TOLERANCE:
            raise RuntimeError("规划路径终点与登记导览点不一致")
        if time.monotonic() >= deadline:
            raise TimeoutError("导航预检超时")
        self._navigation_freshness()  # Explicit make_plan is not a live-pose guarantee.
        return capture

    def _check_localized(self):
        import rosservice

        # Cache only the transport/type, never the localization boolean.
        if self.localization_check is None:
            service_type = self._read(lambda: rosservice.get_service_class_by_name("/slam_reloc_check"), 2)
            if service_type is None:
                raise RuntimeError("无法查询导航定位服务类型，已请求停止")
            self.localization_check = self.rospy.ServiceProxy("/slam_reloc_check", service_type)
        if not self._read(lambda: self.localization_check(code=True), 2).status:
            raise RuntimeError("导航期间定位失效，已请求停止")

    def _fresh_pose(self):
        stamp = self.listener.getLatestCommonTime("map", "base_link")
        age = time.time() - stamp.to_sec()
        validate_tf_age(age, self.tf_max_age)
        translation, rotation = self.listener.lookupTransform("map", "base_link", stamp)
        pose = {"x": translation[0], "y": translation[1], "z": translation[2],
                "yaw": self.tf.transformations.euler_from_quaternion(rotation)[2]}
        return pose, stamp

    def quick_capture(self, point):
        if self._map_signatures() != self.signatures:
            raise RuntimeError("导航期间地图发生变化，已请求停止")
        self._check_localized()
        self._navigation_freshness()  # Reject stale TF/feedback immediately.
        # A sensor read can take time. Return a newly fetched TF after it,
        # not the earlier snapshot that may age while the live stream updates.
        self._scan()
        self._check_localized()
        if self._map_signatures() != self.signatures:
            raise RuntimeError("导航期间地图发生变化，已请求停止")
        pose, stamp = self._fresh_pose()
        if self.navigation_feedback_required:
            with self.odom_lock:
                message = self.navigation_odom
            validate_navigation_odom(message, time.time(), self.tf_max_age)
        age = time.time() - stamp.to_sec()
        validate_tf_age(age, self.tf_max_age)
        return {"localized": True, "frame_id": "map", "map_fingerprint": point["map_fingerprint"],
                "tf_age_seconds": max(0, age), "pose": pose}

    def set_motion(self, enabled):
        # Cleanup must remain possible after SIGTERM set the cancellation flag.
        if enabled:
            # Ownership/status transport may have taken time since quick_capture.
            # A localization RPC must not let an earlier freshness check age out.
            self._navigation_freshness()
            self._check_localized()
            self._navigation_freshness()
        result = bounded_call(lambda: self.enable(data=bool(enabled)), 3, self.cancel if enabled else None)
        if result.success is not True:
            raise RuntimeError("安全控制器未确认运动" + ("使能" if enabled else "禁用"))

    def send_goal(self, pose):
        from move_base_msgs.msg import MoveBaseGoal
        goal = MoveBaseGoal()
        goal.target_pose = self._pose_message(pose)
        self.goal_pose = pose
        self.goal_sent_at = time.monotonic()
        self.client.send_goal(goal)
        # The public cancel_goal() remains scoped to this handle; the ROS1
        # action handle's ID is needed only to reject competing live goals.
        self.goal_id = self.client.gh.comm_state_machine.action_goal.goal_id.id

    def wait_owned_plan(self, deadline):
        while time.monotonic() < deadline:
            if self.cancel.is_set():
                raise InterruptedError("导览点导航已取消")
            self.ensure_owned()
            self._navigation_freshness()
            if self.goal_state() in TERMINAL_FAILURES:
                raise RuntimeError("导航目标被拒绝：" + self.goal_text())
            with self.plan_lock:
                plans = list(self.plans)
            for received, plan in plans:
                if received < self.goal_sent_at or plan.header.frame_id != "map" or not plan.poses:
                    continue
                position = plan.poses[-1].pose.position
                if all(finite_number(v) for v in (position.x, position.y)) and math.hypot(
                    position.x - self.goal_pose["x"], position.y - self.goal_pose["y"]
                ) <= XY_TOLERANCE:
                    return
            time.sleep(0.05)
        raise TimeoutError("未收到本目标的有效路径，拒绝使能运动")

    def goal_state(self):
        return self.client.get_state()

    def goal_text(self):
        return self.client.get_goal_status_text()

    def cancel_owned_goal(self):
        self.client.cancel_goal()  # Deliberately not cancel_all_goals().


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--point-id", required=True)
    parser.add_argument("--timeout", type=float, default=150)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check-only", action="store_true")
    modes.add_argument("--verify-only", action="store_true")
    parser.add_argument("--commissioning-confirmed", action="store_true",
        help="人工首次实机验收：确认已落地、运动模式、拆除吊绳及遥控器监护；仅本次单点任务，不放开网页授权")
    parser.add_argument("--present-after-arrival", action="store_true",
        help="到达核验成功并禁用运动后，提交本机网页的动态讲解任务；失败不播报")
    args = parser.parse_args(argv)
    cancelled = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda _signum, _frame: cancelled.set())
    presentation = None
    try:
        if not finite_number(args.timeout) or not 1 <= args.timeout <= 240:
            raise ValueError("导航超时须为 1–240 秒")
        if args.commissioning_confirmed and (args.check_only or args.verify_only):
            raise ValueError("人工实机验收不能与只读模式混用；只读检查不需要运动确认")
        if args.present_after_arrival and (args.check_only or args.verify_only):
            raise ValueError("到达后讲解不能与只读模式混用；只读检查不能触发播报")
        if not (args.check_only or args.verify_only or args.commissioning_confirmed) and os.environ.get("GUIDE_NAMED_NAV_VERIFIED") != "1":
            raise RuntimeError("点位自主导航尚未完成实机验收；本次未发送目标或使能运动")
        point = load_target(GuidePointStore(), args.point_id)
        presentation = PresentationClient(cancelled) if args.present_after_arrival else None
        if presentation:
            presentation.ensure_ready()
        backend = RosBackend(cancelled)
        if presentation:
            presentation.prepare(point["id"])
        if args.commissioning_confirmed:
            print("[人工验收] 将导航到已登记点位：{}；到达或失败后禁用运动。"
                  "本次不会开放网页运动权限。".format(point["name"]), file=sys.stderr, flush=True)
        result = PointNavigator(backend, point, args.timeout, cancelled).run(args.check_only, args.verify_only)
        if args.commissioning_confirmed:
            result.update(mode="commissioning", navigation_mode="navigate",
                          operator_confirmed=True, deployment_gate_unchanged=True)
        if presentation:
            # Navigation has already cancelled its own goal and acknowledged
            # DISARMED. Failures above never reach this speech-only handoff.
            try:
                result["presentation"] = presentation.submit(point["id"])
            except Exception as exc:
                result.update(status="error", navigation_verified=True,
                    message="到达已核验且运动已禁用，但讲解提交失败：" + str(exc))
                print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
                return 1
        print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
        return 0
    except Exception as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False), flush=True)
        return 1
    finally:
        if presentation:
            presentation.cancel_preparation()


if __name__ == "__main__":
    sys.exit(main())
