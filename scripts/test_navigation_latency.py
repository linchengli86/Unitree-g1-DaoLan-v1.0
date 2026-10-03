#!/usr/bin/env python3
"""No ROS/hardware: finite, bounded observational diagnostics regression."""

import ast
import contextlib
import io
import json
import math
import struct
from pathlib import Path
import unittest
from unittest.mock import patch

import diagnose_navigation_latency as diagnostic


class StatisticsTests(unittest.TestCase):
    def test_bounded_percentiles_and_lifetime_max_are_distinct(self):
        stats = diagnostic.BoundedStats(3)
        for value in (100, 1, 2, 3, 4):
            self.assertTrue(stats.add(value))
        report = stats.summary()
        self.assertEqual(report["count"], 5)
        self.assertEqual(report["retained"], 3)
        self.assertEqual(report["p50"], 3)
        self.assertAlmostEqual(report["p95"], 3.9)
        self.assertEqual(report["max"], 100)
        self.assertEqual(report["percentiles_use"], "recent_retained_samples")

    def test_nonfinite_values_and_booleans_are_rejected(self):
        stats = diagnostic.BoundedStats()
        for value in (math.nan, math.inf, -math.inf, True, "1", None):
            self.assertFalse(stats.add(value))
        report = stats.summary()
        self.assertEqual(report["rejected"], 6)
        self.assertIsNone(report["p95"])
        json.dumps(report, allow_nan=False)

    def test_series_tracks_header_age_gap_signed_velocity_and_owner(self):
        series = diagnostic.TopicSeries()
        series.add(1.0, .1, -.2, .3, "/move_base")
        series.add(1.2, .5, .6, -.7, "/move_base")
        report = series.summary()
        self.assertAlmostEqual(report["receipt_gap_seconds"]["max"], .2)
        self.assertEqual(report["header_age_seconds"]["max"], .5)
        self.assertEqual(report["vx_m_per_s"]["min"], -.2)
        self.assertEqual(report["wz_rad_per_s"]["abs_max"], .7)
        self.assertEqual(report["callerids"], ["/move_base"])
        self.assertFalse(series.add(1.1, .1))


class RecorderTests(unittest.TestCase):
    def recorder(self, duration=30, **kwargs):
        recorder = diagnostic.PassiveRecorder(0.0, **kwargs)
        recorder.begin(1.0, duration)
        return recorder

    def test_three_second_buckets_and_source_vs_common_tf_age(self):
        recorder = self.recorder()
        recorder.record("tf_edge:map->local@/localizer_node", 1.1, 100.0, 99.9, caller="/localizer_node")
        recorder.record("tf_common:map->base_link", 1.1, 100.0, 99.2)
        recorder.record("/scan", 4.1, 103.0, 102.8)
        report = recorder.summary(5.0, "test")
        self.assertEqual([bucket["start_seconds"] for bucket in report["three_second_buckets"]], [0, 3])
        self.assertAlmostEqual(report["topics"]["tf_common:map->base_link"]["header_age_seconds"]["max"], .8)
        self.assertAlmostEqual(report["topics"]["tf_edge:map->local@/localizer_node"]["header_age_seconds"]["max"], .1)
        self.assertIn("not_move_base_internal_cache", report["tf_common_scope"])

    def test_active_alone_does_not_claim_motion_or_physical_movement(self):
        recorder = self.recorder()
        recorder.status([("goal-a", 1)], 1.1)
        report = recorder.summary(2, "test")
        self.assertTrue(report["navigation_attempt_observed"])
        self.assertFalse(report["motion_command_observed"])
        self.assertFalse(report["physical_motion_verified"])
        recorder.record("/navigation_odom", 1.2, 100, 99.9, .3, .2)
        self.assertFalse(recorder.summary(2, "test")["motion_command_observed"])
        recorder.record("/cmd_vel", 1.3, 100, vx=.3, wz=0)
        self.assertTrue(recorder.summary(2, "test")["raw_nonzero_command_observed"])
        self.assertFalse(recorder.summary(2, "test")["smooth_nonzero_command_observed"])

    def test_old_terminal_does_not_stop_and_observed_terminal_has_eight_second_tail(self):
        recorder = self.recorder()
        recorder.status([("old-goal", 3)], 1.1)
        self.assertIsNone(recorder.observation()[1])
        recorder.status([("new-goal", 1)], 1.2)
        recorder.status([("new-goal", 4)], 2.0)
        self.assertEqual(recorder.observation(), (True, 2.0))
        recorder.status([("other-goal", 1)], 2.2)
        self.assertIsNone(recorder.observation()[1])

    def test_invalid_or_out_of_capture_records_are_not_retained(self):
        recorder = self.recorder(duration=6)
        self.assertFalse(recorder.record("/scan", -.1, 100, 99.9))
        self.assertFalse(recorder.record("/scan", 7.0, 106, 105.9))
        self.assertFalse(recorder.record("/scan", 1.1, 100, math.nan))
        self.assertFalse(recorder.record("/cmd_vel", 1.2, 100, vx=math.inf, wz=0))
        report = recorder.summary(8, "test")
        self.assertEqual(report["invalid_records"], 2)
        self.assertEqual(report["topics"], {})
        self.assertEqual(report["capture_seconds"], 6)

    def test_stream_samples_logs_status_and_goal_ids_are_bounded(self):
        recorder = self.recorder(max_streams=2, sample_limit=3)
        for index in range(1200):
            now = 1.0 + index / 100
            recorder.record("/scan", now, now + 100, now + 99)
            recorder.warning("/move_base", 4, "warning", now)
            recorder.status([("goal-" + str(index), 1)], now)
        recorder.record("/tf", 2.6, 102, 101)
        self.assertFalse(recorder.record("extra", 2.7, 102, 101))
        self.assertEqual(len(recorder.topics), 2)
        self.assertEqual(len(recorder.topics["/scan"].samples), 3)
        self.assertEqual(len(recorder.logs), 1024)
        self.assertEqual(len(recorder.status_events), 512)
        self.assertEqual(len(recorder.observed_goals), 64)

    def test_warning_exposes_move_base_reported_pose_age_separately(self):
        recorder = self.recorder()
        recorder.warning("/move_base", 4,
                         "Costmap transform timeout. Current time: 100.7, global_pose stamp: 100.0, tolerance: 0.5",
                         1.1, 99.9, 100.0)
        recorder.warning("/unitree_safe_controller", 2, "Motion ENABLED", 1.2)
        recorder.warning("/move_base", 2, "ordinary info", 1.3)
        recorder.warning("/unrelated", 8, "irrelevant", 1.4)
        report = recorder.summary(2, "test")
        self.assertEqual(len(report["relevant_logs"]), 2)
        warning = report["relevant_logs"][0]
        self.assertAlmostEqual(warning["move_base_reported_pose_age_seconds"], .7)
        self.assertEqual(warning["move_base_transform_tolerance_seconds"], .5)
        json.dumps(report, allow_nan=False)

    def test_wait_phase_keeps_preflight_evidence_before_any_active_goal(self):
        recorder = diagnostic.PassiveRecorder(0.0)
        self.assertTrue(recorder.record("/cmd_vel", 2.0, 100, vx=.3, wz=0))
        recorder.status([("goal-a", 1)], 3.0)
        self.assertEqual(recorder.observation(), (True, None))
        recorder.begin(3.1, 30)
        recorder.record("/cmd_vel_smooth", 3.2, 100, vx=0, wz=.2)
        report = recorder.summary(4, "test")
        self.assertIn("/cmd_vel", report["topics"])
        self.assertTrue(report["raw_nonzero_command_observed"])
        self.assertTrue(report["smooth_nonzero_command_observed"])
        self.assertTrue(report["navigation_attempt_observed"])

    def test_no_active_goal_still_has_rolling_source_trace_and_wait_timeout(self):
        recorder = diagnostic.PassiveRecorder(0.0)
        for second in range(101):
            recorder.record("/livox/imu", float(second), second + 1000, second + 999.99)
        self.assertIsNone(recorder.stop_reason(99.0, 100.0))
        self.assertEqual(recorder.stop_reason(100.0, 100.0), "wait_for_goal_timeout")
        report = recorder.summary(100.0, "wait_for_goal_timeout")
        self.assertFalse(report["navigation_attempt_observed"])
        self.assertEqual(report["capture_seconds"], 0)
        self.assertIsNone(report["capture_start_offset_seconds"])
        self.assertEqual(report["retained_window_start_offset_seconds"], 80.0)
        self.assertEqual(report["topics"]["/livox/imu"]["process_received_total"], 101)
        self.assertEqual(report["topics"]["/livox/imu"]["retained_window_received"], 21)

    def test_capture_keeps_twenty_second_preflight_and_all_time_axes_align(self):
        recorder = diagnostic.PassiveRecorder(0.0)
        recorder.record("/scan", 5.0, 105.0, 104.9)
        recorder.record("/scan", 25.0, 125.0, 124.9)
        recorder.warning("/move_base", 4, "warning", 25.0)
        recorder.begin(30.0, 30.0)
        recorder.status([("goal", 1)], 31.0)
        recorder.record("/scan", 31.0, 131.0, 130.9)
        report = recorder.summary(32.0, "interrupted")
        self.assertEqual(report["capture_start_offset_seconds"], 30.0)
        self.assertEqual(report["retained_window_start_offset_seconds"], 10.0)
        self.assertEqual([item["start_seconds"] for item in report["three_second_buckets"]], [24, 30])
        self.assertEqual(report["relevant_logs"][0]["elapsed_seconds"], 25.0)
        self.assertEqual(report["status_events"][0]["elapsed_seconds"], 31.0)
        samples = report["topics"]["/scan"]["recent_header_receipt_samples"]
        self.assertEqual([sample["elapsed_seconds"] for sample in samples], [25.0, 31.0])

    def test_high_frequency_capture_cannot_evict_frozen_preflight_ring(self):
        recorder = diagnostic.PassiveRecorder(0.0, sample_limit=3)
        for received in (1.0, 2.0, 3.0):
            recorder.record("/livox/imu", received, received + 100, received + 99.99)
        recorder.begin(4.0, 30.0)
        for received in range(4, 20):
            recorder.record("/livox/imu", float(received), received + 100, received + 99.99)
        self.assertEqual(len(recorder.topics["/livox/imu"].preflight_snapshot), 3)
        self.assertEqual(len(recorder.topics["/livox/imu"].samples), 3)
        report = recorder.summary(20.0, "test")["topics"]["/livox/imu"]
        self.assertEqual(report["preflight_statistics"]["received"], 3)
        self.assertEqual(report["capture_statistics"]["received"], 3)
        self.assertEqual([row["elapsed_seconds"] for row in report["preflight_header_receipt_samples"]], [1, 2, 3])
        self.assertEqual(report["retained_window_received"], 6)

    def test_tail_silence_includes_quiet_and_never_received_topics(self):
        recorder = diagnostic.PassiveRecorder(0.0)
        recorder.expect_topics(["/scan", "/navigation_odom"])
        recorder.record("/scan", 1.0, 101.0, 100.9)
        report = recorder.summary(30.0, "interrupted")
        self.assertEqual(report["topics"]["/scan"]["tail_silence_seconds"], 29.0)
        self.assertEqual(report["topics"]["/scan"]["retained_window_received"], 0)
        self.assertEqual(report["topics"]["/navigation_odom"]["tail_silence_seconds"], 30.0)
        self.assertFalse(report["topics"]["/navigation_odom"]["ever_received"])

    def test_critical_first_controller_events_survive_log_capacity(self):
        recorder = self.recorder()
        recorder.warning("/unitree_safe_controller", 2, "Initializing Unitree LocoClient", 1.0)
        recorder.warning("/unitree_safe_controller", 4, "StopMove sent (startup)", 1.1)
        recorder.warning("/unitree_safe_controller", 4, "Motion ENABLED", 1.2)
        for index in range(1100):
            recorder.warning("/move_base", 4, "warning", 2.0 + index / 100.0)
        report = recorder.summary(20.0, "test")
        self.assertEqual(len(report["relevant_logs"]), 1024)
        self.assertEqual(report["controller_milestones"]["first_enable"]["elapsed_seconds"], 1.2)
        self.assertIn("startup", report["controller_milestones"]["first_stop"]["message"])
        self.assertIn("Initializing", report["controller_milestones"]["first_start"]["message"])


class HeaderOnlyTests(unittest.TestCase):
    def payload(self, point_num=2, count=2, offsets=(0, 100000000)):
        header = struct.pack("<IIII", 1, 100, 0, 4) + b"body"
        metadata = struct.pack("<QIB3BI", 100000000000, point_num, 1, 0, 0, 0, count)
        points = b"".join(struct.pack("<IfffBBB", offset, math.nan, 0.0, 0.0, 0, 0, 0)
                          for offset in offsets)
        return header + metadata + points

    def test_header_and_raw_last_offset_read_without_deserializing_cloud(self):
        payload = self.payload()
        self.assertEqual(diagnostic.decode_header_stamp(memoryview(payload)), 100.0)
        begin, end, count = diagnostic.decode_livox_timing(payload)
        self.assertEqual(begin, 100.0)
        self.assertAlmostEqual(end, 100.1)
        self.assertEqual(count, 2)
        self.assertEqual(struct.calcsize("<IfffBBB"), 19)

    def test_livox_length_count_and_order_validation(self):
        for payload in (self.payload()[:-1], self.payload() + b"x", self.payload(point_num=3),
                        self.payload(count=1), self.payload(offsets=(100, 0))):
            with self.assertRaises(ValueError):
                diagnostic.decode_livox_timing(payload)
        self.assertEqual(diagnostic.decode_livox_timing(self.payload(0, 0, ())), (100.0, None, 0))

    def test_header_nanoseconds_and_frame_string_bounds(self):
        for payload in (b"", struct.pack("<IIII", 0, 100, 1000000000, 0),
                        struct.pack("<IIII", 0, 100, 0, 5000),
                        struct.pack("<IIII", 0, 100, 0, 4) + b"x"):
            with self.assertRaises(ValueError):
                diagnostic.decode_header_stamp(payload)


class PassiveCliTests(unittest.TestCase):
    def test_arguments_bounded_before_any_ros_import_or_call(self):
        for argv in (["--duration", "181"], ["--duration", "nan"], ["--duration", "0"],
                     ["--wait-for-goal", "601"], ["--wait-for-goal", "-1"]):
            with patch.object(diagnostic, "run_ros") as run, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    diagnostic.main(argv)
                run.assert_not_called()
        with patch.object(diagnostic, "run_ros", return_value=0) as run:
            self.assertEqual(diagnostic.main([]), 0)
            run.assert_called_once_with(30, 0)

    def test_ros_is_lazy_and_script_contains_no_write_apis(self):
        source = Path(diagnostic.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for statement in tree.body:
            if isinstance(statement, (ast.Import, ast.ImportFrom)):
                module = getattr(statement, "module", "") or ""
                names = [alias.name for alias in statement.names]
                self.assertFalse(module.startswith(("rospy", "tf", "geometry_msgs", "nav_msgs")))
                self.assertFalse(any(name in {"rospy", "tf"} for name in names))
        forbidden = {"Publisher", "ServiceProxy", "SimpleActionClient", "set_param", "publish", "send_goal",
                     "make_plan", "Move", "StopMove", "open", "write_text", "write_bytes"}
        calls = {node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
                 for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, (ast.Name, ast.Attribute))}
        self.assertFalse(calls & forbidden)
        self.assertIn("Subscriber", calls)
        self.assertIn("getLatestCommonTime", calls)


if __name__ == "__main__":
    unittest.main()
