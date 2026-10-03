#!/usr/bin/env python3
"""Bounded tool-loop regressions. No network, SDK, ROS or motion calls."""

import base64
import json
import os
import sys
import types
import unittest
from unittest.mock import patch

try:
    import websocket
except ImportError:
    # Tests do not need the production network dependency.
    websocket = types.ModuleType("websocket")
    websocket.create_connection = None
    sys.modules["websocket"] = websocket

import omni_client
from guide_actions import validate_plan
from guide_skills import execute, omni_tools


GOOD_PLAN = {"title": "往返导览", "steps": [
    {"action": "speak", "parameters": {"text": "欢迎参加导览。"}},
    {"action": "navigate_route", "parameters": {"destination": "end"}},
    {"action": "speak", "parameters": {"text": "我们已经到达终点。"}},
    {"action": "navigate_route", "parameters": {"destination": "start"}},
    {"action": "speak", "parameters": {"text": "谢谢参与。"}},
]}


def tool(name, arguments):
    return {"type": "response.function_call_arguments.done", "name": name,
        "arguments": json.dumps(arguments, ensure_ascii=False), "call_id": "test-call"}


class FakeWebSocket:
    def __init__(self, rounds):
        self.events = [{"type": "session.created"}, {"type": "session.updated"}]
        for calls in rounds:
            self.events.extend(calls)
            self.events.append({"type": "response.done", "response": {"status": "completed"}})
        self.sent = []
        self.closed = False

    def recv(self):
        if not self.events:
            raise AssertionError("An unnecessary model turn was requested")
        return json.dumps(self.events.pop(0))

    def send(self, value):
        self.sent.append(json.loads(value))

    def settimeout(self, value):
        pass

    def close(self):
        self.closed = True


class PlanningTests(unittest.TestCase):
    def run_query(self, socket, executor=None):
        def validate(name, args):
            if name == "request_action_plan":
                return {"confirmation_required": True, "plan": validate_plan(args)}
            return {"read_only": True}
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test-placeholder"}), \
                patch.object(websocket, "create_connection", return_value=socket), \
                patch.object(omni_client, "execute", side_effect=executor or validate):
            return omni_client.query({"text": "请规划往返导览"})

    def test_valid_plan_needs_no_summary_turn(self):
        socket = FakeWebSocket([[tool("request_action_plan", GOOD_PLAN)]])
        result = self.run_query(socket)
        self.assertEqual(len(result["actions"][0]["plan"]["steps"]), 5)
        self.assertIn("尚未执行", result["text"])
        self.assertTrue(socket.closed)
        self.assertEqual(sum(event["type"] == "response.create" for event in socket.sent), 1)

    def test_valid_plan_on_last_round_is_not_lost(self):
        rounds = [[tool("request_action_plan", {"title": "缺少步骤"})]
            for _ in range(omni_client.MAX_TOOL_ROUNDS - 1)]
        socket = FakeWebSocket(rounds + [[tool("request_action_plan", GOOD_PLAN)]])
        result = self.run_query(socket)
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["tool_calls"]), omni_client.MAX_TOOL_ROUNDS)
        self.assertTrue(all(call["result"].get("error") for call in result["tool_calls"][:-1]))

    def test_round_limit_preserves_diagnostics_and_never_succeeds(self):
        socket = FakeWebSocket([[tool("request_action_plan", {"title": "缺少步骤"})]
            for _ in range(omni_client.MAX_TOOL_ROUNDS)])
        with self.assertRaises(RuntimeError) as raised:
            self.run_query(socket)
        self.assertIn("最后校验错误", str(raised.exception))
        self.assertEqual(len(raised.exception.tool_calls), omni_client.MAX_TOOL_ROUNDS)
        self.assertTrue(socket.closed)
        self.assertEqual(sum(event["type"] == "response.create" for event in socket.sent), omni_client.MAX_TOOL_ROUNDS)

    def test_ordinary_question_keeps_text(self):
        socket = FakeWebSocket([[
            {"type": "response.text.delta", "delta": "你好，我是导览助手。"} ]])
        result = self.run_query(socket)
        self.assertEqual(result["text"], "你好，我是导览助手。")
        self.assertFalse(result["actions"])

    def test_invalid_shell_action_cannot_become_preview(self):
        bad = {"title": "非法", "steps": [{"action": "shell", "parameters": {"command": "whoami"}}]}
        socket = FakeWebSocket([[tool("request_action_plan", bad)], []])
        with self.assertRaises(RuntimeError) as raised:
            self.run_query(socket)
        self.assertIn("参数校验", str(raised.exception))
        self.assertIn("白名单", raised.exception.tool_calls[0]["result"]["error"])


class WireContractTests(unittest.TestCase):
    def test_serialized_plan_normalizes_to_same_contract(self):
        result = execute("request_action_plan", {"title": GOOD_PLAN["title"],
            "steps_json": json.dumps(GOOD_PLAN["steps"], ensure_ascii=False)})
        self.assertEqual(result["plan"], validate_plan(GOOD_PLAN))
        self.assertTrue(result["confirmation_required"])

    def test_old_direct_contract_is_compatible(self):
        self.assertEqual(execute("request_action_plan", GOOD_PLAN)["plan"], validate_plan(GOOD_PLAN))

    def test_wire_rejects_invalid_or_oversized_json(self):
        for serialized in ([], "", "x", "null", "{}", "x" * 20001):
            with self.subTest(serialized=str(serialized)[:20]), self.assertRaises(ValueError):
                execute("request_action_plan", {"title": "坏计划", "steps_json": serialized})

    def test_wire_cannot_bypass_action_whitelist(self):
        serialized = json.dumps([{"action": "shell", "parameters": {"command": "whoami"}}])
        with self.assertRaises(ValueError):
            execute("request_action_plan", {"title": "非法", "steps_json": serialized})

    def test_unknown_wire_fields_rejected(self):
        with self.assertRaises(ValueError):
            execute("request_action_plan", {"title": "非法", "steps_json": "[]", "code": "x"})

    def test_advertised_schema_requires_both_strings(self):
        definition = next(tool["function"] for tool in omni_tools() if tool["function"]["name"] == "request_action_plan")
        self.assertEqual(definition["parameters"]["required"], ["title", "steps_json"])
        self.assertEqual(definition["parameters"]["properties"]["steps_json"]["type"], "string")


class BoardDraftTests(unittest.TestCase):
    def run_board(self, socket):
        socket.events.insert(2, {"type": "input_audio_buffer.committed"})
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test-placeholder"}), \
                patch.object(websocket, "create_connection", return_value=socket), \
                patch.object(omni_client, "execute") as execute_mock:
            try:
                return omni_client.query({"mode": "board_draft", "text": "整理展板",
                    "image_jpeg_base64": base64.b64encode(b"\xff\xd8\xfftest\xff\xd9").decode()})
            finally:
                execute_mock.assert_not_called()

    def test_board_has_no_tools(self):
        socket = FakeWebSocket([[{"type": "response.text.delta", "delta": '{"speech":"欢迎","needs_review":false,"uncertainties":[]}'}]])
        result = self.run_board(socket)
        self.assertEqual(result["actions"], [])
        self.assertEqual(result["tool_calls"], [])
        session = next(e["session"] for e in socket.sent if e["type"] == "session.update")
        self.assertEqual(session["tools"], [])
        self.assertIn("不控制机器人", session["instructions"])

    def test_injected_tool_never_executes(self):
        socket = FakeWebSocket([[tool("stop_navigation", {})]])
        with self.assertRaisesRegex(RuntimeError, "禁止调用"):
            self.run_board(socket)
        self.assertTrue(socket.closed)


class BoardPipelineModeTests(unittest.TestCase):
    IMAGE = base64.b64encode(b"\xff\xd8\xfftest\xff\xd9").decode("ascii")

    def run_mode(self, mode, socket, text="识别资料"):
        request = {"mode": mode, "text": text}
        if mode == "board_read":
            request["image_jpeg_base64"] = self.IMAGE
            socket.events.insert(2, {"type": "input_audio_buffer.committed"})
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test-placeholder"}), \
                patch.object(websocket, "create_connection", return_value=socket), \
                patch.object(omni_client, "execute") as execute_mock:
            try:
                return omni_client.query(request)
            finally:
                execute_mock.assert_not_called()
                session = next(event["session"] for event in socket.sent
                               if event["type"] == "session.update")
                self.assertEqual(session["tools"], [])
                self.assertIn("不控制机器人", session["instructions"])

    def assert_preflight_rejects(self, request):
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test-placeholder"}), \
                patch.object(websocket, "create_connection") as connect, \
                patch.object(omni_client, "execute") as execute_mock:
            with self.assertRaises(ValueError):
                omni_client.query(request)
            connect.assert_not_called()
            execute_mock.assert_not_called()

    def test_board_read_uses_no_tools_and_image_protocol(self):
        text = '{"facts":["展板事实"],"needs_review":false,"uncertainties":[]}'
        socket = FakeWebSocket([[{"type": "response.text.delta", "delta": text}]])
        result = self.run_mode("board_read", socket)
        self.assertEqual(result["text"], text)
        self.assertEqual(result["actions"], [])
        self.assertEqual(result["tool_calls"], [])
        session = next(event["session"] for event in socket.sent if event["type"] == "session.update")
        self.assertEqual(session["instructions"], omni_client.BOARD_READ_INSTRUCTIONS)
        kinds = [event["type"] for event in socket.sent]
        self.assertLess(kinds.index("input_audio_buffer.append"), kinds.index("input_image_buffer.append"))
        self.assertTrue(socket.closed)

    def test_board_merge_accepts_long_text_without_image_or_audio_protocol(self):
        for length in (501, 32000):
            with self.subTest(length=length):
                socket = FakeWebSocket([[{"type": "response.text.delta", "delta": "{}"}]])
                text = "资" * length
                result = self.run_mode("board_merge", socket, text=text)
                self.assertEqual(result["actions"], [])
                self.assertEqual(result["tool_calls"], [])
                item = next(event["item"] for event in socket.sent if event["type"] == "conversation.item.create")
                self.assertEqual(item["content"], [{"type": "input_text", "text": text}])
                session = next(event["session"] for event in socket.sent if event["type"] == "session.update")
                self.assertEqual(session["instructions"], omni_client.BOARD_MERGE_INSTRUCTIONS)
                self.assertFalse(any(event["type"].startswith(("input_image_buffer.", "input_audio_buffer."))
                                     for event in socket.sent))
                self.assertTrue(socket.closed)

    def test_new_board_modes_reject_injected_tools_before_execution(self):
        for mode in ("board_read", "board_merge", "point_conception"):
            with self.subTest(mode=mode):
                socket = FakeWebSocket([[tool("stop_navigation", {})]])
                with self.assertRaisesRegex(RuntimeError, "禁止调用"):
                    self.run_mode(mode, socket)
                self.assertTrue(socket.closed)
                self.assertFalse(any(event.get("item", {}).get("type") == "function_call_output"
                                     for event in socket.sent))

    def test_conception_accepts_point_material_without_motion_tools(self):
        socket = FakeWebSocket([[{"type": "response.text.delta", "delta": "{}"}]])
        result = self.run_mode("point_conception", socket, text="资" * 60000)
        self.assertEqual(result["actions"], [])
        session = next(event["session"] for event in socket.sent if event["type"] == "session.update")
        self.assertEqual(session["instructions"], omni_client.CONCEPTION_INSTRUCTIONS)
        self.assertTrue(socket.closed)

    def test_conception_rejects_audio_empty_and_oversized_material(self):
        for fields in ({"audio_pcm16_base64": base64.b64encode(bytes(3200)).decode("ascii")},
                       {"text": ""}, {"text": " "}, {"text": "资" * 60001}):
            self.assert_preflight_rejects({"mode": "point_conception", "text": "点位资料", **fields})

    def test_merge_rejects_image_audio_empty_and_oversized_text_before_connect(self):
        for fields in ({"image_jpeg_base64": self.IMAGE},
                       {"audio_pcm16_base64": base64.b64encode(bytes(3200)).decode("ascii")},
                       {"text": ""}, {"text": " " * 10}, {"text": "资" * 32001}):
            with self.subTest(fields=list(fields)):
                self.assert_preflight_rejects({"mode": "board_merge", "text": "识别资料", **fields})

    def test_board_read_requires_photo_and_rejects_recording_before_connect(self):
        self.assert_preflight_rejects({"mode": "board_read", "text": "识别展板"})
        self.assert_preflight_rejects({"mode": "board_read", "text": "识别展板",
            "image_jpeg_base64": self.IMAGE,
            "audio_pcm16_base64": base64.b64encode(bytes(3200)).decode("ascii")})

    def test_raw_image_limit_applies_even_to_legacy_board_draft_before_connect(self):
        raw = b"\xff\xd8\xff" + bytes(190000 - 2)
        self.assertEqual(len(raw), 190001)
        for mode in ("board_read", "board_draft", "assistant"):
            with self.subTest(mode=mode):
                self.assert_preflight_rejects({"mode": mode, "text": "识别展板",
                    "image_jpeg_base64": base64.b64encode(raw).decode("ascii")})


if __name__ == "__main__":
    unittest.main(verbosity=2)
