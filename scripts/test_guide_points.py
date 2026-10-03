#!/usr/bin/env python3
"""Hardware-free guide point capture/storage/board regressions."""

import base64
import copy
import json
import math
import os
import sys
import subprocess
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from guide_points import GuidePointStore, decode_board_image, run_ros_json, validate_board_draft, validate_samples


def capture():
    now = time.time()
    return {"localized": True, "frame_id": "map", "map_fingerprint": "a" * 64,
            "samples": [{"x": 4.9, "y": -1.1, "z": 0.02, "yaw": 0.79, "stamp": now - 0.5 + i * 0.1} for i in range(5)]}


class PointTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "config" / "guide_points.json"
        self.store = GuidePointStore(self.path, capture)

    def tearDown(self):
        self.directory.cleanup()

    def test_capture_and_restart(self):
        point = self.store.add({"name": "  入口  ", "speech": "欢迎参观。"})
        self.assertEqual(point["name"], "入口")
        self.assertAlmostEqual(point["pose"]["yaw"], 0.79)
        self.assertEqual(point["pose"]["z"], 0)
        self.assertEqual(GuidePointStore(self.path, capture).list(), [point])

    def test_duplicate_preserves_original(self):
        original = self.store.add({"name": "入口"})
        with self.assertRaisesRegex(ValueError, "名称已存在"):
            self.store.add({"name": "入口"})
        self.assertEqual(self.store.list(), [original])

    def test_reject_metadata_and_coordinate_injection(self):
        for payload in [{"name": ""}, {"name": "a" * 41}, {"name": "x", "speech": "a" * 151},
                        {"name": "x", "pose": {"x": 0}}, {"name": "x", "gesture": "shell"}]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.store.add(payload)
        self.assertFalse(self.path.exists())

    def test_only_verified_gesture(self):
        with patch.dict(os.environ, {"GUIDE_VERIFIED_ARM_ACTIONS": ""}), self.assertRaises(ValueError):
            self.store.add({"name": "入口", "gesture": "wave"})
        with patch.dict(os.environ, {"GUIDE_VERIFIED_ARM_ACTIONS": "wave"}):
            self.assertEqual(self.store.add({"name": "入口", "gesture": "wave"})["gesture"], "wave")

    def test_invalid_localization_and_map(self):
        for field, value in [("localized", False), ("frame_id", "odom"), ("map_fingerprint", "")]:
            data = capture()
            data[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                GuidePointStore(self.path, lambda: data).add({"name": "入口"})
        self.assertFalse(self.path.exists())

    def test_stale_frozen_moving_nan(self):
        data = capture()
        for kind in ("stale", "frozen", "position", "yaw", "nan"):
            samples = copy.deepcopy(data["samples"])
            if kind == "stale":
                for s in samples:
                    s["stamp"] -= 10
            elif kind == "frozen":
                for s in samples:
                    s["stamp"] = samples[0]["stamp"]
            elif kind == "position":
                samples[-1]["x"] += 0.10
            elif kind == "yaw":
                samples[-1]["yaw"] += 0.15
            else:
                samples[-1]["x"] = math.nan
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                validate_samples(samples, time.time())

    def test_yaw_wrap(self):
        samples = capture()["samples"]
        for i, s in enumerate(samples):
            s["yaw"] = math.pi - 0.001 if i % 2 else -math.pi + 0.001
        validate_samples(samples, time.time())

    def test_fresh_latest_in_bounded_sampling_window(self):
        now = time.time()
        samples = capture()["samples"]
        for i, sample in enumerate(samples):
            sample["stamp"] = now - 1.8 + i * 0.1
        validate_samples(samples, now)  # Newest is 1.4 s old; window is 0.4 s.
        samples[0]["stamp"] -= 5
        with self.assertRaises(ValueError):
            validate_samples(samples, now)

    def test_concurrent_writes(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i: GuidePointStore(self.path, capture).add({"name": str(i)}), range(12)))
        self.assertEqual(len(self.store.list()), 12)

    def test_corrupt_store_preserved(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("broken")
        with self.assertRaises(ValueError):
            self.store.add({"name": "入口"})
        self.assertEqual(self.path.read_text(), "broken")

    def test_photo_and_safe_image_path(self):
        image = b"\xff\xd8\xfffake-test-jpeg\xff\xd9"
        point = self.store.add({"name": "展板", "image_jpeg_base64": base64.b64encode(image).decode()})
        self.assertEqual(self.store.image_path(point["id"]).read_bytes(), image)
        with self.assertRaises(ValueError):
            self.store.image_path("../outside")
        with self.assertRaises(ValueError):
            self.store.image_path("0" * 32)

    def test_invalid_photo(self):
        for value in ("xxx", base64.b64encode(b"<script>bad</script>").decode(), "a" * 1400001):
            with self.assertRaises(ValueError):
                decode_board_image(value)

    def test_draft_validation(self):
        raw = {"speech": "欢迎参观。", "needs_review": False, "uncertainties": []}
        self.assertEqual(validate_board_draft(json.dumps(raw)), raw)
        raw["uncertainties"] = ["年份看不清"]
        self.assertTrue(validate_board_draft(json.dumps(raw))["needs_review"])
        for bad in [{"speech": "bad"}, {**raw, "speech": "a" * 151}, {**raw, "needs_review": "false"},
                    {**raw, "action": "stop"}, {"speech": "", "needs_review": False, "uncertainties": []}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_board_draft(json.dumps(bad))


class WebPointTests(unittest.TestCase):
    def setUp(self):
        # Import the real HTTP code while isolating all SDK and task hardware.
        fake_channel = types.ModuleType("unitree_sdk2py.core.channel")
        fake_channel.ChannelFactoryInitialize = Mock()
        fake_api = types.ModuleType("unitree_sdk2py.g1.audio.g1_audio_api")
        fake_api.ROBOT_API_ID_AUDIO_TTS = 1
        fake_audio = types.ModuleType("unitree_sdk2py.g1.audio.g1_audio_client")
        fake_audio.AudioClient = Mock()
        with patch.dict(sys.modules, {fake_channel.__name__: fake_channel, fake_api.__name__: fake_api,
                                      fake_audio.__name__: fake_audio}):
            import mobile_guide_server
        self.web = mobile_guide_server
        self.directory = tempfile.TemporaryDirectory()
        self.speech = Mock()
        self.tasks = Mock()
        self.tasks.active.return_value = False
        with patch.object(self.web, "TaskExecutor", return_value=self.tasks), \
                patch.dict(os.environ, {"GUIDE_OPERATOR_PIN": "246810"}):
            self.server = self.web.MobileGuideServer(("127.0.0.1", 0), self.web.Handler, self.speech, "/missing-dialogue")
        self.server.points = GuidePointStore(Path(self.directory.name) / "guide_points.json", capture)
        self.server.relocation = self.web.RelocalizationManager(self.server.points,
            stop=lambda: {"stopped": True}, project=self.directory.name)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.address = "http://127.0.0.1:" + str(self.server.server_address[1])

    def tearDown(self):
        self.server.boards.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.directory.cleanup()

    def request(self, path, payload=None):
        request = urllib.request.Request(self.address + path,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        try:
            response = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=3)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            data = response.read()
            return response.status, data if response.headers["Content-Type"] == "image/jpeg" else json.loads(data)

    def test_save_list_image_and_no_hardware(self):
        image = b"\xff\xd8\xfffake-jpeg\xff\xd9"
        status, result = self.request("/api/points", {"name": "入口", "pin": "246810", "speech": "欢迎",
            "image_jpeg_base64": base64.b64encode(image).decode(), "board_reviewed": True})
        self.assertEqual(status, 200)
        self.assertEqual(self.request("/api/points")[1]["points"], [result["point"]])
        self.assertEqual(self.request(result["point"]["image"]), (200, image))
        self.assertEqual(self.request("/api/points/../../etc/passwd/image")[0], 404)
        self.speech.enqueue.assert_not_called()
        self.tasks.start.assert_not_called()

    def test_wrong_pin_and_coordinate_injection_rejected(self):
        self.assertEqual(self.request("/api/points", {"name": "x", "pin": "bad"})[0], 400)
        self.assertEqual(self.request("/api/points", {"name": "x", "pin": "246810", "pose": {"x": 0}})[0], 400)
        self.assertEqual(self.server.points.list(), [])

    def test_task_and_unreviewed_photo_rejected(self):
        self.tasks.active.return_value = True
        self.assertEqual(self.request("/api/points", {"name": "x", "pin": "246810"})[0], 503)
        self.tasks.active.return_value = False
        self.assertEqual(self.request("/api/points", {"name": "x", "pin": "246810", "image_jpeg_base64": "x"})[0], 400)
        self.assertEqual(self.server.points.list(), [])

    def test_draft_never_speaks_or_prepares_plan(self):
        draft = {"speech": "欢迎参观。", "needs_review": False, "uncertainties": []}
        image = base64.b64encode(b"\xff\xd8\xfffake\xff\xd9").decode()
        tiles = [{"id": "overview", "label": "整图概览", "image_jpeg_base64": image}]
        def query(payload, timeout):
            data = {"facts": ["欢迎参观。"], "needs_review": False, "uncertainties": []} if payload["mode"] == "board_read" else draft
            return {"status": "success", "text": json.dumps(data), "actions": [], "tool_calls": []}
        self.server.boards.prepare = Mock(return_value=tiles)
        self.server.boards.query = Mock(side_effect=query)
        with patch.object(self.server, "omni_ready", return_value=True), \
                patch.object(self.server, "prepare_plan") as prepare:
            status, result = self.request("/api/points/draft", {
                "image_jpeg_base64": image})
            self.assertEqual(status, 200)
            job_id = result["job"]["id"]
            self.server.boards.thread.join(timeout=2)
            status, result = self.request("/api/points/draft/" + job_id)
            self.assertEqual(status, 200)
            self.assertEqual(result["job"]["draft"], draft)
            self.assertEqual([call.args[0]["mode"] for call in self.server.boards.query.call_args_list], ["board_read", "board_merge"])
            prepare.assert_not_called()
        self.assertEqual(self.server.points.list(), [])
        self.speech.enqueue.assert_not_called()
        self.tasks.start.assert_not_called()

    def test_background_draft_reports_failures_and_missing_job(self):
        image = base64.b64encode(b"\xff\xd8\xfffake\xff\xd9").decode()
        self.server.boards.prepare = Mock(side_effect=ValueError("图片无法解码"))
        with patch.object(self.server, "omni_ready", return_value=True):
            status, result = self.request("/api/points/draft", {"image_jpeg_base64": image})
            self.assertEqual(status, 200)
            self.server.boards.thread.join(timeout=2)
        self.assertEqual(self.request("/api/points/draft/" + result["job"]["id"])[1]["job"]["state"], "failed")
        self.assertEqual(self.request("/api/points/draft/unknown")[0], 404)
        self.assertEqual(self.server.points.list(), [])
        self.speech.enqueue.assert_not_called()

    def test_live_pose_without_start_point_and_no_save(self):
        status, result = self.request("/api/points/pose")
        self.assertEqual(status, 200)
        self.assertAlmostEqual(result["pose"]["x"], 4.9)
        self.assertEqual(result["frame_id"], "map")
        self.assertLess(result["tf_age_seconds"], 1.5)
        self.assertEqual(self.server.points.list(), [])
        self.tasks.start.assert_not_called()
        self.speech.enqueue.assert_not_called()

    def test_website_routes_and_assets_are_read_only(self):
        paths = ("/", "/assistant", "/tasks", "/points", "/points/new", "/localization", "/system",
                 "/assets/guide.css", "/assets/guide.js")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for path in paths:
            with self.subTest(path=path), opener.open(self.address + path, timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                body = response.read().decode("utf-8")
                self.assertTrue(body)
                expected = "text/css" if path.endswith(".css") else "application/javascript" if path.endswith(".js") else "text/html"
                self.assertTrue(response.headers["Content-Type"].startswith(expected))
                if expected == "text/html":
                    self.assertIn('id="stop"', body)
        self.assertEqual(self.request("/missing")[0], 404)
        self.assertEqual(self.server.points.list(), [])
        self.assertEqual(self.server.boards.snapshot()["state"], "idle")
        self.tasks.start.assert_not_called()
        self.speech.enqueue.assert_not_called()

    def test_live_pose_rejects_actual_stale_and_running_task(self):
        self.tasks.active.return_value = True
        self.assertEqual(self.request("/api/points/pose")[0], 503)
        self.tasks.active.return_value = False
        def old():
            packet = capture()
            for sample in packet["samples"]:
                sample["stamp"] -= 10
            return packet
        self.server.points.pose_provider = old
        status, result = self.request("/api/points/pose")
        self.assertEqual(status, 503)
        self.assertIn("过期", result["message"])


class HelperTransportTests(unittest.TestCase):
    def test_result_returns_before_ros_style_cleanup(self):
        real_popen = subprocess.Popen
        children = []
        def helper(command, **kwargs):
            child = real_popen([sys.executable, "-u", "-c",
                "import json,time; print(json.dumps({'status':'success','received':True}),flush=True); time.sleep(1.2)"], **kwargs)
            children.append(child)
            return child
        with patch("guide_points.subprocess.Popen", side_effect=helper):
            started = time.monotonic()
            packet = run_ros_json("capture_guide_pose.py", timeout=3)
            self.assertTrue(packet["received"])
            self.assertLess(time.monotonic() - started, 1.0)
            self.assertIsNone(children[0].poll())
        children[0].wait(timeout=3)

    def test_relocation_stdin_and_error_payload(self):
        real_popen = subprocess.Popen
        def helper(command, **kwargs):
            return real_popen([sys.executable, "-u", "-c",
                "import json,sys; d=json.load(sys.stdin); print(json.dumps({'status':'error','message':d['test']}),flush=True)"], **kwargs)
        with patch("guide_points.subprocess.Popen", side_effect=helper):
            self.assertEqual(run_ros_json("web_relocalize.py", payload={"test": "map mismatch"}, timeout=3),
                             {"status": "error", "message": "map mismatch"})


if __name__ == "__main__":
    unittest.main()
