#!/usr/bin/env python3
"""Pure/mock tests for bounded 1 m seed policy and native quality handshakes.

No ROS nodes, processes, SSH, navigation, TTS, or hardware commands are run.
"""

import copy
import json
import math
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from guide_points import GuidePointStore
from guide_relocalization import (COARSE_CANDIDATES, COARSE_MODE, COARSE_REPORT_PARAM,
    MANUAL_SEED_NAME, PRECISE_MODE, RelocalizationManager, validate_coarse_report, validate_seed)
from test_guide_points import capture
from test_guide_relocalization import coarse_capture, seed
import web_relocalize


class CoarseSeedTests(unittest.TestCase):
    def test_only_map_manual_seed_defaults_to_coarse(self):
        initial = validate_seed(seed(MANUAL_SEED_NAME))
        self.assertEqual(initial["localization_mode"], COARSE_MODE)
        self.assertEqual(initial["radius"], 1)
        self.assertEqual(initial["yaw_uncertainty"], math.pi / 4)
        self.assertNotIn("localization_mode", validate_seed(seed()))

    def test_coarse_bounds_cannot_be_expanded_or_malformed(self):
        initial = seed(MANUAL_SEED_NAME)
        for fields in ({"radius": 2}, {"radius": True}, {"radius": float("nan")},
                       {"yaw_uncertainty": math.pi}, {"yaw_uncertainty": False},
                       {"localization_mode": "global"}, {"localization_mode": False},
                       {"localization_mode": ""}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                validate_seed({**initial, **fields})

    def test_saved_guide_point_cannot_request_coarse_policy(self):
        for fields in ({"localization_mode": COARSE_MODE}, {"radius": 1},
                       {"yaw_uncertainty": math.pi / 4}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                validate_seed({**seed("起点"), **fields})

    def test_precise_confirmed_retains_mode_not_coarse_range(self):
        initial = validate_seed({**seed(MANUAL_SEED_NAME), "localization_mode": PRECISE_MODE})
        self.assertEqual(initial["localization_mode"], PRECISE_MODE)
        self.assertNotIn("radius", initial)
        with self.assertRaises(ValueError):
            validate_seed({**initial, "radius": 1})


class CoarseReportTests(unittest.TestCase):
    def setUp(self):
        self.project = Path("/tmp/mock-daolan-coarse")
        result = coarse_capture(self.project)
        self.report = result["coarse_report"]
        self.requested_at = result["coarse_requested_at"]
        self.request_id = result["coarse_request_id"]

    def validate(self, report):
        return validate_coarse_report(report, self.request_id, self.requested_at, self.project)

    def test_complete_request_specific_independent_quality_is_accepted(self):
        self.assertIs(self.validate(self.report), self.report)

    def test_old_wrong_unknown_missing_or_future_report_rejected(self):
        for changed in (None, {}, {**self.report, "request_id": "old"},
                        {**self.report, "state": "matching"},
                        {**self.report, "map_path": "/tmp/other.pcd"},
                        {**self.report, "started_at": self.requested_at - 10},
                        {**self.report, "finished_at": time.time() + 10},
                        {**self.report, "finished_at": time.time() - 20},
                        {**self.report, "finished_at": False}):
            with self.subTest(report=changed), self.assertRaises(RuntimeError):
                self.validate(changed)

    def test_stable_tf_cannot_replace_unique_independent_scan(self):
        for fields in ({"pose_verified": False}, {"pose_verified": 1},
                       {"independent_scan_verified": False}, {"independent_scan_verified": 1},
                       {"ambiguous": True}, {"ambiguous": 0},
                       {"candidate_count": 64}, {"candidate_count": 66},
                       {"candidate_count": True}, {"final_candidates": 0},
                       {"final_candidates": 5}, {"final_candidates": True}):
            with self.subTest(fields=fields), self.assertRaises(RuntimeError):
                self.validate({**self.report, **fields})

    def test_matching_and_validation_both_require_overlap_and_error_limits(self):
        for prefix in ("", "validation_"):
            for field, value in (("score", .04), ("score", -1), ("score", float("nan")),
                                 ("score", True), ("inlier_ratio", .54), ("inlier_ratio", 1.01),
                                 ("inlier_ratio", False), ("count", 79), ("count", 80.0)):
                changed = {**self.report, prefix + field: value}
                with self.subTest(prefix=prefix, field=field, value=value), self.assertRaises(RuntimeError):
                    self.validate(changed)

    def test_boundary_quality_accepted_missing_evidence_rejected(self):
        good = {**self.report, "score": .0324, "validation_score": .0324,
                "inlier_ratio": .55, "validation_inlier_ratio": .55,
                "count": 80, "validation_count": 80, "final_candidates": 4}
        self.validate(good)
        for key in ("verified_pose", "score", "validation_count", "ambiguous"):
            missing = dict(good)
            missing.pop(key)
            with self.subTest(missing=key), self.assertRaises(RuntimeError):
                self.validate(missing)


class CoarseManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        self.points = GuidePointStore(self.project / "config/guide_points.json", capture)
        self.stop = Mock(return_value={"stopped": True})
        self.manager = RelocalizationManager(self.points, self.stop, project=self.project,
            runner=lambda initial, cancel: coarse_capture(self.project))
        self.manager.remember(seed())
        self.original = self.manager.seed_file.read_bytes()

    def tearDown(self):
        self.manager.cancel()
        if self.manager.thread:
            self.manager.thread.join(2)
        self.temp.cleanup()

    def run_seed(self, initial=None):
        self.manager.start_seed(initial or seed(MANUAL_SEED_NAME))
        self.manager.thread.join(2)
        self.assertFalse(self.manager.thread.is_alive())
        return self.manager.snapshot()

    def result_with_pose(self, dx=0, yaw=0):
        packet = capture()
        for sample in packet["samples"]:
            sample["x"] += dx
            sample["yaw"] += yaw
        return coarse_capture(self.project, packet)

    def test_half_meter_correction_accepted_and_saved_as_precise(self):
        self.manager.runner = lambda initial, cancel: self.result_with_pose(.5, .2)
        result = self.run_seed()
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["seed"]["localization_mode"], COARSE_MODE)
        saved = json.loads(self.manager.seed_file.read_text())
        self.assertEqual(saved["localization_mode"], PRECISE_MODE)
        self.assertNotIn("radius", saved)
        self.assertAlmostEqual(saved["pose"]["x"], 5.4)
        self.assertEqual(self.points.list(), [])
        self.stop.assert_called_once_with()

    def test_one_meter_exact_search_boundary_accepted(self):
        self.manager.runner = lambda initial, cancel: self.result_with_pose(1, math.pi / 4)
        self.assertEqual(self.run_seed()["state"], "succeeded")

    def test_correction_outside_radius_or_heading_never_persisted(self):
        for dx, yaw in ((1.001, 0), (0, math.pi / 4 + .001)):
            self.manager.runner = lambda initial, cancel, dx=dx, yaw=yaw: self.result_with_pose(dx, yaw)
            with self.subTest(dx=dx, yaw=yaw):
                self.assertEqual(self.run_seed()["state"], "failed")
                self.assertEqual(self.manager.seed_file.read_bytes(), self.original)

    def test_legacy_precise_twenty_centimeter_guard_not_relaxed(self):
        self.manager.runner = lambda initial, cancel: {"status": "success", **self.result_with_pose(.21)}
        for initial in (seed(), {**seed(MANUAL_SEED_NAME), "localization_mode": PRECISE_MODE}):
            with self.subTest(initial=initial):
                self.assertEqual(self.run_seed(initial)["state"], "failed")
                self.assertEqual(self.manager.seed_file.read_bytes(), self.original)

    def test_quality_failure_or_untrusted_success_never_persisted(self):
        for kind in ("absent", "wrong_id", "ambiguous", "overlap", "validation", "check_false",
                     "incomplete", "tf_mismatch", "tf_before_commit", "stale_tf", "map_change"):
            packet = self.result_with_pose()
            if kind == "absent":
                packet.pop("coarse_report")
            elif kind == "wrong_id":
                packet["coarse_report"]["request_id"] = "another-request"
            elif kind == "ambiguous":
                packet["coarse_report"]["ambiguous"] = True
            elif kind == "overlap":
                packet["coarse_report"]["inlier_ratio"] = .1
            elif kind == "validation":
                packet["coarse_report"]["validation_score"] = .05
            elif kind == "check_false":
                packet["localized"] = False
            elif kind == "incomplete":
                packet["coarse_report"]["candidate_count"] = 40
            elif kind == "tf_mismatch":
                packet["coarse_report"]["verified_pose"]["x"] += .11
            elif kind == "tf_before_commit":
                packet["coarse_report"]["finished_at"] = time.time()
            elif kind == "stale_tf":
                for sample in packet["samples"]:
                    sample["stamp"] -= 10
            else:
                packet["map_fingerprint"] = "b" * 64
            self.manager.runner = lambda initial, cancel, packet=packet: copy.deepcopy(packet)
            with self.subTest(kind=kind):
                self.assertEqual(self.run_seed()["state"], "failed")
                self.assertEqual(self.manager.seed_file.read_bytes(), self.original)


class CoarseServiceTests(unittest.TestCase):
    def setUp(self):
        self.project = Path("/tmp/mock-daolan-coarse")
        self.initial = validate_seed(seed(MANUAL_SEED_NAME))
        self.packet = coarse_capture(self.project)
        self.packet["coarse_requested_at"] = 1000
        self.report = self.packet["coarse_report"]
        self.report.update(started_at=1000, finished_at=1001)
        for index, sample in enumerate(self.packet["samples"]):
            sample["stamp"] = 1001.5 + index * .1
        self.rospy = Mock()
        self.service = Mock(return_value=types.SimpleNamespace(status=1,
            message=json.dumps({"request_id": self.report["request_id"]})))
        self.rospy.ServiceProxy.return_value = self.service
        self.check = Mock(return_value=types.SimpleNamespace(status=True))

    def invoke(self, reports=None, checks=None):
        self.rospy.get_param.side_effect = reports or [
            {"request_id": "old"}, {**self.report, "state": "matching"}, self.report, self.report]
        if checks is not None:
            self.check.side_effect = checks
        with patch.object(web_relocalize, "PROJECT", self.project), \
             patch.object(web_relocalize, "capture", return_value=copy.deepcopy(self.packet)) as capture_mock, \
             patch.object(web_relocalize.time, "time", side_effect=[1000] + [1002] * 20), \
             patch.object(web_relocalize.time, "sleep"):
            result = web_relocalize._coarse_request(self.rospy, Mock(), object, self.check, self.initial)
        self.capture_mock = capture_mock
        return result

    def test_new_request_matches_then_verified_capture_without_legacy_call(self):
        result = self.invoke()
        self.assertEqual(result["coarse_request_id"], self.report["request_id"])
        self.rospy.ServiceProxy.assert_called_once_with("/slam_reloc_coarse", object)
        self.check.assert_called_once_with(code=True)
        self.capture_mock.assert_called_once_with(init_ros=False)
        requested = self.service.call_args.kwargs
        self.assertEqual(requested["yaw"], self.initial["pose"]["yaw"])
        self.assertEqual((requested["roll"], requested["pitch"]), (0, 0))
        self.assertNotIn("radius", requested)

    def test_missing_new_service_is_actionable_and_never_falls_back(self):
        self.rospy.wait_for_service.side_effect = RuntimeError("missing")
        with self.assertRaisesRegex(RuntimeError, "重启更新后的定位模块"):
            self.invoke()
        self.rospy.ServiceProxy.assert_not_called()

    def test_reused_ack_or_non_json_ack_rejected(self):
        for message, old in (("RELOCALIZE CALLED!", {}),
                             (json.dumps({"request_id": "old"}), {"request_id": "old"}),
                             (json.dumps({"request_id": 1}), {})):
            self.service.return_value.message = message
            with self.subTest(message=message), self.assertRaises(RuntimeError):
                self.invoke([old])
        self.check.assert_not_called()

    def test_failure_false_check_and_report_replacement_cannot_succeed(self):
        for reports, checks in (
            ([{}, {**self.report, "state": "failed", "message": "ambiguous"}], None),
            ([{}, self.report], [types.SimpleNamespace(status=False)]),
            ([{}, self.report, {**self.report, "request_id": "concurrent"}], None),
            ([{}, {**self.report, "independent_scan_verified": False}], None)):
            self.check.reset_mock(side_effect=True)
            self.check.return_value = types.SimpleNamespace(status=True)
            with self.subTest(reports=reports), self.assertRaises(RuntimeError):
                self.invoke(reports, checks)

    def test_old_success_report_and_old_true_check_are_not_current_request(self):
        old = {**self.report, "request_id": "old"}
        with patch.object(web_relocalize.time, "monotonic", side_effect=[0, 0, 30]):
            with self.assertRaisesRegex(RuntimeError, "时限"):
                self.invoke([old, old])
        self.check.assert_not_called()

    def test_tf_disagreeing_with_independent_scan_is_rejected(self):
        for sample in self.packet["samples"]:
            sample["x"] += .2
        with self.assertRaises(RuntimeError):
            self.invoke()

    def test_in_progress_false_check_is_not_polled_as_success(self):
        # First report is pending. /check may be false then, but is only read
        # after this request's independently verified succeeded report.
        reports = [{}, {**self.report, "state": "validating"}, self.report, self.report]
        self.invoke(reports, [types.SimpleNamespace(status=True)])
        self.assertEqual(self.check.call_count, 1)


class RelocalizeDispatchTests(unittest.TestCase):
    def test_precise_confirmed_uses_corrected_base_service_registered_seed_legacy(self):
        for initial, expected in (({**seed(MANUAL_SEED_NAME), "localization_mode": PRECISE_MODE}, "/slam_reloc_base"),
                                  (seed("起点"), "/slam_reloc")):
            fake_ros = Mock()
            fake_ros.get_param.return_value = False
            check = Mock(return_value=types.SimpleNamespace(status=True))
            service = Mock(return_value=types.SimpleNamespace(status=1, message="RELOCALIZE CALLED!"))
            fake_ros.ServiceProxy.side_effect = lambda name, cls: check if name == "/slam_reloc_check" else service
            fake_tf = Mock()
            service_types = types.ModuleType("fastlio.srv")
            service_types.SlamReLoc = object
            modules = {"rospy": fake_ros, "rosservice": Mock(), "tf": fake_tf,
                       "fastlio": types.ModuleType("fastlio"), "fastlio.srv": service_types}
            with self.subTest(expected=expected), patch.dict(sys.modules, modules), \
                 patch.object(web_relocalize, "map_fingerprint", return_value="a" * 64), \
                 patch.object(web_relocalize, "sample_tf", return_value=capture()["samples"]), \
                 patch.object(web_relocalize, "capture", return_value={"status": "success", **capture()}), \
                 patch.object(web_relocalize.time, "sleep"):
                self.assertEqual(web_relocalize.relocalize(initial)["status"], "success")
            self.assertIn(expected, [call.args[0] for call in fake_ros.ServiceProxy.call_args_list])
            self.assertNotIn("/slam_reloc_coarse", [call.args[0] for call in fake_ros.ServiceProxy.call_args_list])


if __name__ == "__main__":
    unittest.main()
