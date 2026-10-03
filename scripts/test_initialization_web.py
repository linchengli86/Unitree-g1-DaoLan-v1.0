#!/usr/bin/env python3
"""Loopback HTTP initialization tests; ROS/SDK and movement are never used."""

import hashlib
import io
import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from guide_initialization import InitializationManager
from guide_map import GuideMap, pixel_to_world
from guide_relocalization import COARSE_MODE, PRECISE_MODE, RelocalizationManager
from test_guide_relocalization import coarse_capture
import test_guide_points as point_tests


class InitializationWebTests(point_tests.WebPointTests):
    def setUp(self):
        super().setUp()
        self.project = Path(self.directory.name)
        (self.project / "map").mkdir()
        self.map_yaml = self.project / "map/map.yaml"
        self.map_yaml.write_text("image: map.pgm\nresolution: 1.0\norigin: [0, 0, 0]\n"
                                 "negate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.196\n", encoding="utf-8")
        image = Image.new("L", (10, 10), 255)
        image.putpixel((0, 0), 205)  # Unknown; world (0.5, 9.5).
        image.putpixel((1, 0), 0)  # Occupied; world (1.5, 9.5).
        image.save(str(self.project / "map/map.pgm"))
        self.pcd = self.project / "G1Nav2D/src/fastlio2/PCD/map.pcd"
        self.pcd.parent.mkdir(parents=True)
        self.pcd.write_bytes(b"mock PCD map, never loaded by ROS\n")
        self.server.map = GuideMap(self.project)
        self.map_metadata = self.server.map.metadata()
        self.pose_provider = Mock(side_effect=point_tests.capture)
        self.server.points.pose_provider = self.pose_provider
        self.stop = Mock(return_value={"stopped": True, "message": "mock disabled"})
        self.relocation_runner = Mock(side_effect=self.localized_at_seed)
        self.server.relocation = RelocalizationManager(self.server.points, self.stop,
            idle=lambda: not self.tasks.active(), project=self.project, runner=self.relocation_runner)
        self.initialization_runner = Mock(side_effect=self.initialized_unlocalized)
        self.server.initialization = InitializationManager(self.project,
            runner=self.initialization_runner,
            idle=lambda: not self.tasks.active() and not self.server.relocation.active())
        self.tasks.cancel.return_value = False
        self.tasks.snapshot.return_value = None
        self.block_started = threading.Event()
        self.block_release = threading.Event()

    def tearDown(self):
        self.server.initialization.cancel()
        self.server.relocation.cancel()
        self.block_release.set()
        for manager in (self.server.initialization, self.server.relocation):
            if manager.thread:
                manager.thread.join(timeout=2)
        super().tearDown()

    def initialized_unlocalized(self, cancel, report):
        report("network", "succeeded", "PC2/雷达网络可达")
        report("sensors", "succeeded", "点云与 IMU 为模拟的就绪状态")
        report("safety", "succeeded", "模拟安全控制器已禁用运动")
        report("localization", "skipped", "没有可信 TF，等待人工地图初值")
        return {"status": "success", "localized": False, "needs_initial_pose": True,
                "motion_disabled": True, "map_fingerprint": self.map_metadata["map_fingerprint"],
                "services": {"lidar": True, "localizer": True, "safe_controller": True}}

    def localized_at_seed(self, seed, cancel):
        now = time.time()
        result = {"status": "success", "localized": True, "frame_id": "map",
                "map_fingerprint": seed["map_fingerprint"],
                "samples": [{"x": seed["pose"]["x"], "y": seed["pose"]["y"], "z": 0.0,
                             "yaw": seed["pose"]["yaw"], "stamp": now - 0.5 + index * 0.1}
                            for index in range(5)]}
        if seed.get("localization_mode") == COARSE_MODE:
            # Request-specific native report, finished before all five mock
            # TF samples. Its independent scan pose agrees with actual TF.
            return coarse_capture(self.project, result)
        return result

    def blocking_initialization(self, cancel, report):
        report("network", "running", "等待测试释放")
        self.block_started.set()
        while not self.block_release.wait(0.01):
            if cancel.is_set():
                raise InterruptedError("初始化已取消；不会启用运动")
        return self.initialized_unlocalized(cancel, report)

    def initialize(self):
        status, result = self.request("/api/init/start", {"robot_ready": True, "pin": "246810"})
        self.assertEqual(status, 200, result)
        self.server.initialization.thread.join(timeout=2)
        state = self.server.initialization.snapshot()
        self.assertEqual(state["state"], "succeeded", state)
        return state

    def start_blocked(self):
        self.initialization_runner.side_effect = self.blocking_initialization
        status, result = self.request("/api/init/start", {"robot_ready": True, "pin": "246810"})
        self.assertEqual(status, 200, result)
        self.assertTrue(self.block_started.wait(1))
        self.assertTrue(self.server.initialization.active())

    def manual_payload(self, column=8, row=1, **overrides):
        value = {**pixel_to_world(column, row, self.map_metadata), "yaw": 0.25,
                 "map_fingerprint": self.map_metadata["map_fingerprint"],
                 "confirmed_position": True, "pin": "246810"}
        value.update(overrides)
        return value

    def raw_get(self, path):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            response = opener.open(self.address + path, timeout=3)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, response.headers, response.read()

    def assert_no_actions(self):
        self.initialization_runner.assert_not_called()
        self.relocation_runner.assert_not_called()
        self.stop.assert_not_called()
        self.pose_provider.assert_not_called()
        self.speech.enqueue.assert_not_called()
        self.tasks.start.assert_not_called()

    def test_bootstrap_get_init_and_map_are_read_only(self):
        with patch.object(self.server.initialization, "_run_services") as hardware:
            for path in ("/", "/localization", "/assets/guide.js", "/assets/guide.css"):
                self.assertEqual(self.raw_get(path)[0], 200)
            status, result = self.request("/api/init")
            self.assertEqual(status, 200)
            self.assertTrue(result["requires_pin"])
            self.assertEqual(result["initialization"]["state"], "idle")
            self.assertTrue(result["initialization"]["needs_initial_pose"])
            self.assertFalse(result["initialization"]["automatic_motion_enabled"])
            self.assertEqual(self.request("/api/map")[1]["map"], self.map_metadata)
            self.assertEqual(self.request("/api/relocation")[0], 200)
            hardware.assert_not_called()
        self.assert_no_actions()

    def test_map_png_download_no_sniff_and_no_flip(self):
        status, headers, body = self.raw_get("/api/map/image")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Cache-Control"], "no-store")
        with Image.open(io.BytesIO(body)) as image:
            self.assertEqual(image.size, (10, 10))
            self.assertEqual(image.getpixel((0, 0)), 205)
            self.assertEqual(image.getpixel((0, 9)), 255)
        self.assert_no_actions()

    def test_map_png_accepts_frontend_fingerprint_query(self):
        status, headers, body = self.raw_get(
            "/api/map/image?fingerprint=" + self.map_metadata["map_fingerprint"])
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Cache-Control"], "no-store")
        with Image.open(io.BytesIO(body)) as image:
            self.assertEqual(image.size, (10, 10))
        self.assert_no_actions()

    def test_initialization_requires_pin_ready_and_no_extra_options(self):
        invalid = [{"robot_ready": True, "pin": "bad"}, {"pin": "246810"},
                   {"robot_ready": False, "pin": "246810"}, {"robot_ready": 1, "pin": "246810"},
                   {"robot_ready": "true", "pin": "246810"},
                   {"robot_ready": True, "pin": "246810", "arm": True}]
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertEqual(self.request("/api/init/start", payload)[0], 400)
        self.assert_no_actions()

    def test_unlocalized_initialization_is_ready_but_needs_manual_pose(self):
        state = self.initialize()
        self.assertFalse(state["localized"])
        self.assertTrue(state["needs_initial_pose"])
        self.assertTrue(state["motion_disabled"])
        self.assertFalse(state["automatic_motion_enabled"])
        self.assertNotIn("pose", state)
        self.assertEqual([event["id"] for event in state["stages"]], ["network", "sensors", "safety", "localization"])
        self.relocation_runner.assert_not_called()
        self.stop.assert_not_called()
        self.pose_provider.assert_not_called()
        self.tasks.start.assert_not_called()

    def test_unlocalized_runner_cannot_publish_a_fabricated_pose(self):
        def bad(cancel, report):
            result = self.initialized_unlocalized(cancel, report)
            result["pose"] = {"x": 0, "y": 0, "yaw": 0}
            return result
        self.initialization_runner.side_effect = bad
        self.request("/api/init/start", {"robot_ready": True, "pin": "246810"})
        self.server.initialization.thread.join(timeout=2)
        state = self.server.initialization.snapshot()
        self.assertNotIn("pose", state, "Unlocalized TF must not appear as a trustworthy origin pose")
        self.assertTrue(state["needs_initial_pose"])
        self.tasks.start.assert_not_called()

    def test_init_does_not_overlap_active_task_or_relocation(self):
        self.tasks.active.return_value = True
        self.assertEqual(self.request("/api/init/start", {"robot_ready": True, "pin": "246810"})[0], 503)
        self.tasks.active.return_value = False
        with patch.object(self.server.relocation, "active", return_value=True):
            self.assertEqual(self.request("/api/init/start", {"robot_ready": True, "pin": "246810"})[0], 503)
        self.assert_no_actions()

    def test_running_init_blocks_plan_point_pose_and_both_relocation_modes(self):
        prepared = self.server.prepare_plan({"title": "不运动的旧计划", "steps": [{"action": "wait", "parameters": {"seconds": 1}}]})
        token = prepared["confirmation"]
        self.start_blocked()
        requests = [
            ("/api/init/start", {"robot_ready": True, "pin": "246810"}),
            ("/api/plans/confirm", {"token": token, "pin": "246810"}),
            ("/api/plans/dry-run", {"token": token}),
            ("/api/plans/prepare", {"plan": {"title": "新计划", "steps": [{"action": "wait", "parameters": {"seconds": 1}}]}}),
            ("/api/points", {"name": "不能记录", "pin": "246810"}),
            ("/api/relocation/start", {"confirmed_position": True, "pin": "246810"}),
            ("/api/relocation/manual", self.manual_payload()),
        ]
        for path, payload in requests:
            with self.subTest(path=path):
                self.assertEqual(self.request(path, payload)[0], 503)
        self.assertEqual(self.request("/api/points/pose")[0], 503)
        self.assertEqual(self.server.pending, {})
        self.relocation_runner.assert_not_called()
        self.stop.assert_not_called()
        self.pose_provider.assert_not_called()
        self.tasks.start.assert_not_called()

    def test_stop_cancels_init_and_never_auto_arms(self):
        self.start_blocked()
        with patch.object(self.web, "stop_navigation", return_value={"stopped": True, "message": "mock stop"}) as stop_navigation:
            status, result = self.request("/api/stop", {})
            self.assertEqual(status, 200)
            self.assertTrue(result["task_cancelled"])
            self.assertTrue(result["stopped"])
            stop_navigation.assert_called_once_with()
        self.server.initialization.thread.join(timeout=2)
        state = self.server.initialization.snapshot()
        self.assertEqual(state["state"], "cancelled")
        self.assertFalse(state["automatic_motion_enabled"])
        self.assertNotIn("pose", state)
        self.relocation_runner.assert_not_called()
        self.tasks.start.assert_not_called()

    def test_manual_requires_completed_initialization(self):
        status, result = self.request("/api/relocation/manual", self.manual_payload())
        self.assertEqual(status, 503)
        self.assertIn("初始化", result["message"])
        for state in ("failed", "cancelled"):
            with self.subTest(state=state), patch.object(self.server.initialization, "snapshot", return_value={"state": state}):
                self.assertEqual(self.request("/api/relocation/manual", self.manual_payload())[0], 503)
        self.assert_no_actions()

    def test_missing_map_reports_unavailable_without_starting_services(self):
        self.map_yaml.unlink()
        status, result = self.request("/api/map")
        self.assertEqual(status, 503)
        self.assertEqual(result["status"], "error")
        status, headers, body = self.raw_get("/api/map/image")
        self.assertEqual(status, 503)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        self.assert_no_actions()

    def test_manual_requires_pin_explicit_position_and_strict_payload(self):
        self.initialize()
        invalid = [self.manual_payload(pin="bad"), self.manual_payload(confirmed_position=False),
                   self.manual_payload(confirmed_position=1), self.manual_payload(confirmed_position="true"),
                   self.manual_payload(action="navigate_to_point"), self.manual_payload(x=True),
                   self.manual_payload(y=float("nan")), self.manual_payload(yaw=float("inf")),
                   self.manual_payload(map_fingerprint="A" * 64),
                   self.manual_payload(radius=2.0), self.manual_payload(yaw_uncertainty=3.14)]
        missing = self.manual_payload()
        del missing["yaw"]
        invalid.append(missing)
        for payload in invalid:
            with self.subTest(keys=list(payload)):
                self.assertEqual(self.request("/api/relocation/manual", payload)[0], 400)
        self.stop.assert_not_called()
        self.relocation_runner.assert_not_called()
        self.tasks.start.assert_not_called()

    def test_manual_rejects_unknown_occupied_outside_and_changed_map_without_stop(self):
        self.initialize()
        for payload in [self.manual_payload(0, 0), self.manual_payload(1, 0),
                        self.manual_payload(x=10.0), self.manual_payload(x=-0.1),
                        self.manual_payload(map_fingerprint="a" * 64)]:
            with self.subTest(payload=payload):
                self.assertEqual(self.request("/api/relocation/manual", payload)[0], 400)
        stale = self.manual_payload()
        self.pcd.write_bytes(b"changed map")
        self.assertEqual(self.request("/api/relocation/manual", stale)[0], 400)
        self.stop.assert_not_called()
        self.relocation_runner.assert_not_called()
        self.tasks.start.assert_not_called()

    def test_manual_current_pose_is_not_limited_to_registered_points_and_preserves_registry(self):
        point = self.server.points.add({"name": "既有远处展点", "speech": "原先验资料"})
        original = hashlib.sha256(self.server.points.path.read_bytes()).hexdigest()
        self.pose_provider.reset_mock()
        self.initialize()
        payload = self.manual_payload()
        self.assertGreater(abs(payload["x"] - point["pose"]["x"]), 2.0)
        status, result = self.request("/api/relocation/manual", payload)
        self.assertEqual(status, 200, result)
        self.server.relocation.thread.join(timeout=2)
        final = self.server.relocation.snapshot()
        self.assertEqual(final["state"], "succeeded", final)
        self.assertEqual(final["seed"]["name"], "地图点选当前位置")
        self.assertEqual(final["seed"]["localization_mode"], COARSE_MODE)
        self.assertEqual(final["seed"]["radius"], 1.0)
        self.assertEqual(final["pose"], {"x": 8.5, "y": 8.5, "z": 0.0, "yaw": 0.25})
        self.assertTrue(final["coarse_report"]["independent_scan_verified"])
        saved = json.loads(self.server.relocation.seed_file.read_text())
        self.assertEqual(saved["localization_mode"], PRECISE_MODE)
        self.assertNotIn("radius", saved)
        self.assertEqual(self.relocation_runner.call_count, 1)
        self.stop.assert_called_once_with()
        self.pose_provider.assert_not_called()
        self.assertEqual(hashlib.sha256(self.server.points.path.read_bytes()).hexdigest(), original)
        self.assertEqual(self.server.points.list(), [point])
        self.tasks.start.assert_not_called()
        self.speech.enqueue.assert_not_called()

    def test_manual_coarse_accepts_one_meter_boundary_but_never_expands_it(self):
        self.initialize()
        original = self.localized_at_seed
        for correction, expected in ((0.5, "succeeded"), (1.0, "succeeded"), (1.001, "failed")):
            def corrected(seed, cancel, correction=correction):
                result = original(seed, cancel)
                for sample in result["samples"]:
                    sample["x"] += correction
                result["coarse_report"]["verified_pose"]["x"] += correction
                return result
            self.relocation_runner.side_effect = corrected
            seed_file = self.server.relocation.seed_file
            before = seed_file.read_bytes() if seed_file.exists() else None
            status, response = self.request("/api/relocation/manual", self.manual_payload())
            self.assertEqual(status, 200, response)
            self.server.relocation.thread.join(timeout=2)
            final = self.server.relocation.snapshot()
            with self.subTest(correction=correction):
                self.assertEqual(final["state"], expected, final)
                if expected == "succeeded":
                    self.assertAlmostEqual(final["pose"]["x"], 8.5 + correction)
                else:
                    self.assertIn("1 m", final["message"])
                    self.assertEqual(seed_file.read_bytes(), before)
                    self.assertNotIn("pose", final)
        self.assertEqual(self.stop.call_count, 3)
        self.pose_provider.assert_not_called()
        self.tasks.start.assert_not_called()
        self.speech.enqueue.assert_not_called()

    def test_manual_relocation_blocks_when_a_task_or_relocation_is_active(self):
        self.initialize()
        self.tasks.active.return_value = True
        self.assertEqual(self.request("/api/relocation/manual", self.manual_payload())[0], 503)
        self.tasks.active.return_value = False
        with patch.object(self.server.relocation, "active", return_value=True):
            self.assertEqual(self.request("/api/relocation/manual", self.manual_payload())[0], 503)
        self.stop.assert_not_called()
        self.relocation_runner.assert_not_called()
        self.tasks.start.assert_not_called()

    def test_manual_relocation_checks_actual_final_yaw_and_never_claims_success_on_wrong_heading(self):
        self.initialize()
        original = self.localized_at_seed
        def wrong_heading(seed, cancel):
            result = original(seed, cancel)
            for sample in result["samples"]:
                sample["yaw"] += 1.0
            result["coarse_report"]["verified_pose"]["yaw"] += 1.0
            return result
        self.relocation_runner.side_effect = wrong_heading
        status, result = self.request("/api/relocation/manual", self.manual_payload())
        self.assertEqual(status, 200, result)
        self.server.relocation.thread.join(timeout=2)
        final = self.server.relocation.snapshot()
        self.assertEqual(final["state"], "failed")
        self.assertIn("朝向", final["message"])
        self.assertNotIn("pose", final)
        self.tasks.start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
