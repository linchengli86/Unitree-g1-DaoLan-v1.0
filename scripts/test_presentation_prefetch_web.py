#!/usr/bin/env python3
"""Prepared conception metadata stays separate from task actions and motion."""

import json
import unittest
from unittest.mock import Mock, patch

import test_agent_web as agent_tests
import test_guide_points as point_tests


class PresentationPrefetchWebTests(unittest.TestCase):
    request = point_tests.WebPointTests.request
    _points_digest = agent_tests.AgentWebTests._points_digest
    assert_read_only = agent_tests.AgentWebTests.assert_read_only

    def setUp(self):
        agent_tests.AgentWebTests.setUp(self)
        self.ticket = "a" * 32
        self.preparation = {"id": self.ticket, "state": "running", "point_id": self.first["id"],
                            "topic": "结构", "map_fingerprint": "test-map", "context_sha256": "b" * 64}
        self.valid = True
        self.jobs = Mock()
        self.jobs.start.return_value = dict(self.preparation)
        self.jobs.binding.side_effect = self.binding
        self.jobs.cancel.side_effect = self.cancel
        self.jobs.cancel_all.side_effect = self.cancel_all
        self.server.conception_jobs.cancel_all()
        self.server.conception_jobs = self.jobs
        self.tasks.presentation_provider = self.jobs.consume
        self.tasks.start.return_value = {"id": "c" * 32, "state": "running"}
        self.ready_patch = patch.object(self.server, "omni_ready", return_value=True)
        self.ready_patch.start()

    def tearDown(self):
        self.ready_patch.stop()
        agent_tests.AgentWebTests.tearDown(self)

    def binding(self, ticket, point_id, topic):
        if not self.valid or ticket != self.ticket:
            raise ValueError("准备票据已取消或不存在")
        if point_id != self.first["id"] or topic != "结构":
            raise ValueError("准备票据与点位或主题不匹配")
        return dict(self.preparation)

    def cancel(self, ticket):
        if ticket != self.ticket:
            return False
        self.valid = False
        return True

    def cancel_all(self):
        self.valid = False
        return 1

    def plan(self, point_id=None, topic="结构"):
        return {"title": "当前展点动态讲解", "steps": [{"action": "present_point",
            "parameters": {"point_id": point_id or self.first["id"], "topic": topic}, "timeout": 120}]}

    def prepare(self, **extra):
        payload = {"plan": self.plan(), "presentation_ticket": self.ticket}
        payload.update(extra)
        return self.request("/api/plans/prepare", payload)

    def test_prefetch_starts_async_metadata_only_without_motion_speech_or_planning(self):
        with patch.object(self.web, "stop_navigation") as stop:
            status, result = self.request("/api/agent/prefetch", {"point_id": self.first["id"], "topic": "结构"})
            stop.assert_not_called()
        self.assertEqual(status, 200, result)
        self.assertEqual(result["preparation"], self.preparation)
        self.jobs.start.assert_called_once_with(self.first["id"], "结构")
        self.jobs.consume.assert_not_called()
        self.assertEqual(self.server.pending, {})
        self.assert_read_only()

    def test_prefetch_rejects_client_text_commands_and_unknown_fields(self):
        for key, value in (("text", "用户注入台词"), ("segments", ["任意台词"]),
                           ("plan", self.plan()), ("motion", True), ("pin", "246810")):
            with self.subTest(key=key):
                self.assertEqual(self.request("/api/agent/prefetch", {
                    "point_id": self.first["id"], key: value})[0], 400)
        self.jobs.start.assert_not_called()
        self.assert_read_only()

    def test_prefetch_rejects_unconfigured_or_busy_server(self):
        with patch.object(self.server, "omni_ready", return_value=False):
            self.assertEqual(self.request("/api/agent/prefetch", {"point_id": self.first["id"]})[0], 503)
        for manager in (self.tasks, self.server.relocation, self.server.initialization):
            with self.subTest(manager=type(manager).__name__), patch.object(manager, "active", return_value=True):
                self.assertEqual(self.request("/api/agent/prefetch", {"point_id": self.first["id"]})[0], 503)
        self.jobs.start.assert_not_called()
        self.assert_read_only()

    def test_ticket_binds_only_internal_metadata_not_action_parameters(self):
        status, result = self.prepare(plan=self.plan(topic="  结构  "))
        self.assertEqual(status, 200, result)
        token = result["confirmation"]
        self.assertEqual(self.server.pending_presentations, {token: self.ticket})
        self.assertNotIn(self.ticket, json.dumps(result["plan"]))
        self.assertEqual(result["plan"]["steps"][0]["parameters"], {"point_id": self.first["id"], "topic": "结构"})
        self.jobs.binding.assert_called_once_with(self.ticket, self.first["id"], "结构")
        self.jobs.consume.assert_not_called()
        self.assert_read_only()

    def test_wrong_point_topic_unknown_ticket_and_injected_action_parameter_rejected(self):
        payloads = [{"plan": self.plan(point_id=self.second["id"])}, {"plan": self.plan(topic="其他主题")},
                    {"presentation_ticket": "d" * 32}, {"presentation_ticket": None}]
        injected = self.plan()
        injected["steps"][0]["parameters"]["presentation_ticket"] = self.ticket
        payloads.append({"plan": injected})
        for payload in payloads:
            with self.subTest(payload=payload):
                self.assertEqual(self.prepare(**payload)[0], 400)
        self.assertEqual(self.server.pending, {})
        self.jobs.consume.assert_not_called()
        self.assert_read_only()

    def test_ticket_cannot_attach_to_other_actions_or_multi_step_plan(self):
        plans = [
            {"title": "wait", "steps": [{"action": "wait", "parameters": {"seconds": 1}}]},
            {"title": "speak", "steps": [{"action": "speak", "parameters": {"text": "任意台词"}}]},
            {"title": "nav", "steps": [{"action": "navigate_to_point", "parameters": {"point_id": self.first["id"]}}]},
            {"title": "two", "steps": self.plan()["steps"] * 2},
        ]
        for plan in plans:
            with self.subTest(plan=plan):
                self.assertEqual(self.prepare(plan=plan)[0], 400)
        self.jobs.binding.assert_not_called()
        self.jobs.consume.assert_not_called()
        self.assert_read_only()

    def test_prepare_payload_rejects_unknown_fields(self):
        self.assertEqual(self.prepare(segments=["客户端台词"])[0], 400)
        self.jobs.binding.assert_not_called()
        self.assert_read_only()

    def test_confirm_passes_ticket_without_consuming_in_http_handler(self):
        status, prepared = self.prepare()
        self.assertEqual(status, 200)
        status, result = self.request("/api/plans/confirm", {"token": prepared["confirmation"]})
        self.assertEqual(status, 200, result)
        self.tasks.start.assert_called_once_with(prepared["plan"], dry_run=False, presentation_ticket=self.ticket)
        self.jobs.consume.assert_not_called()
        self.assertEqual(self.server.pending_presentations, {})
        self.speech.enqueue.assert_not_called()

    def test_dry_run_does_not_consume_or_start_conception(self):
        _, prepared = self.prepare()
        status, result = self.request("/api/plans/dry-run", {"token": prepared["confirmation"]})
        self.assertEqual(status, 200, result)
        self.tasks.start.assert_called_once_with(prepared["plan"], dry_run=True, presentation_ticket=self.ticket)
        self.jobs.start.assert_not_called()
        self.jobs.consume.assert_not_called()
        self.speech.enqueue.assert_not_called()

    def test_legacy_plan_start_has_unchanged_signature(self):
        status, prepared = self.request("/api/plans/prepare", {"plan": self.plan()})
        self.assertEqual(status, 200)
        self.assertEqual(self.server.pending_presentations, {})
        self.assertEqual(self.request("/api/plans/confirm", {"token": prepared["confirmation"]})[0], 200)
        self.tasks.start.assert_called_once_with(prepared["plan"], dry_run=False)
        self.jobs.binding.assert_not_called()

    def test_cancel_invalidates_bound_ticket_before_confirmation(self):
        _, prepared = self.prepare()
        status, result = self.request("/api/agent/prefetch/cancel", {"id": self.ticket})
        self.assertEqual(status, 200, result)
        self.assertTrue(result["cancelled"])
        self.assertEqual(self.request("/api/plans/confirm", {"token": prepared["confirmation"]})[0], 400)
        self.tasks.start.assert_not_called()
        self.jobs.consume.assert_not_called()

    def test_cancel_accepts_only_valid_ticket_identifier(self):
        for payload in ({}, {"id": None}, {"id": "../etc/passwd"}, {"id": "A" * 32},
                        {"id": self.ticket, "point_id": self.first["id"]}, {"id": self.ticket, "text": "x"}):
            with self.subTest(payload=payload):
                self.assertEqual(self.request("/api/agent/prefetch/cancel", payload)[0], 400)
        self.jobs.cancel.assert_not_called()
        self.assert_read_only()

    def test_stop_clears_pending_and_invalidates_all_preparations(self):
        _, prepared = self.prepare()
        with patch.object(self.web, "stop_navigation", return_value={"stopped": True, "message": "mock stop"}):
            self.assertEqual(self.request("/api/stop", {})[0], 200)
        self.jobs.cancel_all.assert_called_once_with()
        self.assertEqual(self.server.pending, {})
        self.assertEqual(self.server.pending_presentations, {})
        self.assertEqual(self.request("/api/plans/confirm", {"token": prepared["confirmation"]})[0], 400)
        self.tasks.start.assert_not_called()

    def test_init_and_registered_relocation_invalidate_all_preparations(self):
        actions = [
            ("/api/init/start", {"robot_ready": True, "pin": "246810"}, self.server.initialization, "start"),
            ("/api/relocation/start", {"point_id": self.first["id"], "confirmed_position": True, "pin": "246810"},
             self.server.relocation, "start"),
        ]
        for path, payload, manager, method in actions:
            self.valid = True
            self.jobs.cancel_all.reset_mock()
            self.prepare()
            with self.subTest(path=path), patch.object(manager, method, return_value={"state": "running"}):
                self.assertEqual(self.request(path, payload)[0], 200)
            self.jobs.cancel_all.assert_called_once_with()
            self.assertEqual(self.server.pending_presentations, {})
            self.assertFalse(self.valid)
        self.tasks.start.assert_not_called()

    def test_manual_relocation_invalidates_preparations(self):
        self.prepare()
        payload = {"x": 1.0, "y": 2.0, "yaw": 0.2, "map_fingerprint": "mock-map",
                   "confirmed_position": True, "pin": "246810"}
        with patch.object(self.server.initialization, "snapshot", return_value={"state": "succeeded"}), \
                patch.object(self.server.map, "validate_manual_seed", return_value={"pose": {}}), \
                patch.object(self.server.relocation, "start_seed", return_value={"state": "running"}):
            self.assertEqual(self.request("/api/relocation/manual", payload)[0], 200)
        self.jobs.cancel_all.assert_called_once_with()
        self.assertEqual(self.server.pending_presentations, {})
        self.tasks.start.assert_not_called()

    def test_server_shutdown_invalidates_all_preparations(self):
        self.server.shutdown()
        self.jobs.cancel_all.assert_called_once_with()
        self.assertFalse(self.valid)
        self.tasks.start.assert_not_called()

    def test_tts_timing_is_measured_without_unsupported_speed_parameters(self):
        worker = self.web.UnitreeSpeechWorker("fake-interface", 100)
        client = Mock()
        client.SetVolume.return_value = 0
        client._Call.return_value = (0, None)
        request = worker.enqueue("测试")
        worker.items.put_nowait(None)
        with patch.object(self.web, "ChannelFactoryInitialize") as initialize, \
                patch.object(self.web, "AudioClient", return_value=client), \
                patch.object(worker.shutdown_event, "wait", return_value=False) as playback_wait:
            worker.run()
        initialize.assert_called_once_with(0, "fake-interface")
        self.assertTrue(request["done"].is_set())
        self.assertIsNone(request["error"])
        result = request["result"]
        self.assertGreaterEqual(result["queue_wait_seconds"], 0)
        self.assertGreaterEqual(result["sdk_call_seconds"], 0)
        self.assertGreaterEqual(result["tts_requested_at"], request["queued_at"])
        self.assertGreaterEqual(result["sdk_accepted_at"], result["tts_requested_at"])
        self.assertEqual(result["completion"], "estimated_playback_wait_elapsed")
        self.assertAlmostEqual(result["wait_seconds"], 2 * 0.30 + 1.5)
        playback_wait.assert_called_once_with(result["wait_seconds"])
        parameters = json.loads(client._Call.call_args.args[1])
        self.assertEqual(set(parameters), {"index", "text", "speaker_id"})
        self.assertEqual(parameters["text"], "测试")
        self.assertEqual(parameters["speaker_id"], 0)
        client.SetVolume.assert_called_once_with(100)


if __name__ == "__main__":
    unittest.main()
