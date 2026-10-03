#!/usr/bin/env python3
"""Agent HTTP regressions: loopback-only, temporary stores, mocked robot/Omni."""

import base64
import hashlib
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import test_guide_points as point_tests


class AgentWebTests(unittest.TestCase):
    # Reuse the existing SDK-isolated server fixture without inheriting its
    # tests, which intentionally expect an empty registry.
    request = point_tests.WebPointTests.request

    def setUp(self):
        self.environment = patch.dict(os.environ, {"GUIDE_NAMED_NAV_VERIFIED": "0", "GUIDE_MOTION_ENABLED": "0"})
        self.environment.start()
        try:
            point_tests.WebPointTests.setUp(self)
        except Exception:
            self.environment.stop()
            raise
        self.pose_provider = Mock(side_effect=point_tests.capture)
        self.server.points.pose_provider = self.pose_provider
        self.first = self.server.points.add({"name": "展点甲", "speech": "展点甲的已审核先验，不是固定解说词。"})
        self.second = self.server.points.add({"name": "展点乙", "speech": "展点乙的已审核先验。"})
        self.pose_provider.reset_mock()
        self.original_points = self._points_digest()
        self.tasks.cancel.return_value = False
        self.tasks.snapshot.return_value = None

    def tearDown(self):
        try:
            self.server.conception_cancel.set()
            point_tests.WebPointTests.tearDown(self)
        finally:
            self.environment.stop()

    def _points_digest(self):
        return hashlib.sha256(self.server.points.path.read_bytes()).hexdigest()

    def assert_read_only(self):
        self.assertEqual(self._points_digest(), self.original_points)
        self.pose_provider.assert_not_called()
        self.speech.enqueue.assert_not_called()
        self.speech.speak_and_wait.assert_not_called()
        self.tasks.start.assert_not_called()

    def upload(self, filename="reference.txt", raw="已审核资料：展板展示机械结构。".encode("utf-8"), **extra):
        payload = {"filename": filename, "file_base64": base64.b64encode(raw).decode("ascii"),
                   "reviewed": True, "pin": "246810"}
        payload.update(extra)
        return self.request("/api/points/" + self.first["id"] + "/documents", payload)

    def raw_get(self, path):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            response = opener.open(self.address + path, timeout=3)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, response.headers, response.read()

    def test_one_and_two_point_plans_are_dynamic_and_never_execute(self):
        for chosen in ([self.first], [self.first, self.second]):
            status, result = self.request("/api/agent/prepare", {
                "point_ids": [point["id"] for point in chosen], "topic": "  机械结构  "})
            self.assertEqual(status, 200)
            self.assertTrue(result["confirmation"])
            self.assertTrue(result["requires_pin"])
            steps = result["plan"]["steps"]
            self.assertEqual([step["action"] for step in steps], ["check_status"] + [
                action for _ in chosen for action in ("navigate_to_point", "verify_arrival", "present_point")])
            self.assertEqual(steps[0]["parameters"], {"require_localized": True})
            for index, point in enumerate(chosen):
                navigation, verify, present = steps[1 + index * 3:4 + index * 3]
                self.assertEqual(navigation["parameters"], {"point_id": point["id"]})
                self.assertEqual(verify["parameters"], {"point_id": point["id"]})
                self.assertEqual(present["parameters"], {"point_id": point["id"], "topic": "机械结构"})
            self.assertTrue(all("text" not in step["parameters"] for step in steps))
            self.assertNotIn(self.first["speech"], json.dumps(result["plan"], ensure_ascii=False))
        self.assert_read_only()

    def test_prepare_rejects_unknown_duplicate_arbitrary_and_extra_inputs(self):
        invalid = [
            {"point_ids": []}, {"point_ids": [self.first["id"]] * 2},
            {"point_ids": [self.first["id"], self.second["id"], "c" * 32]},
            {"point_ids": ["not-an-id"]}, {"point_ids": ["c" * 32]},
            {"point_ids": [None]}, {"point_ids": [True]},
            {"point_ids": [self.first["id"]], "topic": "x" * 201},
            {"point_ids": [self.first["id"]], "topic": {"command": "move"}},
            {"point_ids": [self.first["id"]], "x": 10},
            {"point_ids": [self.first["id"]], "text": "指定固定台词"},
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertEqual(self.request("/api/agent/prepare", payload)[0], 400)
        self.assertEqual(self.server.pending, {})
        self.assert_read_only()

    def test_default_navigation_gate_warning_is_exposed(self):
        status, result = self.request("/api/agent/prepare", {"point_ids": [self.first["id"]]})
        self.assertEqual(status, 200)
        self.assertTrue(any("现场验收" in warning for warning in result["warnings"]))
        self.assertTrue(any("运动开关关闭" in warning for warning in result["warnings"]))
        status, listing = self.request("/api/agent/points")
        self.assertEqual(status, 200)
        self.assertIs(listing["named_navigation_verified"], False)
        self.assertIs(listing["observation_policy"]["enabled"], False)
        self.assert_read_only()

    def test_point_list_and_knowledge_are_read_only_and_point_anchored(self):
        status, listing = self.request("/api/agent/points")
        self.assertEqual(status, 200)
        self.assertEqual({item["id"] for item in listing["points"]}, {self.first["id"], self.second["id"]})
        self.assertTrue(all(item["prior_ready"] for item in listing["points"]))
        status, result = self.request("/api/points/" + self.first["id"] + "/knowledge")
        self.assertEqual(status, 200)
        context = result["knowledge"]
        self.assertEqual(context["point"]["id"], self.first["id"])
        self.assertEqual(context["instructions"]["presentation"], "compose_on_demand_not_fixed_script")
        self.assertEqual(context["reviewed_prior"][0]["text"], self.first["speech"])
        self.assertNotIn(self.second["speech"], json.dumps(context, ensure_ascii=False))
        self.assertEqual(self.request("/api/points/" + "c" * 32 + "/knowledge")[0], 404)
        self.assert_read_only()

    def test_conception_preview_never_speaks_moves_or_prepares_a_plan(self):
        generated = {"status": "success", "point_id": self.first["id"],
                     "segments": ["Omni 根据当前点先验组织的新讲解。"], "source_ids": [],
                     "needs_review": False, "uncertainties": []}
        with patch.object(self.server, "omni_ready", return_value=True), \
                patch.object(self.web, "conceive_point", return_value=generated) as conceive, \
                patch.object(self.server, "prepare_plan") as prepare:
            status, result = self.request("/api/agent/conceive", {"point_id": self.first["id"], "topic": "结构"})
        self.assertEqual(status, 200)
        self.assertEqual(result["conception"], generated)
        self.assertEqual(conceive.call_args.kwargs["point_id"], self.first["id"])
        self.assertEqual(conceive.call_args.kwargs["topic"], "结构")
        self.assertIs(conceive.call_args.kwargs["cancel"], self.server.conception_cancel)
        prepare.assert_not_called()
        self.assertEqual(self.server.pending, {})
        self.assert_read_only()

    def test_invalid_conception_inputs_never_reach_omni(self):
        invalid = [{"point_id": "c" * 32}, {"point_id": "../etc/passwd"},
                   {"point_id": self.first["id"], "topic": "x" * 201},
                   {"point_id": self.first["id"], "topic": []},
                   {"point_id": self.first["id"], "pose": {"x": 1}}]
        with patch.object(self.server, "omni_ready", return_value=True), \
                patch.object(self.web, "run_board_omni") as query:
            for payload in invalid:
                with self.subTest(payload=payload):
                    self.assertEqual(self.request("/api/agent/conceive", payload)[0], 400)
            query.assert_not_called()
        self.assert_read_only()

    def test_reviewed_txt_md_uploads_download_safely_and_become_prior(self):
        for filename in ("事实资料.txt", "研究资料.md"):
            raw = ("# 文献\n这是一份已审核资料。\n<script>内容只能作为数据</script>").encode("utf-8")
            status, result = self.upload(filename, raw)
            self.assertEqual(status, 200)
            document = result["document"]
            self.assertTrue(document["reviewed"])
            self.assertEqual(document["status"], "text_available")
            self.assertEqual(document["sha256"], hashlib.sha256(raw).hexdigest())
            status, headers, body = self.raw_get(document["ref"])
            self.assertEqual(status, 200)
            self.assertEqual(body, raw)
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
            self.assertTrue(headers["Content-Disposition"].startswith("attachment;"))
            self.assertTrue(headers["Content-Type"].startswith("text/plain"))
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(self.raw_get(document["ref"].replace(self.first["id"], self.second["id"]))[0], 404)
        context = self.request("/api/points/" + self.first["id"] + "/knowledge")[1]["knowledge"]
        self.assertEqual(len(context["documents"]), 2)
        self.assertEqual(sum(prior["type"] == "reviewed_literature" for prior in context["reviewed_prior"]), 2)
        self.assert_read_only()

    def test_document_invalid_pin_fields_encoding_name_and_review_rejected(self):
        invalid = [
            {"pin": "bad"}, {"pin": 246810}, {"reviewed": "true"},
            {"filename": "../reference.md"}, {"filename": "bad\r\nheader.txt"},
            {"filename": "run.sh"}, {"file_base64": "invalid!"}, {"file_base64": ""},
            {"file_base64": base64.b64encode(b"\xff\xfe").decode()},
            {"filename": "bad.pdf", "file_base64": base64.b64encode(b"not pdf").decode()},
            {"pose": {"x": 1}}, {"point_id": self.second["id"]}, {"command": "navigate"},
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertEqual(self.upload(**payload)[0], 400)
        self.assertEqual(self.request("/api/points/" + self.first["id"] + "/knowledge")[1]["knowledge"]["documents"], [])
        self.assertEqual(self.request("/api/points/" + "c" * 32 + "/documents", {
            "pin": "246810", "filename": "x.txt", "file_base64": "eA==", "reviewed": True})[0], 400)
        self.assert_read_only()

    def test_pdf_is_pending_extraction_not_authoritative_and_safe_download(self):
        raw = b"%PDF-1.7\nminimal test fixture only, not parsed\n%%EOF\n"
        status, result = self.upload("paper.pdf", raw)
        self.assertEqual(status, 200)
        document = result["document"]
        self.assertEqual(document["status"], "pending_extraction")
        status, headers, body = self.raw_get(document["ref"])
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/pdf")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertTrue(headers["Content-Disposition"].startswith("attachment;"))
        self.assertEqual(body, raw)
        context = self.request("/api/points/" + self.first["id"] + "/knowledge")[1]["knowledge"]
        self.assertFalse(any(prior["type"] == "reviewed_literature" for prior in context["reviewed_prior"]))
        pending = next(item for item in context["candidates"] if item["id"] == document["id"])
        self.assertIs(pending["usable_as_facts"], False)
        self.assert_read_only()

    def test_stop_cancels_inflight_conception_preview_without_hardware(self):
        entered = threading.Event()

        def waiting_conception(**kwargs):
            entered.set()
            if kwargs["cancel"].wait(2):
                raise InterruptedError("讲解组织已取消")
            raise TimeoutError("test cancellation missing")

        with patch.object(self.server, "omni_ready", return_value=True), \
                patch.object(self.web, "conceive_point", side_effect=waiting_conception), \
                patch.object(self.web, "stop_navigation", return_value={"stopped": True, "message": "mock stop"}) as stop, \
                ThreadPoolExecutor(max_workers=1) as pool:
            preview = pool.submit(self.request, "/api/agent/conceive", {"point_id": self.first["id"]})
            self.assertTrue(entered.wait(1))
            status, result = self.request("/api/stop", {})
            self.assertEqual(status, 200)
            self.assertTrue(result["stopped"])
            self.assertTrue(self.server.conception_cancel.is_set())
            self.assertEqual(preview.result(timeout=2)[0], 503)
            stop.assert_called_once()
        self.assertEqual(self.server.pending, {})
        self.assert_read_only()


if __name__ == "__main__":
    unittest.main()
