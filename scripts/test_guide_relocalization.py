#!/usr/bin/env python3
"""No-hardware relocalization policy, state, persistence and HTTP tests."""

import copy
import json
import math
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from guide_points import GuidePointStore
from guide_relocalization import RelocalizationManager, validate_seed
from capture_guide_pose import file_digest
from test_guide_points import WebPointTests, capture


def seed(name="最近确认位置"):
    return {"name": name, "frame_id": "map", "pose": {"x": 4.9, "y": -1.1, "z": 0, "yaw": 0.79},
            "map_fingerprint": "a" * 64}


def coarse_capture(project, data=None):
    """Mock native request report; no ROS, matching or hardware execution."""
    now = time.time()
    data = capture() if data is None else data
    return {"status": "success", **data, "coarse_request_id": "test-request",
            "coarse_requested_at": now - 2,
            "coarse_report": {"request_id": "test-request", "started_at": now - 2,
                "finished_at": now - 1, "state": "succeeded",
                "map_path": str(Path(project) / "G1Nav2D/src/fastlio2/PCD/map.pcd"),
                "score": 0.01, "inlier_ratio": 0.8, "count": 120, "candidate_count": 65,
                "final_candidates": 1, "validation_score": 0.01,
                "validation_inlier_ratio": 0.8, "validation_count": 120,
                "ambiguous": False, "pose_verified": True, "independent_scan_verified": True,
                "verified_pose": {key: data["samples"][-1][key] for key in ("x", "y", "z", "yaw")}}}


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.points = GuidePointStore(Path(self.temp.name) / "config" / "guide_points.json", capture)
        self.events = []
        self.manager = RelocalizationManager(self.points,
            stop=lambda: self.events.append("stop") or {"stopped": True}, project=self.temp.name,
            runner=lambda initial, cancel: self.events.append("relocate") or {"status": "success", **capture()})
        self.manager.remember(seed())

    def tearDown(self):
        self.manager.cancel()
        if self.manager.thread:
            self.manager.thread.join(2)
        self.temp.cleanup()

    def finish(self):
        self.manager.thread.join(2)
        self.assertFalse(self.manager.thread.is_alive())
        return self.manager.snapshot()

    def test_last_seed_and_start_priority(self):
        self.assertEqual(self.manager.choose_seed()["name"], "最近确认位置")
        start = self.points.add({"name": "起点"})
        self.assertEqual(self.manager.choose_seed(), validate_seed(start))
        point = self.points.add({"name": "展板"})
        self.assertEqual(self.manager.choose_seed(point["id"])["name"], "展板")
        with self.assertRaises(ValueError):
            self.manager.choose_seed("missing")

    def test_stop_before_relocate_and_persist(self):
        self.manager.start()
        result = self.finish()
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(self.events, ["stop", "relocate"])
        self.assertAlmostEqual(json.loads(self.manager.seed_file.read_text())["pose"]["yaw"], 0.79)

    def test_stop_unconfirmed_never_calls_runner(self):
        self.manager.stop = lambda: {"stopped": False}
        self.manager.start()
        self.assertEqual(self.finish()["state"], "failed")
        self.assertEqual(self.events, [])

    def test_manual_seed_is_not_a_registered_point(self):
        self.manager.runner = lambda initial, cancel: self.events.append("relocate") or coarse_capture(self.temp.name)
        self.manager.start_seed(seed("地图点选当前位置"))
        self.assertEqual(self.finish()["state"], "succeeded")
        self.assertEqual(self.points.list(), [])
        self.assertEqual(self.events, ["stop", "relocate"])

    def test_manual_heading_mismatch_is_not_success(self):
        def different_heading(initial, cancel):
            data = capture()
            for sample in data["samples"]:
                sample["yaw"] += math.pi
            return coarse_capture(self.temp.name, data)
        self.manager.runner = different_heading
        self.manager.start_seed(seed("地图点选当前位置"))
        result = self.finish()
        self.assertEqual(result["state"], "failed")
        self.assertIn("朝向", result["message"])
        self.assertEqual(json.loads(self.manager.seed_file.read_text())["name"], "最近确认位置")

    def test_manual_heading_wrap_uses_smallest_angle(self):
        initial = seed("地图点选当前位置")
        initial["pose"]["yaw"] = math.pi - 0.01
        def wrapped(initial, cancel):
            data = capture()
            for sample in data["samples"]:
                sample["yaw"] = -math.pi + 0.01
            return coarse_capture(self.temp.name, data)
        self.manager.runner = wrapped
        self.manager.start_seed(initial)
        self.assertEqual(self.finish()["state"], "succeeded")

    def test_false_map_stale_drift_rejected(self):
        for kind in ("localized", "map", "stale", "drift"):
            def invalid(initial, cancel, kind=kind):
                result = {"status": "success", **capture()}
                if kind == "localized":
                    result["localized"] = False
                elif kind == "map":
                    result["map_fingerprint"] = "b" * 64
                elif kind == "stale":
                    for s in result["samples"]:
                        s["stamp"] -= 10
                else:
                    for s in result["samples"]:
                        s["x"] += 1
                return result
            self.manager.runner = invalid
            self.manager.start()
            with self.subTest(kind=kind):
                self.assertEqual(self.finish()["state"], "failed")

    def test_duplicate_and_cancel(self):
        entered = threading.Event()
        def wait(initial, cancel):
            entered.set()
            cancel.wait(1)
            return {"status": "success", **capture()}
        self.manager.runner = wait
        self.manager.start()
        self.assertTrue(entered.wait(1))
        with self.assertRaises(RuntimeError):
            self.manager.start()
        self.assertTrue(self.manager.cancel())
        self.assertEqual(self.finish()["state"], "cancelled")

    def test_bad_seed_rejected_without_stop(self):
        for invalid in [{**seed(), "frame_id": "odom"}, {**seed(), "map_fingerprint": "bad"},
                        {**seed(), "pose": {**seed()["pose"], "x": math.nan}},
                        {**seed(), "pose": {**seed()["pose"], "x": True}}]:
            with self.assertRaises(ValueError):
                self.manager.remember(invalid)
        self.assertEqual(self.events, [])

    def test_map_digest_detects_same_size_changes(self):
        path = Path(self.temp.name) / "map.pgm"
        path.write_bytes(b"map-one")
        first = file_digest(path)
        self.assertEqual(file_digest(path), first)
        path.write_bytes(b"map-two")
        self.assertNotEqual(file_digest(path), first)


class WebRelocationTests(WebPointTests):
    # Parent HTTP point tests are intentionally exercised again alongside
    # the relocalization guard; each uses an isolated temporary store.
    def test_relocation_pin_confirmation_and_unknown_fields(self):
        self.server.relocation.remember(seed())
        for payload in [{"pin": "246810"}, {"pin": "bad", "confirmed_position": True},
                        {"pin": "246810", "confirmed_position": True, "x": 0}]:
            self.assertEqual(self.request("/api/relocation/start", payload)[0], 400)
        self.assertFalse(self.server.relocation.active())

    def test_http_start_and_task_guard(self):
        self.server.relocation.remember(seed())
        event = threading.Event()
        self.server.relocation.runner = lambda initial, cancel: event.wait(1) or {"status": "success", **capture()}
        try:
            status, body = self.request("/api/relocation/start", {"pin": "246810", "confirmed_position": True})
            self.assertEqual(status, 200)
            self.assertEqual(body["relocation"]["state"], "running")
            self.assertEqual(self.request("/api/plans/prepare", {"plan": {"title": "x", "steps": [{"action": "stop", "parameters": {}}]}})[0], 503)
            self.assertEqual(self.request("/api/points", {"name": "x", "pin": "246810"})[0], 503)
            self.assertEqual(self.request("/api/relocation/start", {"pin": "246810", "confirmed_position": True})[0], 503)
            self.assertEqual(self.request("/api/relocation")[1]["relocation"]["state"], "running")
        finally:
            self.server.relocation.cancel()
            event.set()
            self.server.relocation.thread.join(2)
        self.tasks.start.assert_not_called()
        self.speech.enqueue.assert_not_called()


if __name__ == "__main__":
    unittest.main()
