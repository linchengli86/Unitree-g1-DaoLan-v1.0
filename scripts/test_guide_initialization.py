#!/usr/bin/env python3
"""No-hardware initialization policy/state/procedure tests."""

import copy
import json
import os
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from guide_initialization import InitializationManager
import initialize_robot_services as helper


CONFIRMATIONS = {"grounded": True, "motion_mode": True, "stationary": True}


def success(localized=False):
    data = {"status": "success", "localized": localized, "needs_initial_pose": not localized,
            "motion_disabled": True, "frame_id": "map", "map_fingerprint": "a" * 64, "services": {}}
    if localized:
        data["pose"] = {"x": 2.0, "y": 1.0, "z": 0.0, "yaw": 0.1}
    return data


def inventory():
    return {name: [] for name in ("navigation", "safe_controller", "unsafe_controller", "foreign_controller", "foreign_navigation")}


def master(nodes=None, active=False, known=True, controller_pid=2):
    return {"status": "success", "master_ready": True, "nodes": sorted(nodes or []),
            "active_navigation": active, "navigation_idle_known": known, "controller_pid": controller_pid,
            "controller_ready": "/unitree_safe_controller" in (nodes or [])}


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runner = Mock(return_value=success())
        self.manager = InitializationManager(self.temp.name, self.runner)

    def tearDown(self):
        self.manager.cancel()
        if self.manager.thread:
            self.manager.thread.join(2)
        self.temp.cleanup()

    def finish(self):
        self.manager.thread.join(2)
        self.assertFalse(self.manager.thread.is_alive())
        return self.manager.snapshot()

    def test_confirmation_missing_false_extra_or_nonboolean_rejected(self):
        for data in (None, {}, {**CONFIRMATIONS, "grounded": False}, {**CONFIRMATIONS, "motion_mode": 1},
                     {**CONFIRMATIONS, "task": "x"}, {"grounded": True, "stationary": True}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.manager.start(data)
        self.runner.assert_not_called()

    def test_busy_never_starts(self):
        self.manager.idle = lambda: False
        with self.assertRaises(RuntimeError):
            self.manager.start(CONFIRMATIONS)
        self.runner.assert_not_called()

    def test_unlocalized_software_setup_succeeds_without_claiming_pose(self):
        self.manager.start(CONFIRMATIONS)
        result = self.finish()
        self.assertEqual(result["state"], "succeeded")
        self.assertTrue(result["needs_initial_pose"])
        self.assertNotIn("pose", result)
        self.assertFalse(result["automatic_motion_enabled"])
        self.assertIn("地图", result["message"])

    def test_localized_success_and_deepcopy(self):
        self.runner.return_value = success(True)
        self.manager.start(CONFIRMATIONS)
        result = self.finish()
        result["pose"]["x"] = 99
        self.assertEqual(self.manager.snapshot()["pose"]["x"], 2)
        self.assertFalse(self.manager.snapshot()["needs_initial_pose"])

    def test_unlocalized_runner_pose_is_never_exposed(self):
        self.runner.return_value = {**success(), "pose": {"x": 0, "y": 0, "z": 0, "yaw": 0}}
        self.manager.start(CONFIRMATIONS)
        result = self.finish()
        self.assertEqual(result["state"], "succeeded")
        self.assertNotIn("pose", result)

    def test_nonfinite_localized_pose_rejected(self):
        self.runner.return_value = {**success(True), "pose": {"x": float("nan"), "y": 0, "z": 0, "yaw": 0}}
        self.manager.start(CONFIRMATIONS)
        self.assertEqual(self.finish()["state"], "failed")

    def test_singleflight_cancel_and_services_not_killed(self):
        entered = threading.Event()
        def wait(cancel, report):
            report("network", "running", "网络检查")
            entered.set()
            cancel.wait(1)
            return success()
        self.manager.runner = wait
        self.manager.start(CONFIRMATIONS)
        self.assertTrue(entered.wait(1))
        with self.assertRaises(RuntimeError):
            self.manager.start(CONFIRMATIONS)
        self.assertTrue(self.manager.cancel())
        self.assertEqual(self.finish()["state"], "cancelled")
        self.assertFalse(self.manager.cancel())
        self.assertFalse(self.manager.snapshot()["automatic_motion_enabled"])

    def test_failed_runner_preserves_reason(self):
        self.runner.return_value = {"status": "error", "message": "雷达供电不可达"}
        self.manager.start(CONFIRMATIONS)
        result = self.finish()
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["message"], "雷达供电不可达")

    def test_no_success_without_disabled_ack_or_consistent_localization(self):
        for invalid in ({**success(), "motion_disabled": False}, {**success(), "localized": 0},
                        {**success(), "needs_initial_pose": False}, {**success(True), "pose": None}):
            self.runner.return_value = invalid
            self.manager.start(CONFIRMATIONS)
            with self.subTest(result=invalid):
                self.assertEqual(self.finish()["state"], "failed")

    def test_reports_update_not_duplicate_and_are_independent(self):
        details = {"names": ["eth0"]}
        def run(cancel, report):
            report("network", "running", "检查连接")
            report("network", "succeeded", "已连接", details)
            details["names"].append("other")
            return success()
        self.manager.runner = run
        self.manager.start(CONFIRMATIONS)
        result = self.finish()
        self.assertEqual(len(result["stages"]), 1)
        self.assertEqual(result["stages"][0]["state"], "succeeded")
        self.assertEqual(result["stages"][0]["details"], {"names": ["eth0"]})

    def test_new_attempt_never_leaks_old_pose(self):
        self.runner.return_value = success(True)
        self.manager.start(CONFIRMATIONS)
        self.finish()
        self.runner.return_value = {"status": "error", "message": "连接丢失"}
        self.manager.start(CONFIRMATIONS)
        result = self.finish()
        self.assertNotIn("pose", result)
        self.assertTrue(result["needs_initial_pose"])


class ProcessTests(unittest.TestCase):
    def record(self, script="g1_control_safe.py", interface="eth0", uid=None):
        root = Path("/tmp/daolan-policy-test")
        return {"pid": 42, "argv": ["/bin/python3", str(root / "unitree_sdk2_python/example/g1/high_level" / script), interface],
                "cwd": str(root / "unitree_sdk2_python/example/g1/high_level"),
                "start_ticks": 33, "uid": os.getuid() if uid is None else uid}

    def test_safe_exact_script_interface_owner_and_shell_false_match(self):
        project = Path("/tmp/daolan-policy-test")
        record = self.record()
        self.assertEqual(helper.process_role(record, project), "safe_controller")
        self.assertEqual(helper.process_role(self.record(interface="wlan0"), project), "foreign_controller")
        self.assertEqual(helper.process_role(self.record(uid=os.getuid() + 1), project), "foreign_controller")
        record["argv"] = ["bash", "-lc", "echo g1_control_safe.py eth0"]
        self.assertIsNone(helper.process_role(record, project))

    def test_unsafe_controller_rejected_not_terminated(self):
        services = inventory()
        services["unsafe_controller"] = [self.record("g1_control.py")]
        with self.assertRaisesRegex(RuntimeError, "旧 g1_control.py"):
            helper.validate_inventory(services)
        self.assertEqual(len(services["unsafe_controller"]), 1)
        self.assertEqual(helper.process_role(self.record("g1_control_test.py"), "/tmp/daolan-policy-test"), "unsafe_controller")

    def test_partial_foreign_duplicate_inventory_rejected(self):
        for role in ("foreign_controller", "foreign_navigation", "safe_controller", "navigation"):
            services = inventory()
            services[role] = [self.record()] * (2 if role in ("safe_controller", "navigation") else 1)
            with self.subTest(role=role), self.assertRaises(RuntimeError):
                helper.validate_inventory(services)

    def test_reused_pid_different_start_tick_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "pid.json"
            record = self.record()
            path.write_text(json.dumps({"pid": 42, "start_ticks": 32, "project": "/tmp/daolan-policy-test"}))
            with patch.object(helper, "process_record", return_value=record):
                self.assertIsNone(helper.read_owned_pid(path, "safe_controller", "/tmp/daolan-policy-test"))
            path.write_text(json.dumps({"pid": 42, "start_ticks": 33, "project": "/tmp/daolan-policy-test"}))
            with patch.object(helper, "process_record", return_value=record):
                self.assertEqual(helper.read_owned_pid(path, "safe_controller", "/tmp/daolan-policy-test")["pid"], 42)

    def test_active_or_unknown_navigation_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "活动"):
            helper.assert_idle(master(active=True))
        with self.assertRaisesRegex(RuntimeError, "无法确认"):
            helper.assert_idle(master(known=False))
        helper.assert_idle(master())

    def test_network_only_verified_interface(self):
        with patch.object(helper, "run_finite") as command:
            with self.assertRaisesRegex(RuntimeError, "eth0"):
                helper.check_network("wlan0")
            command.assert_not_called()

    def test_ros_controller_pid_must_match_verified_process(self):
        self.assertEqual(helper.verify_controller_pid(master(controller_pid=2), [{"pid": 2}]), 2)
        with self.assertRaisesRegex(RuntimeError, "PID"):
            helper.verify_controller_pid(master(controller_pid=3), [{"pid": 2}])


class ProcedureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        (self.project / "unitree_sdk2_python/example/g1/high_level").mkdir(parents=True)
        (self.project / "unitree_sdk2_python/example/g1/high_level/g1_control_safe.py").write_text("# fixture")
        self.setup = self.project / "ros-setup.bash"
        self.setup.write_text("# fixture")
        self.patches = [patch.dict(os.environ, {"ROS_SETUP": str(self.setup), "CONTROL_PYTHON": "/usr/bin/python3",
                         "CONTROL_IFACE": "eth0", "ROS_MASTER_URI": "http://localhost:11311"}),
                        patch.object(helper, "verify_files", return_value={"map_fingerprint": "a" * 64, "point_count": 8}),
                        patch.object(helper, "check_network", return_value={"interface": "eth0"}),
                        patch.object(helper, "find_services", return_value=inventory()),
                        patch.object(helper, "start_service", return_value={"pid": 99}),
                        patch.object(helper, "run_finite", return_value=(0, b"")),
                        patch.object(helper, "probe")]
        self.started = [p.start() for p in self.patches]
        self.services, self.start, self.command, self.probe = self.started[3:]
        self.calls = []
        def response(mode, timeout=5, controller_pid=None):
            self.calls.append(mode)
            if mode == "master":
                return master(helper.REQUIRED_NODES | ({"/unitree_safe_controller"} if self.calls.count("master") >= 4 else set()), controller_pid=99)
            if mode == "disable":
                return {"status": "success", "motion_disabled": True}
            if mode == "sensors":
                return {"status": "success", "topics": {}}
            return {"status": "success", "localized": False}
        self.probe.side_effect = response
        helper.CANCEL.clear()

    def tearDown(self):
        helper.CANCEL.clear()
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def test_unlocalized_success_no_relocation_or_navigation_goal(self):
        result = helper.initialize(self.project, report=Mock())
        self.assertTrue(result["needs_initial_pose"])
        self.assertNotIn("pose", result)
        self.assertEqual(set(self.calls), {"master", "disable", "sensors", "localization"})
        self.assertEqual([call.args[0] for call in self.start.call_args_list], ["safe_controller"])
        self.assertTrue(result["motion_disabled"])

    def test_existing_full_services_never_start_duplicates(self):
        services = inventory()
        services["navigation"] = [{"pid": 1}]
        services["safe_controller"] = [{"pid": 2}]
        self.services.return_value = services
        def response(mode, timeout=5, controller_pid=None):
            self.calls.append(mode)
            if mode == "master":
                return master(helper.REQUIRED_NODES | {"/unitree_safe_controller"})
            return {"status": "success", "motion_disabled": True, "localized": False}
        self.probe.side_effect = response
        result = helper.initialize(self.project, report=Mock())
        self.start.assert_not_called()
        self.assertTrue(result["services"]["controller"]["reused"])

    def test_existing_active_goal_refuses_before_disable_or_launch(self):
        services = inventory()
        services["safe_controller"] = [{"pid": 2}]
        self.services.return_value = services
        self.probe.side_effect = lambda mode, timeout=5: master(helper.REQUIRED_NODES | {"/unitree_safe_controller"}, active=True)
        with self.assertRaisesRegex(RuntimeError, "活动"):
            helper.initialize(self.project, report=Mock())
        self.assertEqual([call.args[0] for call in self.probe.call_args_list], ["master"])
        self.start.assert_not_called()

    def test_existing_partial_navigation_not_restarted(self):
        self.probe.return_value = master({"/localizer_node"})
        self.probe.side_effect = None
        with self.assertRaisesRegex(RuntimeError, "一部分"):
            helper.initialize(self.project, report=Mock())
        self.start.assert_not_called()

    def test_unknown_registered_controller_not_duplicated(self):
        self.probe.side_effect = lambda mode, timeout=5: master(helper.REQUIRED_NODES | {"/unitree_safe_controller"})
        with self.assertRaisesRegex(RuntimeError, "进程归属"):
            helper.initialize(self.project, report=Mock())
        self.start.assert_not_called()

    def test_owned_cold_navigation_waits_for_first_known_idle_status(self):
        navigation_states = iter([master(), master(helper.REQUIRED_NODES, known=False), master(helper.REQUIRED_NODES)])
        phase_calls = 0
        def startup(mode, timeout=5, controller_pid=None):
            nonlocal phase_calls
            if mode == "master":
                phase_calls += 1
                if phase_calls <= 3:
                    return next(navigation_states)
                nodes = helper.REQUIRED_NODES | ({"/unitree_safe_controller"} if phase_calls >= 5 else set())
                return master(nodes, controller_pid=99)
            if mode == "disable":
                return {"status": "success", "motion_disabled": True}
            return {"status": "success", "localized": False}
        self.probe.side_effect = startup
        with patch.object(helper.time, "sleep"):
            result = helper.initialize(self.project, report=Mock())
        self.assertTrue(result["needs_initial_pose"])
        self.assertEqual([call.args[0] for call in self.start.call_args_list], ["navigation", "safe_controller"])

    def test_owned_cold_navigation_still_refuses_active_goal(self):
        self.probe.side_effect = [master(), master(helper.REQUIRED_NODES, active=True)]
        with self.assertRaisesRegex(RuntimeError, "活动"):
            helper.initialize(self.project, report=Mock())
        self.assertEqual([call.args[0] for call in self.start.call_args_list], ["navigation"])

    def test_existing_navigation_unknown_idle_refused_without_disable(self):
        services = inventory()
        services["navigation"] = [{"pid": 4674}]
        services["safe_controller"] = [{"pid": 4892}]
        self.services.return_value = services
        self.probe.side_effect = None
        self.probe.return_value = master(helper.REQUIRED_NODES | {"/unitree_safe_controller"}, known=False, controller_pid=4892)
        with self.assertRaisesRegex(RuntimeError, "无法确认"):
            helper.initialize(self.project, report=Mock())
        self.start.assert_not_called()
        self.assertEqual([call.args[0] for call in self.probe.call_args_list], ["master"])

    def test_sensor_failure_never_claims_success(self):
        original = self.probe.side_effect
        def missing(mode, timeout=5, controller_pid=None):
            if mode == "sensors":
                raise RuntimeError("IMU 消息未到达")
            return original(mode, timeout, controller_pid)
        self.probe.side_effect = missing
        report = Mock()
        with self.assertRaisesRegex(RuntimeError, "IMU"):
            helper.initialize(self.project, report=report)
        self.assertTrue(any(call.args[:2] == ("sensors", "failed") for call in report.call_args_list))

    def test_cancelled_before_any_service_changes(self):
        helper.CANCEL.set()
        with self.assertRaises(InterruptedError):
            helper.initialize(self.project, report=Mock())
        self.probe.assert_not_called()
        self.start.assert_not_called()

    def test_foreign_master_refused(self):
        with patch.dict(os.environ, {"ROS_MASTER_URI": "http://192.168.8.8:11311"}):
            with self.assertRaisesRegex(RuntimeError, "本机"):
                helper.initialize(self.project, report=Mock())
        self.start.assert_not_called()


class ReadOnlyProbeTests(unittest.TestCase):
    def setUp(self):
        self.master = Mock()
        self.master.getSystemState.return_value = (
            [("/move_base/status", ["/move_base"])],
            [("/move_base/goal", ["/move_base"])],
            [("/unitree_motion_enable", ["/unitree_safe_controller"])])
        self.rospy = Mock()
        self.rospy.ROSException = type("ROSException", (Exception,), {})
        self.rospy.get_param.return_value = False
        self.message = types.SimpleNamespace(header=types.SimpleNamespace(
            stamp=types.SimpleNamespace(to_sec=lambda: time.time())), status_list=[])
        self.rospy.wait_for_message.return_value = self.message
        self.rosservice = Mock()
        self.rosservice.get_service_class_by_name.return_value = object
        modules = {"rosgraph": types.SimpleNamespace(Master=Mock(return_value=self.master)),
                   "rospy": self.rospy, "rosservice": self.rosservice,
                   "actionlib_msgs": types.ModuleType("actionlib_msgs"),
                   "actionlib_msgs.msg": types.SimpleNamespace(GoalStatusArray=object)}
        self.module_patch = patch.dict("sys.modules", modules)
        self.pid_patch = patch.object(helper, "controller_node_pid", return_value=4892)
        self.module_patch.start()
        self.pid_patch.start()

    def tearDown(self):
        self.pid_patch.stop()
        self.module_patch.stop()

    def test_empty_status_is_known_idle_without_any_tf_read(self):
        result = helper.ros_probe("master")
        self.assertTrue(result["navigation_idle_known"])
        self.assertFalse(result["active_navigation"])
        self.assertEqual(result["controller_pid"], 4892)
        self.rospy.ServiceProxy.assert_not_called()
        self.assertTrue(self.rospy.init_node.call_args.kwargs["anonymous"])

    def test_pending_status_is_not_idle(self):
        self.message.status_list = [types.SimpleNamespace(status=0)]
        self.assertTrue(helper.ros_probe("master")["active_navigation"])

    def test_status_first_message_timeout_is_unknown(self):
        self.rospy.wait_for_message.side_effect = self.rospy.ROSException("no first status yet")
        result = helper.ros_probe("master")
        self.assertFalse(result["navigation_idle_known"])
        self.assertFalse(result["active_navigation"])

    def test_false_localization_does_not_import_tf_capture(self):
        self.rospy.ServiceProxy.return_value.return_value = types.SimpleNamespace(status=False)
        with patch.dict("sys.modules", {"capture_guide_pose": None}):
            result = helper.ros_probe("localization")
        self.assertEqual(result, {"status": "success", "localized": False, "needs_initial_pose": True})
        self.assertTrue(self.rospy.init_node.call_args.kwargs["anonymous"])

    def test_real_existing_project_cwd_command_shapes_are_accepted(self):
        root = Path("/home/unitree/robot/DaoLan")
        nav = {"pid": 4674, "uid": os.getuid(), "cwd": str(root), "start_ticks": 1,
               "argv": ["/usr/bin/python3", "/opt/ros/noetic/bin/roslaunch", "fastlio", "navigation.launch", "rviz:=false"]}
        control = {"pid": 4892, "uid": os.getuid(), "cwd": str(root), "start_ticks": 2,
                   "argv": ["/home/unitree/robot_dev/envs/unitree-core/bin/python", "-u",
                            str(root / "unitree_sdk2_python/example/g1/high_level/g1_control_safe.py"), "eth0"]}
        self.assertEqual(helper.process_role(nav, root), "navigation")
        self.assertEqual(helper.process_role(control, root), "safe_controller")


if __name__ == "__main__":
    unittest.main()
