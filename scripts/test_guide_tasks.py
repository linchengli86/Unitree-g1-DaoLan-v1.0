#!/usr/bin/env python3
"""Hardware-free regression tests: python3 scripts/test_guide_tasks.py."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from guide_actions import action_catalog, check_execution_policy, validate_plan
from guide_task_executor import TaskExecutor


POINT_ID = "1" * 32


def plan(*steps):
    return {"title": "回归测试", "steps": list(steps)}


def step(action, **parameters):
    return {"action": action, "parameters": parameters}


class FakeSpeech:
    def __init__(self):
        self.calls = []

    def speak_and_wait(self, text, timeout, cancel):
        self.calls.append(text)
        return {"sdk_accepted": True}


class ContractTests(unittest.TestCase):
    def test_normalization(self):
        normalized = validate_plan(plan(step("speak", text="  欢迎  "), step("wait", seconds=2)))
        self.assertEqual(normalized["version"], 1)
        self.assertEqual(normalized["steps"][0]["parameters"]["text"], "欢迎")
        self.assertEqual(normalized["steps"][1]["timeout"], 35)
        self.assertEqual(validate_plan(normalized), normalized)

    def test_reject_unknown_and_unbounded(self):
        invalid = [plan(step("shell", command="whoami")),
            plan(step("wait", seconds=float("nan"))), plan(step("wait", seconds=True)),
            plan(step("navigate_route", destination="arbitrary")),
            plan(step("arm_gesture", gesture="sit")),
            plan(step("speak", text="x", command="oops")),
            {**plan(step("stop")), "version": True},
            plan(*[step("navigate_route", destination="end") for _ in range(4)])]
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                validate_plan(raw)

    def test_motion_and_arm_policies(self):
        arm = validate_plan(plan(step("arm_gesture", gesture="wave")))
        for motion, enabled, verified in [(False, True, {"wave"}), (True, False, {"wave"}), (True, True, set())]:
            with self.assertRaises(ValueError):
                check_execution_policy(arm, motion, enabled, verified)
        check_execution_policy(arm, True, True, {"wave"})
        with patch.dict(os.environ, {"GUIDE_VERIFIED_ARM_ACTIONS": " wave,clap "}):
            self.assertTrue(action_catalog()["arm_gestures"]["wave"]["verified"])
        self.assertEqual(action_catalog()["actions"]["speak"]["parameters"]["required"], ["text"])

    def test_point_contract_and_catalog(self):
        normalized = validate_plan(plan(step("navigate_to_point", point_id=POINT_ID),
            step("verify_arrival", point_id=POINT_ID), step("present_point", point_id=POINT_ID)))
        self.assertEqual([s["timeout"] for s in normalized["steps"]], [240, 20, 240])
        self.assertEqual(normalized["steps"][2]["parameters"]["topic"], "")
        catalog = action_catalog()["actions"]
        self.assertEqual(catalog["navigate_to_point"]["parameters"]["required"], ["point_id"])
        self.assertEqual(set(catalog["present_point"]["parameters"]["properties"]), {"point_id", "topic"})
        self.assertFalse(catalog["present_point"]["motion"])

    def test_point_contract_rejects_injection_and_fixed_speech(self):
        invalid = [step("navigate_to_point", point_id="point; echo unsafe"),
            step("verify_arrival", point_id="A" * 32),
            step("present_point", point_id=POINT_ID, text="固定讲解不属于这个动作"),
            step("present_point", point_id=POINT_ID, topic="x" * 201),
            step("present_point", point_id=POINT_ID, topic=None)]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_plan(plan(value))
        with self.assertRaises(ValueError):
            validate_plan(plan(*[step("present_point", point_id=POINT_ID) for _ in range(4)]))

    def test_named_navigation_requires_separate_field_verification(self):
        named = validate_plan(plan(step("navigate_to_point", point_id=POINT_ID)))
        with patch.dict(os.environ, {"GUIDE_NAMED_NAV_VERIFIED": "0"}):
            with self.assertRaises(ValueError):
                check_execution_policy(named, True, False, set())
            self.assertFalse(action_catalog()["named_navigation_verified"])
        with patch.dict(os.environ, {"GUIDE_NAMED_NAV_VERIFIED": "1"}):
            check_execution_policy(named, True, False, set())
            self.assertTrue(action_catalog()["named_navigation_verified"])


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.speech = FakeSpeech()
        self.status_calls, self.stop_calls = [], []
        self.executor = TaskExecutor(self.speech, project=self.temp.name,
            status=lambda: self.status_calls.append(1) or {"localized": False},
            assets=lambda: {"route": True},
            stop=lambda: self.stop_calls.append(1) or {"stopped": True})
        self.environment = patch.dict(os.environ, {"GUIDE_MOTION_ENABLED": "0", "GUIDE_ARM_ENABLED": "0",
            "GUIDE_NAMED_NAV_VERIFIED": "0"})
        self.environment.start()

    def tearDown(self):
        self.executor.cancel()
        if self.executor.thread:
            self.executor.thread.join(3)
        self.environment.stop()
        self.temp.cleanup()

    def finish(self):
        self.executor.thread.join(3)
        self.assertFalse(self.executor.thread.is_alive())
        return self.executor.snapshot()

    def test_dry_run_calls_no_hardware(self):
        self.executor.start(plan(step("arm_gesture", gesture="wave"), step("navigate_route", destination="end")), dry_run=True)
        result = self.finish()
        self.assertEqual(result["state"], "simulated")
        self.assertFalse(self.status_calls or self.stop_calls or self.speech.calls)

    def test_order_and_persistence(self):
        self.executor.start(plan(step("speak", text="第一句"), step("wait", seconds=0), step("speak", text="第二句")))
        result = self.finish()
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(self.speech.calls, ["第一句", "第二句"])
        saved = json.loads((Path(self.temp.name) / "run/tasks/current.json").read_text())
        self.assertEqual(saved, result)

    def test_failure_skips_later_steps(self):
        self.executor.start(plan(step("check_status", require_localized=True), step("speak", text="不应该朗读")))
        result = self.finish()
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["steps"][1]["state"], "skipped")
        self.assertFalse(self.speech.calls)

    def test_cancel_skips_later_steps_and_prevents_concurrency(self):
        self.executor.start(plan(step("wait", seconds=2), step("speak", text="不应该朗读")))
        with self.assertRaises(RuntimeError):
            self.executor.start(plan(step("stop")))
        self.assertTrue(self.executor.cancel())
        result = self.finish()
        self.assertEqual(result["state"], "cancelled")
        self.assertEqual(result["steps"][1]["state"], "skipped")
        self.assertFalse(self.speech.calls)

    def test_restart_never_resumes(self):
        self.executor.current = {"id": "old", "state": "running"}
        self.executor._persist()
        restored = TaskExecutor(self.speech, project=self.temp.name)
        self.assertEqual(restored.snapshot()["state"], "interrupted")
        self.assertFalse(restored.active())

    def test_owned_process_timeout(self):
        self.executor.current = {"id": "timeout"}
        with self.assertRaises(TimeoutError):
            self.executor._process([sys.executable, "-c", "import time; time.sleep(20)"], .1)
        self.assertIsNone(self.executor.child)

    def test_point_dry_run_has_no_verification_conception_or_hardware(self):
        calls = []
        self.executor.verify_callback = lambda **kwargs: calls.append("verify")
        self.executor.conception_callback = lambda **kwargs: calls.append("conception")
        self.executor.start(plan(step("navigate_to_point", point_id=POINT_ID),
            step("verify_arrival", point_id=POINT_ID), step("present_point", point_id=POINT_ID)), dry_run=True)
        self.assertEqual(self.finish()["state"], "simulated")
        self.assertFalse(calls or self.status_calls or self.stop_calls or self.speech.calls)

    def test_point_preflight_needs_maps_not_recorded_route(self):
        self.executor.status_callback = lambda: {"safe_controller": True, "localizer": True,
            "planner": True, "localized": True}
        named = validate_plan(plan(step("navigate_to_point", point_id=POINT_ID)))
        with patch.dict(os.environ, {"GUIDE_MOTION_ENABLED": "1", "GUIDE_NAMED_NAV_VERIFIED": "1"}), \
                patch("guide_task_executor.route_running", return_value=False):
            self.executor.assets_callback = lambda: {"map_2d": True, "map_3d": True, "route": False}
            self.executor._preflight(named)
            self.executor.assets_callback = lambda: {"map_2d": True, "map_3d": False, "route": True}
            with self.assertRaises(RuntimeError):
                self.executor._preflight(named)

    def test_point_navigation_uses_fixed_owned_helper_command(self):
        self.executor.status_callback = lambda: {"safe_controller": True, "localizer": True,
            "planner": True, "localized": True}
        self.executor.assets_callback = lambda: {"map_2d": True, "map_3d": True, "route": False}
        with patch.dict(os.environ, {"GUIDE_MOTION_ENABLED": "1", "GUIDE_NAMED_NAV_VERIFIED": "1"}), \
                patch("guide_task_executor.route_running", return_value=False), \
                patch.object(self.executor, "_process", return_value={"status": "success", "point_id": POINT_ID,
                    "verified": True, "arrived": True, "motion_disabled": True,
                    "position_error_m": .077, "yaw_error_rad": .034}) as process:
            self.executor.start(plan(step("navigate_to_point", point_id=POINT_ID)))
            result = self.finish()
        self.assertEqual(result["state"], "succeeded")
        args, kwargs = process.call_args
        self.assertEqual(args[0][:2], ["bash", "-lc"])
        self.assertIn("/usr/bin/python3 -u", args[0][2])
        self.assertIn("navigate_to_point_safe.py", args[0][2])
        self.assertIn("--point-id " + POINT_ID, args[0][2])
        self.assertNotIn("--commissioning-confirmed", args[0][2])
        self.assertNotIn("--present-after-arrival", args[0][2])
        self.assertEqual(kwargs, {"route": True, "json_result": True})
        self.assertTrue(self.stop_calls)

    def test_point_result_requires_owned_verified_arrival_and_disarm(self):
        good = {"status": "success", "point_id": POINT_ID, "verified": True,
                "arrived": True, "motion_disabled": True, "position_error_m": .077, "yaw_error_rad": .034}
        self.assertEqual(self.executor._validate_point_result(good, POINT_ID), good)
        bad = [None, {}, {**good, "point_id": "2" * 32}, {**good, "status": "error"}]
        for key in ("verified", "arrived", "motion_disabled"):
            bad.extend([{**good, key: False}, {**good, key: 1}, {k: v for k, v in good.items() if k != key}])
        for key in ("position_error_m", "yaw_error_rad"):
            bad.extend({**good, key: value} for value in (float("nan"), float("inf"), -.1, .201, True, "0.1", None))
        for result in bad:
            with self.subTest(result=result), self.assertRaises(RuntimeError):
                self.executor._validate_point_result(result, POINT_ID)

    def test_invalid_point_success_fails_task_and_stops_without_speaking(self):
        self.executor.status_callback = lambda: {"safe_controller": True, "localizer": True,
            "planner": True, "localized": True}
        self.executor.assets_callback = lambda: {"map_2d": True, "map_3d": True, "route": False}
        with patch.dict(os.environ, {"GUIDE_MOTION_ENABLED": "1", "GUIDE_NAMED_NAV_VERIFIED": "1"}), \
                patch("guide_task_executor.route_running", return_value=False), \
                patch.object(self.executor, "_process", return_value={"status": "success", "verified": True}):
            self.executor.start(plan(step("navigate_to_point", point_id=POINT_ID)))
            result = self.finish()
        self.assertEqual(result["state"], "failed")
        self.assertTrue(self.stop_calls)
        self.assertFalse(self.speech.calls)

    def setup_presentation(self, conceived=None, verify=None):
        events = []
        def conception(**kwargs):
            events.append(("conception", kwargs))
            return conceived or {"segments": ["第一段动态讲解。", "第二段动态讲解。"],
                "source_ids": ["board:" + POINT_ID], "needs_review": False}
        def verification(**kwargs):
            events.append(("verify", kwargs))
            return verify or {"status": "success", "point_id": POINT_ID, "verified": True}
        self.executor.conception_callback = conception
        self.executor.verify_callback = verification
        return events

    def test_present_generates_after_arrival_then_rechecks_and_speaks(self):
        events = self.setup_presentation()
        self.executor.start(plan(step("present_point", point_id=POINT_ID, topic="适合儿童")))
        result = self.finish()
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual([name for name, _ in events], ["verify", "conception", "verify"])
        self.assertEqual(events[1][1]["topic"], "适合儿童")
        self.assertIs(events[1][1]["cancel"], self.executor.cancel_event)
        self.assertEqual(self.speech.calls, ["第一段动态讲解。", "第二段动态讲解。"])
        self.assertEqual(result["steps"][0]["result"]["source_ids"], ["board:" + POINT_ID])
        self.assertFalse(self.stop_calls)

    def test_present_refuses_uncertain_or_unbounded_conception_before_speech(self):
        invalid = [
            {"segments": ["不确定内容"], "source_ids": ["board:test"], "needs_review": True},
            {"segments": ["遗漏审核字段"], "source_ids": ["board:test"]},
            {"segments": ["x" * 151], "source_ids": ["board:test"], "needs_review": False},
            {"segments": ["x" * 150] * 4, "source_ids": ["board:test"], "needs_review": False},
            {"segments": ["没有来源"], "source_ids": [], "needs_review": False},
            {"segments": ["来源字段非法"], "source_ids": [None], "needs_review": False},
        ]
        for value in invalid:
            with self.subTest(value=value):
                self.setup_presentation(conceived=value)
                self.executor.start(plan(step("present_point", point_id=POINT_ID)))
                self.assertEqual(self.finish()["state"], "failed")
                self.assertFalse(self.speech.calls)

    def test_present_never_generates_without_actual_arrival(self):
        events = self.setup_presentation(verify={"verified": False})
        self.executor.start(plan(step("present_point", point_id=POINT_ID)))
        self.assertEqual(self.finish()["state"], "failed")
        self.assertEqual([name for name, _ in events], ["verify"])
        self.assertFalse(self.speech.calls)

    def test_present_blocks_speech_when_robot_moves_during_conception(self):
        self.setup_presentation()
        answers = iter([{"verified": True}, {"verified": False}])
        self.executor.verify_callback = lambda **kwargs: next(answers)
        self.executor.start(plan(step("present_point", point_id=POINT_ID)))
        self.assertEqual(self.finish()["state"], "failed")
        self.assertFalse(self.speech.calls)

    def test_cancel_during_conception_prevents_speech(self):
        entered = threading.Event()
        self.setup_presentation()
        def conception(**kwargs):
            entered.set()
            kwargs["cancel"].wait(2)
            return {"segments": ["不应该播报"], "source_ids": ["board:test"], "needs_review": False}
        self.executor.conception_callback = conception
        self.executor.start(plan(step("present_point", point_id=POINT_ID)))
        self.assertTrue(entered.wait(2))
        self.executor.cancel()
        self.assertEqual(self.finish()["state"], "cancelled")
        self.assertFalse(self.speech.calls)

    def test_verify_rejects_wrong_point(self):
        self.executor.verify_callback = lambda **kwargs: {"verified": True, "point_id": "2" * 32}
        self.executor.start(plan(step("verify_arrival", point_id=POINT_ID)))
        self.assertEqual(self.finish()["state"], "failed")

    def test_process_json_uses_only_current_command_result(self):
        self.executor.current = {"id": "json_result"}
        result = self.executor._process([sys.executable, "-c",
            'print(\'{"status":"success","verified":true}\')'], 2, json_result=True)
        self.assertTrue(result["verified"])
        with self.assertRaises(RuntimeError):
            self.executor._process([sys.executable, "-c", 'print("no result")'], 2, json_result=True)
        with self.assertRaisesRegex(RuntimeError, "拒绝"):
            self.executor._process([sys.executable, "-c", 'print(\'{"status":"error","error":"拒绝"}\')'],
                2, json_result=True)

    def test_presentation_budget_refuses_before_any_audio(self):
        self.setup_presentation()
        self.executor.start(plan({**step("present_point", point_id=POINT_ID), "timeout": 1}))
        self.assertEqual(self.finish()["state"], "failed")
        self.assertFalse(self.speech.calls)

    def test_prefetched_presentation_still_checks_arrival_twice(self):
        events = self.setup_presentation()
        def provide(**kwargs):
            events.append(("prefetch", kwargs))
            return {"status": "success", "point_id": POINT_ID, "needs_review": False,
                    "segments": ["本次动态讲解。"], "source_ids": ["board:" + POINT_ID]}
        self.executor.presentation_provider = provide
        self.executor.presentation_cancel = Mock()
        self.executor.presentation_revalidate = Mock()
        self.executor.start(plan(step("present_point", point_id=POINT_ID)), presentation_ticket="a" * 32)
        result = self.finish()
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual([name for name, _ in events], ["verify", "prefetch", "verify"])
        self.assertEqual(self.speech.calls, ["本次动态讲解。"])
        self.assertTrue(result["steps"][0]["result"]["prefetched"])
        self.executor.presentation_cancel.assert_called_once_with("a" * 32)
        self.executor.presentation_revalidate.assert_called_once_with(ticket="a" * 32,
            point_id=POINT_ID, topic="")

    def test_updated_prior_after_final_arrival_check_prevents_audio(self):
        self.setup_presentation()
        self.executor.presentation_provider = Mock(return_value={"status": "success", "point_id": POINT_ID,
            "needs_review": False, "segments": ["不应播报。"], "source_ids": ["board:" + POINT_ID]})
        self.executor.presentation_revalidate = Mock(side_effect=RuntimeError("prior updated"))
        self.executor.start(plan(step("present_point", point_id=POINT_ID)), presentation_ticket="a" * 32)
        self.assertEqual(self.finish()["state"], "failed")
        self.assertFalse(self.speech.calls)

    def test_prefetched_presentation_never_consumed_or_spoken_if_not_arrived(self):
        self.setup_presentation(verify={"verified": False})
        self.executor.presentation_provider = Mock()
        self.executor.presentation_cancel = Mock()
        self.executor.start(plan(step("present_point", point_id=POINT_ID)), presentation_ticket="a" * 32)
        self.assertEqual(self.finish()["state"], "failed")
        self.executor.presentation_provider.assert_not_called()
        self.executor.presentation_cancel.assert_called_once_with("a" * 32)
        self.assertFalse(self.speech.calls)

    def test_prefetched_dry_run_never_consumes_or_cancels_calculation(self):
        self.executor.presentation_provider = Mock()
        self.executor.presentation_cancel = Mock()
        self.executor.start(plan(step("present_point", point_id=POINT_ID)), dry_run=True,
                            presentation_ticket="a" * 32)
        self.assertEqual(self.finish()["state"], "simulated")
        self.executor.presentation_provider.assert_not_called()
        self.executor.presentation_cancel.assert_not_called()
        self.assertFalse(self.speech.calls)

    def test_prefetched_wrong_point_or_failed_result_never_speaks(self):
        for value in ({"status": "success", "point_id": "b" * 32},
                      {"status": "error", "point_id": POINT_ID}):
            self.setup_presentation()
            self.executor.presentation_provider = Mock(return_value={**value, "needs_review": False,
                "segments": ["不播报。"], "source_ids": ["board:" + POINT_ID]})
            self.executor.start(plan(step("present_point", point_id=POINT_ID)), presentation_ticket="a" * 32)
            self.assertEqual(self.finish()["state"], "failed")
            self.assertFalse(self.speech.calls)

    def test_prefetch_metadata_cannot_attach_to_motion_or_arbitrary_plan(self):
        self.executor.presentation_provider = Mock()
        for raw_plan in (plan(step("navigate_to_point", point_id=POINT_ID)),
                         plan(step("present_point", point_id=POINT_ID), step("speak", text="其他"))):
            with self.assertRaises(ValueError):
                self.executor.start(raw_plan, presentation_ticket="a" * 32)
        with self.assertRaises(ValueError):
            self.executor.start(plan(step("present_point", point_id=POINT_ID)), presentation_ticket="../bad")


if __name__ == "__main__":
    unittest.main(verbosity=2)
