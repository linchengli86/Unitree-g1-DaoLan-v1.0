#!/usr/bin/env python3
"""No-ROS, no-hardware navigation policy tests using registered point fixtures."""

import copy
import contextlib
import io
import json
import math
import sys
import threading
import time
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from navigate_to_point_safe import (PointNavigator, RosBackend, arrival_result, bounded_call,
                                    grid_cell, load_target, main, validate_target,
                                    MOVING_CAPTURE_ERROR, PresentationClient)


def point():
    return {"id": "a" * 32, "name": "展点", "frame_id": "map", "map_fingerprint": "b" * 64,
            "pose": {"x": 4.0, "y": -1.0, "z": 0.0, "yaw": 0.7}}


def captured(at_target=True):
    pose = copy.deepcopy(point()["pose"])
    if not at_target:
        pose["x"] -= 1
    return {"localized": True, "frame_id": "map", "map_fingerprint": "b" * 64,
            "tf_age_seconds": 0.1, "pose": pose}


class Backend:
    def __init__(self):
        self.events = []
        self.initial = captured(False)
        self.fresh = captured(True)
        self.state = 3
        self.quick_calls = 0

    def preflight(self, point, deadline, verify_only=False, check_only=False):
        self.events.append("preflight")
        return copy.deepcopy(self.initial)

    def ensure_idle(self):
        self.events.append("idle")

    def set_motion(self, enabled):
        self.events.append("arm" if enabled else "disarm")

    def quick_capture(self, point):
        self.events.append("quick")
        self.quick_calls += 1
        return copy.deepcopy(self.initial if self.quick_calls == 1 else self.fresh)

    def stationary_capture(self):
        self.events.append("stationary")
        return copy.deepcopy(self.fresh)

    def send_goal(self, pose):
        self.events.append("goal")

    def wait_owned_plan(self, deadline):
        self.events.append("plan")

    def ensure_owned(self):
        self.events.append("owned")

    def goal_state(self):
        return self.state

    def goal_text(self):
        return "mock failure"

    def cancel_owned_goal(self):
        self.events.append("cancel-own")


class NavigationTests(unittest.TestCase):
    def test_read_only_check_never_mutates(self):
        backend = Backend()
        result = PointNavigator(backend, point()).run(check_only=True)
        self.assertEqual(result["status"], "success")
        self.assertEqual(backend.events, ["preflight"])

    def test_read_only_verify_never_mutates_on_success_or_failure(self):
        for at_target in (True, False):
            backend = Backend()
            backend.initial = captured(at_target)
            if at_target:
                self.assertTrue(PointNavigator(backend, point()).run(verify_only=True)["arrived"])
            else:
                with self.assertRaises(RuntimeError):
                    PointNavigator(backend, point()).run(verify_only=True)
            self.assertEqual(backend.events, ["preflight"])

    def test_success_arms_only_after_checks_and_own_plan_then_stops(self):
        backend = Backend()
        result = PointNavigator(backend, point()).run()
        self.assertTrue(result["arrived"])
        self.assertTrue(result["motion_disabled"])
        self.assertLess(backend.events.index("plan"), backend.events.index("arm"))
        self.assertEqual(backend.events[-1], "cancel-own")
        self.assertLess(backend.events.index("disarm", backend.events.index("arm")), backend.events.index("stationary"))

    def test_already_arrived_no_goal_or_arm(self):
        backend = Backend()
        backend.initial = captured(True)
        self.assertTrue(PointNavigator(backend, point()).run()["arrived"])
        self.assertNotIn("goal", backend.events)
        self.assertNotIn("arm", backend.events)

    def test_bad_preflight_never_arms_or_sends_goal(self):
        for invalid in ({"localized": False}, {"tf_age_seconds": 4}, {"map_fingerprint": "c" * 64}):
            backend = Backend()
            backend.initial.update(invalid)
            with self.assertRaises(RuntimeError):
                PointNavigator(backend, point()).run()
            self.assertEqual(backend.events, ["preflight"])

    def test_plan_failure_cancels_own_and_disables_never_arms(self):
        backend = Backend()
        backend.wait_owned_plan = Mock(side_effect=TimeoutError("no plan"))
        with self.assertRaises(TimeoutError):
            PointNavigator(backend, point()).run()
        self.assertNotIn("arm", backend.events)
        self.assertEqual(backend.events[-2:], ["cancel-own", "disarm"])

    def test_action_success_requires_actual_pose_and_heading(self):
        for invalid in ({"x": 5}, {"yaw": 1.5}):
            backend = Backend()
            backend.fresh["pose"].update(invalid)
            with self.assertRaisesRegex(RuntimeError, "实际位置和朝向"):
                PointNavigator(backend, point()).run()
            self.assertIn("cancel-own", backend.events)
            self.assertEqual(backend.events.count("arm"), 1)
            self.assertEqual(backend.events.count("disarm"), 2)

    def test_stop_waits_for_transient_motion_without_rearming(self):
        backend = Backend()
        backend.stationary_capture = Mock(side_effect=[ValueError(MOVING_CAPTURE_ERROR), captured()])
        now = [100.0]
        navigator = PointNavigator(backend, point(), clock=lambda: now[0],
            sleep=lambda duration: now.__setitem__(0, now[0] + duration))
        self.assertTrue(navigator.run()["verified"])
        self.assertEqual(backend.stationary_capture.call_count, 2)
        self.assertEqual(backend.events.count("arm"), 1)
        self.assertEqual(backend.events.count("disarm"), 2)

    def test_persistent_motion_has_bounded_wait_and_stays_disarmed(self):
        backend = Backend()
        backend.stationary_capture = Mock(side_effect=ValueError(MOVING_CAPTURE_ERROR))
        now = [100.0]
        navigator = PointNavigator(backend, point(), clock=lambda: now[0],
            sleep=lambda duration: now.__setitem__(0, now[0] + duration))
        with self.assertRaisesRegex(TimeoutError, "停稳核验超时"):
            navigator.run()
        self.assertEqual(now[0], 108.0)
        self.assertEqual(backend.events.count("arm"), 1)
        self.assertEqual(backend.events.count("disarm"), 2)
        self.assertEqual(backend.events[-1], "cancel-own")

    def test_settling_never_retries_stale_invalid_or_unlocalized_capture(self):
        for failure in (ValueError("定位数据已过期，请检查重定位和 TF"),
                        ValueError("定位数据无效"), RuntimeError("重定位未成功")):
            backend = Backend()
            backend.stationary_capture = Mock(side_effect=failure)
            with self.assertRaises(type(failure)):
                PointNavigator(backend, point()).run()
            self.assertEqual(backend.stationary_capture.call_count, 1)
            self.assertEqual(backend.events.count("disarm"), 2)

    def test_cancellation_during_settling_never_rearms(self):
        backend = Backend()
        backend.stationary_capture = Mock(side_effect=ValueError(MOVING_CAPTURE_ERROR))
        cancel = threading.Event()
        navigator = PointNavigator(backend, point(), cancel=cancel, sleep=lambda _: cancel.set())
        with self.assertRaises(InterruptedError):
            navigator.run()
        self.assertEqual(backend.events.count("arm"), 1)
        self.assertEqual(backend.events.count("disarm"), 2)

    def test_settling_respects_overall_deadline(self):
        backend = Backend()
        backend.stationary_capture = Mock(side_effect=ValueError(MOVING_CAPTURE_ERROR))
        now = [100.0]
        navigator = PointNavigator(backend, point(), timeout=1, clock=lambda: now[0],
            sleep=lambda duration: now.__setitem__(0, now[0] + duration))
        with self.assertRaises(TimeoutError):
            navigator.run()
        self.assertEqual(now[0], 101.0)

    def test_localization_loss_after_goal_stops_without_arm(self):
        backend = Backend()
        backend.quick_capture = Mock(side_effect=[captured(False), {**captured(), "localized": False}])
        with self.assertRaises(RuntimeError):
            PointNavigator(backend, point()).run()
        self.assertNotIn("arm", backend.events)
        self.assertEqual(backend.events[-2:], ["cancel-own", "disarm"])

    def test_cancel_while_running_cancels_only_own_goal_and_stops(self):
        backend = Backend()
        backend.state = 1
        cancel = threading.Event()
        navigator = PointNavigator(backend, point(), cancel=cancel, sleep=lambda _: cancel.set())
        with self.assertRaises(InterruptedError):
            navigator.run()
        self.assertEqual(backend.events[-2:], ["cancel-own", "disarm"])

    def test_overall_timeout_stops(self):
        backend = Backend()
        backend.state = 1
        now = [100.0]
        navigator = PointNavigator(backend, point(), timeout=2, clock=lambda: now[0],
                                   sleep=lambda _: now.__setitem__(0, now[0] + 3))
        with self.assertRaises(TimeoutError):
            navigator.run()
        self.assertEqual(backend.events[-2:], ["cancel-own", "disarm"])

    def test_disable_must_be_acknowledged(self):
        backend = Backend()
        def set_motion(enabled):
            backend.events.append("arm" if enabled else "disarm")
            if backend.events.count("disarm") > 1:
                raise RuntimeError("controller offline")
        backend.set_motion = set_motion
        with self.assertRaisesRegex(RuntimeError, "立即停止机器人"):
            PointNavigator(backend, point()).run()

    def test_competing_goal_prevents_arm(self):
        backend = Backend()
        backend.ensure_owned = Mock(side_effect=RuntimeError("another goal"))
        with self.assertRaises(RuntimeError):
            PointNavigator(backend, point()).run()
        self.assertNotIn("arm", backend.events)
        self.assertEqual(backend.events[-2:], ["cancel-own", "disarm"])


class ValidationTests(unittest.TestCase):
    def test_only_registered_ids_and_unique_matches(self):
        store = Mock()
        store.list.return_value = [point()]
        self.assertEqual(load_target(store, "a" * 32)["name"], "展点")
        for invalid in ("../secret", "unknown", "A" * 32, "c" * 32, None):
            with self.assertRaises(ValueError):
                load_target(store, invalid)
        store.list.return_value = [point(), point()]
        with self.assertRaises(ValueError):
            load_target(store, "a" * 32)

    def test_malformed_target_and_timeout_rejected_without_ros(self):
        for invalid in ({"frame_id": "odom"}, {"map_fingerprint": "bad"},
                        {"pose": {**point()["pose"], "x": math.nan}},
                        {"pose": {**point()["pose"], "y": True}},
                        {"pose": {**point()["pose"], "z": 1}}):
            with self.assertRaises(ValueError):
                validate_target({**point(), **invalid})
        for timeout in (True, math.nan, 0, 241):
            with self.assertRaises(ValueError):
                PointNavigator(Backend(), point(), timeout)

    def test_arrival_wraps_yaw(self):
        self.assertTrue(arrival_result({"x": 0, "y": 0, "yaw": math.pi - 0.01},
                                       {"x": 0.1, "y": 0, "yaw": -math.pi + 0.01})["arrived"])
        self.assertFalse(arrival_result(point()["pose"], {**point()["pose"], "yaw": 2})["arrived"])

    def test_bounded_read_call_timeout_and_cancellation(self):
        event = threading.Event()
        try:
            with self.assertRaises(TimeoutError):
                bounded_call(lambda: event.wait(1), 0.01)
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(InterruptedError):
                bounded_call(lambda: event.wait(1), 0.01, cancel)
        finally:
            event.set()

    def test_map_target_bounds_unknown_and_obstacle(self):
        grid = SimpleNamespace(header=SimpleNamespace(frame_id="map"),
            info=SimpleNamespace(resolution=1.0, width=2, height=2,
                origin=SimpleNamespace(position=SimpleNamespace(x=0., y=0.),
                    orientation=SimpleNamespace(x=0., y=0., z=0., w=1.))), data=[0, 0, 0, 0])
        self.assertEqual(grid_cell({"x": 0.5, "y": 0.5}, grid), (0, 0))
        for invalid in ({"x": -0.01, "y": 0.5}, {"x": 2.0, "y": 0.5}):
            with self.assertRaises(RuntimeError):
                grid_cell(invalid, grid)
        for occupancy in (-1, 65, 100):
            grid.data[0] = occupancy
            with self.assertRaises(RuntimeError):
                grid_cell({"x": 0.5, "y": 0.5}, grid)

    def test_unverified_cli_refuses_before_reading_files_or_ros(self):
        with patch.dict("os.environ", {"GUIDE_NAMED_NAV_VERIFIED": "0"}), \
                patch("navigate_to_point_safe.GuidePointStore") as store, \
                patch("navigate_to_point_safe.RosBackend") as ros, \
                patch("navigate_to_point_safe.signal.signal"), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["--point-id", "a" * 32]), 1)
            self.assertEqual(json.loads(output.getvalue())["status"], "error")
            store.assert_not_called()
            ros.assert_not_called()

    def commissioning_main(self, backend, extra=()):
        output = io.StringIO()
        with patch.dict("os.environ", {"GUIDE_NAMED_NAV_VERIFIED": "0"}), \
                patch("navigate_to_point_safe.GuidePointStore") as store, \
                patch("navigate_to_point_safe.RosBackend", return_value=backend) as ros, \
                patch("navigate_to_point_safe.signal.signal"), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            store.return_value.list.return_value = [point()]
            code = main(["--point-id", "a" * 32, "--commissioning-confirmed", *extra])
            self.assertEqual(__import__("os").environ["GUIDE_NAMED_NAV_VERIFIED"], "0")
        return code, json.loads(output.getvalue()), store, ros

    def test_commissioning_retains_owned_plan_checks_and_does_not_open_gate(self):
        backend = Backend()
        code, result, store, ros = self.commissioning_main(backend)
        self.assertEqual(code, 0)
        self.assertEqual(result["mode"], "commissioning")
        self.assertTrue(result["verified"])
        self.assertTrue(result["motion_disabled"])
        self.assertTrue(result["deployment_gate_unchanged"])
        self.assertLess(backend.events.index("plan"), backend.events.index("arm"))
        self.assertEqual(backend.events[-1], "cancel-own")

    def test_commissioning_preflight_failure_never_arms(self):
        backend = Backend()
        backend.initial["localized"] = False
        code, result, store, ros = self.commissioning_main(backend)
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "error")
        self.assertEqual(backend.events, ["preflight"])

    def test_commissioning_plan_failure_never_arms_and_cleans_up(self):
        backend = Backend()
        backend.wait_owned_plan = Mock(side_effect=TimeoutError("no plan"))
        code, result, store, ros = self.commissioning_main(backend)
        self.assertEqual(code, 1)
        self.assertNotIn("arm", backend.events)
        self.assertEqual(backend.events[-2:], ["cancel-own", "disarm"])

    def test_commissioning_conflicting_readonly_modes_rejected_before_ros(self):
        for mode in ("--check-only", "--verify-only"):
            backend = Backend()
            code, result, store, ros = self.commissioning_main(backend, [mode])
            self.assertEqual(code, 1)
            self.assertEqual(backend.events, [])
            store.assert_not_called()
            ros.assert_not_called()

    def test_presentation_readonly_modes_rejected_before_ros(self):
        for mode in ("--check-only", "--verify-only"):
            with patch("navigate_to_point_safe.GuidePointStore") as store, \
                    patch("navigate_to_point_safe.RosBackend") as ros, \
                    patch("navigate_to_point_safe.signal.signal"), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--point-id", "a" * 32, mode, "--present-after-arrival"]), 1)
            store.assert_not_called()
            ros.assert_not_called()

    def test_optional_presentation_only_after_success_and_cleanup(self):
        backend = Backend()
        def prepare(point_id):
            self.assertEqual(point_id, "a" * 32)
            self.assertNotIn("arm", backend.events)
        def submit(point_id):
            self.assertEqual(point_id, "a" * 32)
            self.assertEqual(backend.events[-1], "cancel-own")
            self.assertEqual(backend.events.count("disarm"), 2)
            return {"status": "submitted", "completion_verified": False}
        with patch("navigate_to_point_safe.PresentationClient") as presenter:
            presenter.return_value.prepare.side_effect = prepare
            presenter.return_value.submit.side_effect = submit
            code, result, _, _ = self.commissioning_main(backend, ["--present-after-arrival"])
            self.assertEqual(code, 0)
            self.assertFalse(result["presentation"]["completion_verified"])
            presenter.return_value.ensure_ready.assert_called_once()
            presenter.return_value.prepare.assert_called_once_with("a" * 32)

    def test_failed_arrival_never_submits_presentation(self):
        backend = Backend()
        backend.fresh["pose"]["x"] += 1
        with patch("navigate_to_point_safe.PresentationClient") as presenter:
            code, result, _, _ = self.commissioning_main(backend, ["--present-after-arrival"])
            self.assertEqual(code, 1)
            presenter.return_value.submit.assert_not_called()
            presenter.return_value.cancel_preparation.assert_called_once()
            self.assertEqual(backend.events.count("disarm"), 2)

    def test_presentation_outage_prevents_any_navigation(self):
        backend = Backend()
        with patch("navigate_to_point_safe.PresentationClient") as presenter:
            presenter.return_value.ensure_ready.side_effect = RuntimeError("not ready")
            code, result, _, ros = self.commissioning_main(backend, ["--present-after-arrival"])
            self.assertEqual(code, 1)
            self.assertEqual(backend.events, [])
            ros.assert_not_called()
            presenter.return_value.submit.assert_not_called()

    def test_presentation_submission_failure_keeps_verified_navigation_disarmed(self):
        backend = Backend()
        with patch("navigate_to_point_safe.PresentationClient") as presenter:
            presenter.return_value.submit.side_effect = RuntimeError("server unavailable")
            code, result, _, _ = self.commissioning_main(backend, ["--present-after-arrival"])
            self.assertEqual(code, 1)
            self.assertTrue(result["navigation_verified"])
            self.assertTrue(result["motion_disabled"])
            self.assertEqual(backend.events.count("disarm"), 2)

    def test_prefetch_failure_prevents_motion_and_is_cancelled(self):
        backend = Backend()
        with patch("navigate_to_point_safe.PresentationClient") as presenter:
            presenter.return_value.prepare.side_effect = RuntimeError("prefetch unavailable")
            code, result, _, _ = self.commissioning_main(backend, ["--present-after-arrival"])
            self.assertEqual(code, 1)
            self.assertEqual(backend.events, [])
            presenter.return_value.submit.assert_not_called()
            presenter.return_value.cancel_preparation.assert_called_once()


class PresentationClientTests(unittest.TestCase):
    def setUp(self):
        self.client = PresentationClient(threading.Event())
        self.steps = [{"action": "present_point", "parameters": {"point_id": "a" * 32, "topic": ""},
                       "timeout": 240}]

    def replies(self, steps=None):
        return [{"omni": True}, {"task": None},
            {"confirmation": "c" * 32, "plan": {"steps": self.steps if steps is None else steps}},
            {"task": {"id": "d" * 32, "plan": {"steps": self.steps}}}]

    def test_handoff_is_speech_only_and_does_not_claim_playback(self):
        self.client._request = Mock(side_effect=self.replies())
        result = self.client.submit("a" * 32)
        self.assertEqual(result["status"], "submitted")
        self.assertFalse(result["completion_verified"])
        sent = self.client._request.call_args_list[2].args[1]["plan"]
        self.assertEqual(sent["steps"], self.steps)
        self.assertEqual(self.client.address, "http://127.0.0.1:8765")

    def test_unexpected_navigation_plan_never_confirmed(self):
        self.client._request = Mock(side_effect=self.replies([
            {"action": "navigate_to_point", "parameters": {"point_id": "a" * 32}}]))
        with self.assertRaises(RuntimeError):
            self.client.submit("a" * 32)
        self.assertEqual(self.client._request.call_count, 3)

    def test_missing_omni_or_busy_task_rejected(self):
        for replies in ([{"omni": False}], [{"omni": True}, {"task": {"state": "running"}}]):
            self.client._request = Mock(side_effect=replies)
            with self.assertRaises(RuntimeError):
                self.client.submit("a" * 32)

    def test_cancelled_before_handoff_never_contacts_server(self):
        self.client.cancel.set()
        with patch.object(self.client.opener, "open") as contact:
            with self.assertRaises(InterruptedError):
                self.client.ensure_ready()
            contact.assert_not_called()

    def test_prefetch_is_pure_compute_and_ticket_is_internal_metadata(self):
        self.client._request = Mock(side_effect=[{"omni": True}, {"task": None},
            {"preparation": {"id": "e" * 32, "state": "running"}}, *self.replies()])
        self.client.prepare("a" * 32)
        self.assertEqual(self.client.preparation_id, "e" * 32)
        self.assertEqual(self.client._request.call_args_list[2].args,
            ("/api/agent/prefetch", {"point_id": "a" * 32, "topic": ""}))
        self.client.submit("a" * 32)
        prepared = self.client._request.call_args_list[5].args[1]
        self.assertEqual(prepared["presentation_ticket"], "e" * 32)
        self.assertEqual(prepared["plan"]["steps"], self.steps)
        self.assertIsNone(self.client.preparation_id)

    def test_malformed_prefetch_ticket_is_rejected(self):
        self.client._request = Mock(side_effect=[{"omni": True}, {"task": None},
            {"preparation": {"id": "../bad"}}])
        with self.assertRaises(RuntimeError):
            self.client.prepare("a" * 32)
        self.assertIsNone(self.client.preparation_id)

    def test_cancel_preparation_still_works_after_navigation_cancel(self):
        self.client.preparation_id = "e" * 32
        self.client.cancel.set()
        with patch.object(self.client.opener, "open") as contact:
            self.client.cancel_preparation()
            request = contact.call_args.args[0]
            self.assertEqual(request.full_url, self.client.address + "/api/agent/prefetch/cancel")
            self.assertEqual(json.loads(request.data), {"id": "e" * 32})
        self.assertIsNone(self.client.preparation_id)


class RosReadOnlyTests(unittest.TestCase):
    """Exercise the real preflight with inert ROS module and message stubs."""

    def setUp(self):
        self.modules = {}
        for name in ("rosnode", "nav_msgs", "nav_msgs.msg", "nav_msgs.srv"):
            self.modules[name] = ModuleType(name)
        self.modules["rosnode"].get_node_names = Mock(return_value=[
            "/localizer_node", "/move_base", "/unitree_safe_controller"])
        self.modules["nav_msgs.msg"].OccupancyGrid = object
        self.modules["nav_msgs.srv"].GetPlan = object
        self.backend = object.__new__(RosBackend)
        self.backend.deadline = None
        self.backend.cancel = threading.Event()
        self.backend.rospy = Mock()
        self.backend.rospy.get_param.side_effect = lambda name, default=None: {
            "/move_base/global_costmap/transform_tolerance": .5,
            "/move_base/local_costmap/transform_tolerance": .5,
            "/move_base/TebLocalPlannerROS/odom_topic": "navigation_odom"}.get(name, default)
        self.backend.tf_max_age = .5
        self.backend.navigation_feedback_required = False
        self.backend._navigation_freshness = Mock()
        self.backend._read = lambda callback, timeout=3: callback()
        self.backend._map_signatures = Mock(return_value=(1, 2, 3))
        self.backend.stationary_capture = Mock(return_value=captured())
        self.backend.ensure_idle = Mock()
        self.backend._scan = Mock()
        self.backend.client = Mock()
        self.backend.client.wait_for_server.return_value = True
        self.backend._pose_message = lambda pose: pose
        self.backend.rospy.wait_for_message.return_value = SimpleNamespace(header=SimpleNamespace(frame_id="map"),
            info=SimpleNamespace(resolution=1.0, width=10, height=10,
                origin=SimpleNamespace(position=SimpleNamespace(x=0., y=-5.),
                    orientation=SimpleNamespace(x=0., y=0., z=0., w=1.))), data=[0] * 100)

    def test_check_only_never_calls_costmap_mutating_make_plan(self):
        with patch.dict(sys.modules, self.modules):
            self.backend.preflight(point(), time.monotonic() + 20, check_only=True)
        self.backend.rospy.ServiceProxy.assert_not_called()
        self.assertEqual(self.backend.rospy.wait_for_service.call_count, 1)
        self.assertEqual(self.backend.rospy.wait_for_service.call_args.args[0], "/unitree_motion_enable")

    def test_verify_only_does_not_require_planner_or_controller(self):
        self.modules["rosnode"].get_node_names.return_value = ["/localizer_node"]
        with patch.dict(sys.modules, self.modules):
            self.backend.preflight(point(), time.monotonic() + 20, verify_only=True)
        self.backend.rospy.ServiceProxy.assert_not_called()
        self.backend.rospy.wait_for_service.assert_not_called()
        self.backend.rospy.wait_for_message.assert_not_called()
        self.backend.ensure_idle.assert_not_called()

    def test_real_noetic_get_plan_header_can_be_empty(self):
        target = point()["pose"]
        plan = SimpleNamespace(header=SimpleNamespace(frame_id=""), poses=[
            SimpleNamespace(header=SimpleNamespace(frame_id="map"), pose=SimpleNamespace(
                position=SimpleNamespace(x=target["x"], y=target["y"])))])
        self.backend.rospy.ServiceProxy.return_value.return_value = SimpleNamespace(plan=plan)
        with patch.dict(sys.modules, self.modules):
            self.backend.preflight(point(), time.monotonic() + 20)
        self.backend.rospy.ServiceProxy.assert_called_once()

    def configure_quick_capture(self, stamps):
        self.backend.signatures = (1, 2, 3)
        self.backend._check_localized = Mock()
        # Exercise the actual TF check while keeping feedback separately inert.
        self.backend._navigation_freshness = lambda: self.backend._fresh_pose()
        self.backend.listener = Mock()
        self.backend.listener.getLatestCommonTime.side_effect = [
            SimpleNamespace(to_sec=lambda stamp=stamp: stamp) for stamp in stamps]
        self.backend.listener.lookupTransform.side_effect = lambda _a, _b, stamp: (
            [stamp.to_sec(), 0., 0.], [0., 0., 0., 1.])
        self.backend.tf = Mock()
        self.backend.tf.transformations.euler_from_quaternion.return_value = [0., 0., 0.]

    def test_quick_capture_returns_new_tf_after_delayed_scan(self):
        self.configure_quick_capture([99.9, 101.9])
        now = [100.0]
        self.backend._scan.side_effect = lambda: now.__setitem__(0, 102.0)
        with patch("navigate_to_point_safe.time.time", side_effect=lambda: now[0]):
            result = self.backend.quick_capture(point())
        self.assertAlmostEqual(result["tf_age_seconds"], .1)
        self.assertEqual(result["pose"]["x"], 101.9)
        self.assertEqual(self.backend._check_localized.call_count, 2)

    def test_quick_capture_stale_entry_never_waits_for_scan(self):
        self.configure_quick_capture([98.4])
        with patch("navigate_to_point_safe.time.time", return_value=100):
            with self.assertRaisesRegex(RuntimeError, "TF 已过期"):
                self.backend.quick_capture(point())
        self.backend._scan.assert_not_called()

    def test_quick_capture_rejects_stream_frozen_during_scan(self):
        self.configure_quick_capture([99.9, 99.9])
        now = [100.0]
        self.backend._scan.side_effect = lambda: now.__setitem__(0, 102.0)
        with patch("navigate_to_point_safe.time.time", side_effect=lambda: now[0]):
            with self.assertRaisesRegex(RuntimeError, "TF 已过期"):
                self.backend.quick_capture(point())

    def test_quick_capture_rejects_localization_loss_during_scan(self):
        self.configure_quick_capture([99.9])
        self.backend._check_localized.side_effect = [None, RuntimeError("导航期间定位失效")]
        with patch("navigate_to_point_safe.time.time", return_value=100):
            with self.assertRaisesRegex(RuntimeError, "定位失效"):
                self.backend.quick_capture(point())
        self.assertEqual(self.backend.listener.getLatestCommonTime.call_count, 1)

    def test_quick_capture_rejects_map_change_during_scan(self):
        self.configure_quick_capture([99.9])
        self.backend._map_signatures.side_effect = [(1, 2, 3), (1, 2, 4)]
        with patch("navigate_to_point_safe.time.time", return_value=100):
            with self.assertRaisesRegex(RuntimeError, "地图发生变化"):
                self.backend.quick_capture(point())

    def test_capture_reuses_backend_listener(self):
        self.backend._read = lambda callback, timeout=3: callback()
        self.backend.listener = object()
        with patch("capture_guide_pose.capture", return_value={"status": "success"}) as capture:
            self.backend._capture()
        capture.assert_called_once_with(init_ros=False, listener=self.backend.listener)

    def test_localization_transport_cached_but_every_status_is_fresh(self):
        module = ModuleType("rosservice")
        module.get_service_class_by_name = Mock(return_value=object())
        self.backend.localization_check = None
        check = self.backend.rospy.ServiceProxy.return_value
        check.side_effect = [SimpleNamespace(status=True), SimpleNamespace(status=True), SimpleNamespace(status=False)]
        with patch.dict(sys.modules, {"rosservice": module}):
            self.backend._check_localized()
            self.backend._check_localized()
            with self.assertRaisesRegex(RuntimeError, "定位失效"):
                self.backend._check_localized()
        module.get_service_class_by_name.assert_called_once()
        self.backend.rospy.ServiceProxy.assert_called_once()
        self.assertEqual(check.call_count, 3)

    def test_navigation_fixed_tf_is_static_and_preserves_existing_extrinsic(self):
        from pathlib import Path
        from xml.etree import ElementTree
        root = Path(__file__).resolve().parents[1]
        launch = ElementTree.parse(root / "G1Nav2D/src/fastlio2/launch/navigation.launch")
        fixed = [node for node in launch.findall("node") if node.get("name") == "base_to_body"]
        self.assertEqual(len(fixed), 1)
        self.assertEqual(fixed[0].get("pkg"), "tf2_ros")
        self.assertEqual(fixed[0].get("type"), "static_transform_publisher")
        self.assertEqual(fixed[0].get("args").split(),
            ["0.0", "0", "0.0", "3.1416", "3.1416", "0", "body", "base_link"])


if __name__ == "__main__":
    unittest.main()
