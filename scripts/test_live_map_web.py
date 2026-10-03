#!/usr/bin/env python3
"""Live-map HTTP regressions using only loopback and mocked robot services."""

import base64
import hashlib
import json
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import test_guide_points as point_tests


class LiveMapWebTests(unittest.TestCase):
    # Reuse the SDK-isolated HTTP fixture without inheriting unrelated tests.
    request = point_tests.WebPointTests.request

    def setUp(self):
        # The manager constructor is lazy; mock it before the fixture creates
        # the server so even a future subscription cannot touch actual ROS.
        import guide_live_map
        self.original_manager_class = guide_live_map.LiveMapManager
        self.live = Mock()
        self.live.snapshot.return_value = {
            "state": "ready", "ready": True, "localized": True,
            "frame_id": "map", "map_fingerprint": "a" * 64,
            "pose": {"x": 4.9, "y": -1.1, "yaw": 0.79},
            "obstacles": [[5.1, -1.0]],
        }
        self.factory = patch.object(guide_live_map, "LiveMapManager", return_value=self.live)
        self.factory_mock = self.factory.start()
        # Another test file may already have imported the HTTP module.
        import sys
        module = sys.modules.get("mobile_guide_server")
        self.web_factory = patch.object(module, "LiveMapManager", return_value=self.live) if module else None
        if self.web_factory:
            self.web_factory_mock = self.web_factory.start()
        try:
            point_tests.WebPointTests.setUp(self)
        except Exception:
            if self.web_factory:
                self.web_factory.stop()
            elif "mobile_guide_server" in sys.modules:
                sys.modules["mobile_guide_server"].LiveMapManager = self.original_manager_class
            self.factory.stop()
            raise
        image = b"\xff\xd8\xfffake-live-map-jpeg\xff\xd9"
        self.point = self.server.points.add({
            "name": "保留的展点", "speech": "已保存的先验。",
            "image_jpeg_base64": base64.b64encode(image).decode("ascii"),
        })
        self.original_registry = hashlib.sha256(self.server.points.path.read_bytes()).hexdigest()
        self.original_photo = self.server.points.image_path(self.point["id"]).read_bytes()
        self.pose_provider = Mock(side_effect=point_tests.capture)
        self.server.points.pose_provider = self.pose_provider
        self.server.initialization.start = Mock()
        self.server.relocation.start = Mock()
        self.server.relocation.start_seed = Mock()
        self.server.stop_actions = Mock()
        self.server.prepare_plan = Mock()
        self.server.query_board = Mock()
        self.server.trigger_assistant = Mock()

    def tearDown(self):
        try:
            point_tests.WebPointTests.tearDown(self)
        finally:
            if self.web_factory:
                self.web_factory.stop()
            else:
                # A first import binds the patched class with ``from ...``;
                # restore that module reference as well as its source module.
                self.web.LiveMapManager = self.original_manager_class
            self.factory.stop()

    def raw_request(self, path, payload=None):
        request = urllib.request.Request(self.address + path,
            data=None if payload is None else json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            response = opener.open(request, timeout=3)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, response.headers, response.read()

    def assert_read_only(self):
        self.assertEqual(hashlib.sha256(self.server.points.path.read_bytes()).hexdigest(),
                         self.original_registry)
        self.assertEqual(self.server.points.image_path(self.point["id"]).read_bytes(), self.original_photo)
        self.pose_provider.assert_not_called()
        self.server.initialization.start.assert_not_called()
        self.server.relocation.start.assert_not_called()
        self.server.relocation.start_seed.assert_not_called()
        self.tasks.start.assert_not_called()
        self.tasks.cancel.assert_not_called()
        self.speech.enqueue.assert_not_called()
        self.speech.speak_and_wait.assert_not_called()
        self.server.stop_actions.assert_not_called()
        self.server.prepare_plan.assert_not_called()
        self.server.query_board.assert_not_called()
        self.server.trigger_assistant.assert_not_called()
        self.assertEqual(self.server.pending, {})

    def test_live_json_is_no_store_and_read_only_without_operator_pin(self):
        status, headers, raw = self.raw_request("/api/map/live")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        self.assertEqual(json.loads(raw), {"status": "success", "live": self.live.snapshot.return_value})
        self.live.snapshot.assert_called_once_with()
        self.assert_read_only()

    def test_unknown_and_stale_never_manufacture_a_pose(self):
        for state in ("unknown", "stale", "unlocalized"):
            self.live.snapshot.return_value = {
                "state": state, "ready": False, "localized": False,
                "frame_id": "map", "pose": None, "obstacles": [],
                "message": "尚无可用的实时定位",
            }
            with self.subTest(state=state):
                status, result = self.request("/api/map/live")
                self.assertEqual(status, 200)
                self.assertIsNone(result["live"]["pose"])
                self.assertIs(result["live"]["ready"], False)
                self.assertIs(result["live"]["localized"], False)
                self.assertNotIn("initialization", result)
        self.assert_read_only()

    def test_many_reads_share_the_server_manager_and_never_restart_services(self):
        original_manager = self.server.live_map
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.request("/api/map/live"), range(12)))
        self.assertTrue(all(status == 200 for status, _ in results))
        self.assertIs(self.server.live_map, original_manager)
        self.assertIs(original_manager, self.live)
        self.assertEqual(self.live.snapshot.call_count, 12)
        constructor = self.web_factory_mock if self.web_factory else self.factory_mock
        constructor.assert_called_once()
        self.assert_read_only()

    def test_monitoring_remains_read_only_during_an_existing_task_or_initialization(self):
        self.tasks.active.return_value = True
        self.server.initialization.active = Mock(return_value=True)
        self.server.relocation.active = Mock(return_value=True)
        status, result = self.request("/api/map/live")
        self.assertEqual(status, 200)
        self.assertEqual(result["live"], self.live.snapshot.return_value)
        self.assert_read_only()

    def test_live_page_and_assets_do_not_read_sensors_or_trigger_actions(self):
        paths = ("/map", "/assets/guide.css", "/assets/guide.js")
        for path in paths:
            with self.subTest(path=path):
                status, headers, raw = self.raw_request(path)
                self.assertEqual(status, 200)
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertTrue(raw)
                expected = "text/css" if path.endswith(".css") else "application/javascript" if path.endswith(".js") else "text/html"
                self.assertTrue(headers["Content-Type"].startswith(expected))
                if expected == "text/html":
                    self.assertIn('id="stop"', raw.decode("utf-8"))
        self.live.snapshot.assert_not_called()
        self.assert_read_only()

    def test_snapshot_failure_is_503_json_and_not_fake_ready(self):
        self.live.snapshot.side_effect = RuntimeError("实时订阅暂不可用")
        status, headers, raw = self.raw_request("/api/map/live")
        result = json.loads(raw)
        self.assertEqual(status, 503)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(result["status"], "error")
        self.assertIn("实时订阅暂不可用", result["message"])
        self.assertNotIn("live", result)
        self.assertNotIn("pose", result)
        self.assert_read_only()

    def test_live_endpoint_rejects_post_and_does_not_accept_motion_commands(self):
        status, _, raw = self.raw_request("/api/map/live", {"pin": "246810", "x": 5, "action": "move"})
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(raw)["status"], "error")
        self.live.snapshot.assert_not_called()
        self.assert_read_only()


if __name__ == "__main__":
    unittest.main()
