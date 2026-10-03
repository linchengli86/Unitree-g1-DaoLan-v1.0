#!/usr/bin/env python3
"""Bounded exhibit-board job regressions; no network, ROS, SDK or hardware."""

import base64
import copy
import json
import threading
import time
import unittest
from unittest.mock import Mock, patch

from guide_board_processing import BoardDraftManager, validate_tile_read


IMAGE = base64.b64encode(b"\xff\xd8\xfftest-board\xff\xd9").decode("ascii")
READ = {"facts": ["展板介绍了一项技术。"], "needs_review": False, "uncertainties": []}
DRAFT = {"speech": "这块展板介绍了一项技术。", "needs_review": False, "uncertainties": []}
TERMINAL = {"succeeded", "failed", "cancelled"}


def result(data, **extras):
    return {"status": "success", "text": json.dumps(data, ensure_ascii=False),
            "actions": [], "tool_calls": [], **extras}


def tiles(count=3):
    return [{"id": "tile-{}".format(i), "label": "区域 {}".format(i),
             "image_jpeg_base64": IMAGE} for i in range(count)]


class TileReadTests(unittest.TestCase):
    def test_valid_read_and_uncertainty_requires_review(self):
        self.assertEqual(validate_tile_read(json.dumps(READ)), READ)
        data = {**READ, "uncertainties": ["年份模糊"]}
        self.assertTrue(validate_tile_read(json.dumps(data))["needs_review"])

    def test_unknown_missing_and_wrong_field_types_rejected(self):
        invalid = [[], {**READ, "commands": ["move"]}, {"facts": []},
                   {**READ, "facts": "x"}, {**READ, "facts": [123]},
                   {**READ, "needs_review": 1}, {**READ, "uncertainties": "x"},
                   {**READ, "uncertainties": [False]}]
        for data in invalid:
            with self.subTest(data=data), self.assertRaises((ValueError, TypeError)):
                validate_tile_read(json.dumps(data))

    def test_fact_and_uncertainty_limits(self):
        invalid = [{**READ, "facts": ["x"] * 25}, {**READ, "facts": ["x" * 161]},
                   {**READ, "uncertainties": ["x"] * 9},
                   {**READ, "uncertainties": ["x" * 201]}]
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ValueError):
                validate_tile_read(json.dumps(data))


class BoardJobTests(unittest.TestCase):
    def setUp(self):
        self.managers = []
        self.release = threading.Event()

    def tearDown(self):
        self.release.set()
        for manager in self.managers:
            manager.close()

    def manager(self, prepare=None, query=None, timeout=2):
        manager = BoardDraftManager(prepare or (lambda image: tiles()),
                                    query or self.query, timeout=timeout)
        self.managers.append(manager)
        return manager

    @staticmethod
    def query(payload, timeout):
        if payload["mode"] == "board_read":
            return result(READ)
        if payload["mode"] == "board_merge":
            return result(DRAFT)
        raise AssertionError("Unsafe Omni request mode: " + str(payload.get("mode")))

    def wait_done(self, manager, job_id=None, limit=3):
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            snapshot = manager.snapshot(job_id)
            if snapshot["state"] in TERMINAL:
                return snapshot
            time.sleep(0.01)
        self.fail("Board job did not finish within test bound")

    def test_success_modes_progress_and_no_automatic_save(self):
        query = Mock(side_effect=self.query)
        prepare = Mock(return_value=tiles())
        manager = self.manager(prepare, query)
        # Draft creation must not commit any points or durable state.
        with patch("guide_points.GuidePointStore.add") as save, \
                patch("pathlib.Path.write_text") as write:
            started = manager.start(IMAGE)
            completed = self.wait_done(manager, started["id"])
            save.assert_not_called()
            write.assert_not_called()
        self.assertEqual(completed["state"], "succeeded")
        self.assertEqual(completed["completed"], completed["total"])
        self.assertEqual(completed["draft"], DRAFT)
        prepare.assert_called_once_with(IMAGE)
        requests = [call.args[0] for call in query.call_args_list]
        self.assertEqual(sum(p["mode"] == "board_read" for p in requests), 3)
        self.assertEqual(sum(p["mode"] == "board_merge" for p in requests), 1)
        for request in requests:
            if request["mode"] == "board_read":
                self.assertEqual(request["image_jpeg_base64"], IMAGE)
            else:
                self.assertFalse(request.get("image_jpeg_base64"))
                self.assertTrue(request.get("text"))
        self.assertTrue(all(0 < call.args[1] <= 2 for call in query.call_args_list))

    def test_invalid_image_rejected_before_prepare_or_query(self):
        prepare, query = Mock(), Mock()
        manager = self.manager(prepare, query)
        with self.assertRaises(ValueError):
            manager.start("not-valid-base64")
        prepare.assert_not_called()
        query.assert_not_called()

    def test_partial_read_failure_is_flagged_and_still_merged(self):
        calls = []
        def query(payload, timeout):
            calls.append(copy.deepcopy(payload))
            if payload["mode"] == "board_read" and "区域 1" in payload.get("text", ""):
                raise ConnectionResetError("test connection reset")
            return self.query(payload, timeout)
        manager = self.manager(query=query)
        completed = self.wait_done(manager, manager.start(IMAGE)["id"])
        self.assertEqual(completed["state"], "succeeded")
        self.assertTrue(completed["draft"]["needs_review"])
        self.assertTrue(completed["draft"]["uncertainties"])
        self.assertTrue(completed.get("failures"))
        self.assertEqual(sum(p["mode"] == "board_merge" for p in calls), 1)

    def test_all_failed_reads_do_not_merge(self):
        query = Mock(side_effect=ConnectionResetError("unavailable"))
        manager = self.manager(query=query)
        completed = self.wait_done(manager, manager.start(IMAGE)["id"])
        self.assertEqual(completed["state"], "failed")
        self.assertNotIn("draft", completed)
        self.assertTrue(all(call.args[0]["mode"] == "board_read" for call in query.call_args_list))

    def test_injected_read_actions_are_not_used(self):
        query = Mock(return_value=result(READ, actions=[{"skill": "request_route", "destination": "end"}]))
        manager = self.manager(query=query)
        completed = self.wait_done(manager, manager.start(IMAGE)["id"])
        self.assertEqual(completed["state"], "failed")
        self.assertTrue(all(call.args[0]["mode"] == "board_read" for call in query.call_args_list))

    def test_injected_merge_tool_calls_fail(self):
        def query(payload, timeout):
            if payload["mode"] == "board_merge":
                return result(DRAFT, tool_calls=[{"name": "stop_navigation"}])
            return result(READ)
        manager = self.manager(query=query)
        completed = self.wait_done(manager, manager.start(IMAGE)["id"])
        self.assertEqual(completed["state"], "failed")
        self.assertNotIn("draft", completed)

    def test_invalid_read_schema_is_partial_failure(self):
        def query(payload, timeout):
            if payload["mode"] == "board_read" and "区域 1" in payload.get("text", ""):
                return result({**READ, "robot_command": "move"})
            return self.query(payload, timeout)
        manager = self.manager(query=query)
        completed = self.wait_done(manager, manager.start(IMAGE)["id"])
        self.assertEqual(completed["state"], "succeeded")
        self.assertTrue(completed["draft"]["needs_review"])

    def test_merge_failure_does_not_create_draft(self):
        def query(payload, timeout):
            if payload["mode"] == "board_merge":
                raise TimeoutError("merge failed")
            return result(READ)
        manager = self.manager(query=query)
        completed = self.wait_done(manager, manager.start(IMAGE)["id"])
        self.assertEqual(completed["state"], "failed")
        self.assertNotIn("draft", completed)

    def test_invalid_merge_schema_is_rejected(self):
        def query(payload, timeout):
            if payload["mode"] == "board_merge":
                return result({**DRAFT, "speech": "字" * 151})
            return result(READ)
        manager = self.manager(query=query)
        completed = self.wait_done(manager, manager.start(IMAGE)["id"])
        self.assertEqual(completed["state"], "failed")
        self.assertNotIn("draft", completed)

    def test_at_most_two_tile_workers_and_single_active_job(self):
        lock, two_running = threading.Lock(), threading.Event()
        count, maximum = 0, 0
        def query(payload, timeout):
            nonlocal count, maximum
            if payload["mode"] == "board_read":
                with lock:
                    count += 1
                    maximum = max(count, maximum)
                    if count == 2:
                        two_running.set()
                self.release.wait(2)
                with lock:
                    count -= 1
            return self.query(payload, timeout)
        manager = self.manager(query=query)
        started = manager.start(IMAGE)
        self.assertTrue(two_running.wait(1))
        with self.assertRaises(RuntimeError):
            manager.start(IMAGE)
        self.assertEqual(manager.snapshot(started["id"])["id"], started["id"])
        self.release.set()
        self.assertEqual(self.wait_done(manager)["state"], "succeeded")
        self.assertEqual(maximum, 2)

    def test_prepared_tile_cap(self):
        query = Mock(side_effect=self.query)
        manager = self.manager(prepare=lambda image: tiles(6), query=query)
        try:
            started = manager.start(IMAGE)
        except ValueError:
            pass
        else:
            self.assertEqual(self.wait_done(manager, started["id"])["state"], "failed")
        query.assert_not_called()

    def test_timeout_bounds_job_without_late_draft(self):
        entered = threading.Event()
        def query(payload, timeout):
            entered.set()
            self.release.wait(1)
            return self.query(payload, timeout)
        manager = self.manager(query=query, timeout=0.08)
        started = manager.start(IMAGE)
        self.assertTrue(entered.wait(0.5))
        completed = self.wait_done(manager, started["id"], limit=0.8)
        self.assertEqual(completed["state"], "failed")
        self.release.set()
        time.sleep(0.03)
        self.assertEqual(manager.snapshot(started["id"])["state"], "failed")
        self.assertNotIn("draft", manager.snapshot(started["id"]))

    def test_cancel_prevents_merge_and_late_draft(self):
        entered = threading.Event()
        calls = []
        def query(payload, timeout):
            calls.append(payload["mode"])
            entered.set()
            self.release.wait(1)
            return self.query(payload, timeout)
        manager = self.manager(query=query)
        started = manager.start(IMAGE)
        self.assertTrue(entered.wait(0.5))
        manager.cancel()
        self.release.set()
        completed = self.wait_done(manager, started["id"])
        self.assertEqual(completed["state"], "cancelled")
        self.assertNotIn("board_merge", calls)
        self.assertNotIn("draft", completed)

    def test_snapshot_is_copy_and_unknown_id_rejected(self):
        manager = self.manager()
        started = manager.start(IMAGE)
        completed = self.wait_done(manager, started["id"])
        completed["draft"]["speech"] = "changed externally"
        self.assertEqual(manager.snapshot(started["id"])["draft"]["speech"], DRAFT["speech"])
        with self.assertRaises(ValueError):
            manager.snapshot("unknown-id")

    def test_new_job_after_completed_has_different_id(self):
        manager = self.manager()
        first = manager.start(IMAGE)
        self.wait_done(manager, first["id"])
        second = manager.start(IMAGE)
        self.assertNotEqual(second["id"], first["id"])
        self.assertEqual(self.wait_done(manager, second["id"])["state"], "succeeded")


if __name__ == "__main__":
    unittest.main(verbosity=2)
