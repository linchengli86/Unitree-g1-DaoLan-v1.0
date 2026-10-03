#!/usr/bin/env python3
"""Prefetch regressions: no ROS, SDK, hardware, network or actual Omni."""

import copy
import threading
import time
import unittest

from guide_conception_jobs import ConceptionJobs


POINT_ID = "a" * 32
OTHER_ID = "b" * 32
SOURCE = "point:" + POINT_ID + ":legacy_summary"
PHOTO = "point:" + POINT_ID + ":photo"


def context():
    return {"point": {"id": POINT_ID, "name": "音圈展点", "frame_id": "map",
                      "pose": {"x": 1, "y": 2, "yaw": 0}, "map_fingerprint": "d" * 64},
            "reviewed_prior": [{"source_id": SOURCE, "reviewed": True, "text": "已审核事实"}],
            "sources": [{"source_id": SOURCE, "reviewed": True}], "candidates": [], "documents": []}


def conceived():
    return {"status": "success", "point_id": POINT_ID, "point_name": "音圈展点",
            "map_fingerprint": "d" * 64, "generated_at": time.time(),
            "presentation_mode": "dynamic_conception", "segments": ["本次动态构思。"],
            "source_ids": [SOURCE], "needs_review": False, "uncertainties": []}


class ConceptionJobsTests(unittest.TestCase):
    def setUp(self):
        self.data = context()
        self.calls = []
        self.cancel = threading.Event()
        self.jobs = ConceptionJobs(lambda point_id: copy.deepcopy(self.data), self.generate)

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return conceived()

    def wait_ready(self, ticket):
        self.assertTrue(self.jobs.jobs[ticket]["done"].wait(2))

    def test_start_is_async_and_passes_only_pure_conception_arguments(self):
        entered, release = threading.Event(), threading.Event()
        def generate(**kwargs):
            self.calls.append(kwargs)
            entered.set()
            self.assertTrue(release.wait(2))
            return conceived()
        self.jobs.conceive = generate
        job = self.jobs.start(POINT_ID, "  给儿童  ")
        self.assertEqual(job["state"], "running")
        self.assertRegex(job["id"], r"^[0-9a-f]{32}$")
        self.assertTrue(entered.wait(2))
        self.assertEqual(self.jobs.binding(job["id"], POINT_ID, "给儿童")["state"], "running")
        self.assertEqual(set(self.calls[0]), {"point_id", "topic", "cancel"})
        self.assertEqual(self.calls[0]["topic"], "给儿童")
        release.set()
        result = self.jobs.consume(job["id"], POINT_ID, "给儿童", self.cancel, 2)
        self.assertEqual(result["segments"], ["本次动态构思。"])

    def test_binding_does_not_consume_and_ticket_is_one_use(self):
        ticket = self.jobs.start(POINT_ID)["id"]
        self.wait_ready(ticket)
        for _ in range(2):
            self.assertEqual(self.jobs.binding(ticket, POINT_ID, "")["state"], "ready")
        self.assertEqual(self.jobs.consume(ticket, POINT_ID, "", self.cancel, 2)["point_id"], POINT_ID)
        with self.assertRaises(ValueError):
            self.jobs.consume(ticket, POINT_ID, "", self.cancel, 2)

    def test_concurrent_consumers_cannot_reuse_ticket(self):
        ticket = self.jobs.start(POINT_ID)["id"]
        self.wait_ready(ticket)
        original = self.jobs.context_factory
        local = threading.local()
        barrier = threading.Barrier(2)
        def factory(point_id):
            local.reads = getattr(local, "reads", 0) + 1
            result = original(point_id)
            if local.reads == 2:
                barrier.wait(timeout=2)
            return result
        self.jobs.context_factory = factory
        results, failures = [], []
        def consume():
            try:
                results.append(self.jobs.consume(ticket, POINT_ID, "", self.cancel, 2))
            except Exception as exc:
                failures.append(exc)
        threads = [threading.Thread(target=consume) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ValueError)

    def test_consumed_revalidation_returns_only_binding_never_more_text(self):
        ticket = self.jobs.start(POINT_ID)["id"]
        self.wait_ready(ticket)
        with self.assertRaises(ValueError):
            self.jobs.validate_consumed(ticket, POINT_ID, "")
        self.jobs.consume(ticket, POINT_ID, "", self.cancel, 2)
        for _ in range(3):
            result = self.jobs.validate_consumed(ticket, POINT_ID, "")
            self.assertEqual(result["state"], "consumed")
            self.assertNotIn("segments", result)
            self.assertNotIn("result", result)
        with self.assertRaises(ValueError):
            self.jobs.consume(ticket, POINT_ID, "", self.cancel, 2)

    def test_consumed_revalidation_rejects_material_or_map_changes(self):
        for modify in (lambda data: data["reviewed_prior"][0].update(text="已经改动的先验"),
                       lambda data: data["point"].update(map_fingerprint="f" * 64)):
            with self.subTest(modification=modify):
                self.data = context()
                self.jobs = ConceptionJobs(lambda point_id: copy.deepcopy(self.data), self.generate)
                ticket = self.jobs.start(POINT_ID)["id"]
                self.wait_ready(ticket)
                self.jobs.consume(ticket, POINT_ID, "", self.cancel, 2)
                modify(self.data)
                with self.assertRaises(RuntimeError):
                    self.jobs.validate_consumed(ticket, POINT_ID, "")

    def test_consumed_revalidation_rejects_expiration_and_other_binding(self):
        now = [100.0]
        self.jobs = ConceptionJobs(lambda point_id: context(), self.generate, clock=lambda: now[0])
        ticket = self.jobs.start(POINT_ID)["id"]
        self.wait_ready(ticket)
        self.jobs.consume(ticket, POINT_ID, "", self.cancel, 2)
        for point_id, topic in ((OTHER_ID, ""), (POINT_ID, "不同主题")):
            with self.subTest(point_id=point_id, topic=topic), self.assertRaises(ValueError):
                self.jobs.validate_consumed(ticket, point_id, topic)
        now[0] += 240
        with self.assertRaises(TimeoutError):
            self.jobs.validate_consumed(ticket, POINT_ID, "")

    def test_stop_cancel_all_revokes_consumed_audio_binding(self):
        ticket = self.jobs.start(POINT_ID)["id"]
        self.wait_ready(ticket)
        self.jobs.consume(ticket, POINT_ID, "", self.cancel, 2)
        self.assertEqual(self.jobs.cancel_all(), 1)
        with self.assertRaises(InterruptedError):
            self.jobs.validate_consumed(ticket, POINT_ID, "")

    def test_every_trip_generates_new_result(self):
        tickets = []
        for _ in range(5):
            ticket = self.jobs.start(POINT_ID)["id"]
            self.wait_ready(ticket)
            self.jobs.consume(ticket, POINT_ID, "", self.cancel, 2)
            tickets.append(ticket)
        self.assertEqual(len(set(tickets)), 5)
        self.assertEqual(len(self.calls), 5)
        self.assertLessEqual(len(self.jobs.jobs), 4)

    def test_only_one_conceiver_can_run_even_after_cancel(self):
        entered, release = threading.Event(), threading.Event()
        def generate(**kwargs):
            entered.set()
            release.wait(2)
            return conceived()
        self.jobs.conceive = generate
        ticket = self.jobs.start(POINT_ID)["id"]
        self.assertTrue(entered.wait(2))
        with self.assertRaises(RuntimeError):
            self.jobs.start(POINT_ID)
        self.assertTrue(self.jobs.cancel(ticket))
        with self.assertRaises(RuntimeError):
            self.jobs.start(POINT_ID)
        release.set()
        self.wait_ready(ticket)
        second = self.jobs.start(POINT_ID)["id"]
        self.wait_ready(second)

    def test_storage_is_bounded_to_four_jobs(self):
        for _ in range(4):
            ticket = self.jobs.start(POINT_ID)["id"]
            self.wait_ready(ticket)
        with self.assertRaises(RuntimeError):
            self.jobs.start(POINT_ID)
        self.assertEqual(self.jobs.cancel_all(), 4)
        ticket = self.jobs.start(POINT_ID)["id"]
        self.wait_ready(ticket)
        self.assertEqual(len(self.jobs.jobs), 1)

    def test_ticket_rejects_other_point_or_topic(self):
        ticket = self.jobs.start(POINT_ID, "儿童")["id"]
        self.wait_ready(ticket)
        for point_id, topic in ((OTHER_ID, "儿童"), (POINT_ID, "成人")):
            with self.subTest(point_id=point_id, topic=topic), self.assertRaises(ValueError):
                self.jobs.binding(ticket, point_id, topic)
        self.assertEqual(self.jobs.binding(ticket, POINT_ID, "儿童")["state"], "ready")

    def test_all_material_changes_invalidate_ready_ticket(self):
        modifications = [
            lambda data: data["point"].update(pose={"x": 9, "y": 2, "yaw": 0}),
            lambda data: data["point"].update(map_fingerprint="e" * 64),
            lambda data: data["reviewed_prior"][0].update(reviewed=False),
            lambda data: data["reviewed_prior"][0].update(text="已修改事实"),
            lambda data: data["sources"].append({"type": "prior_photo", "source_id": PHOTO, "sha256": "f" * 64}),
            lambda data: data["documents"].append({"reviewed": True, "sha256": "c" * 64}),
            lambda data: data["candidates"].append({"text": "新冲突信息"}),
        ]
        for modify in modifications:
            with self.subTest(modification=modify):
                self.data = context()
                self.jobs = ConceptionJobs(lambda point_id: copy.deepcopy(self.data), self.generate)
                ticket = self.jobs.start(POINT_ID)["id"]
                self.wait_ready(ticket)
                modify(self.data)
                with self.assertRaises(RuntimeError):
                    self.jobs.consume(ticket, POINT_ID, "", self.cancel, 2)

    def test_context_change_before_generation_prevents_model_call(self):
        reads = []
        def factory(point_id):
            reads.append(point_id)
            data = context()
            if len(reads) > 1:
                data["point"]["name"] = "不同展点名称"
            return data
        self.jobs.context_factory = factory
        ticket = self.jobs.start(POINT_ID)["id"]
        self.wait_ready(ticket)
        with self.assertRaises(RuntimeError):
            self.jobs.consume(ticket, POINT_ID, "", self.cancel, 2)
        self.assertFalse(self.calls)

    def test_context_change_during_generation_rejects_result(self):
        def generate(**kwargs):
            self.data["reviewed_prior"][0]["text"] = "生成期间被更改"
            return conceived()
        self.jobs.conceive = generate
        ticket = self.jobs.start(POINT_ID)["id"]
        self.wait_ready(ticket)
        with self.assertRaises(RuntimeError):
            self.jobs.consume(ticket, POINT_ID, "", self.cancel, 2)

    def test_context_change_during_final_consume_is_detected(self):
        ticket = self.jobs.start(POINT_ID)["id"]
        self.wait_ready(ticket)
        original = self.jobs.context_factory
        reads = []
        def factory(point_id):
            reads.append(point_id)
            if len(reads) == 2:
                self.data["point"]["name"] = "消费前变化"
            return original(point_id)
        self.jobs.context_factory = factory
        with self.assertRaises(RuntimeError):
            self.jobs.consume(ticket, POINT_ID, "", self.cancel, 2)

    def test_canonical_hash_ignores_object_key_order(self):
        ticket = self.jobs.start(POINT_ID)["id"]
        self.wait_ready(ticket)
        self.data = dict(reversed(list(self.data.items())))
        self.assertEqual(self.jobs.consume(ticket, POINT_ID, "", self.cancel, 2)["status"], "success")

    def test_invalid_or_unreviewed_result_is_rejected(self):
        changes = [{"needs_review": True}, {"uncertainties": ["资料冲突"]},
                   {"actions": [{"action": "move"}]}, {"tool_calls": [{"name": "move"}]},
                   {"point_id": OTHER_ID}, {"map_fingerprint": "e" * 64}, {"status": "error"},
                   {"source_ids": ["point:" + OTHER_ID + ":legacy_summary"]},
                   {"source_ids": []}, {"segments": ["x" * 151]}, {"segments": []},
                   {"segments": ["x" * 150] * 4}, {"uncertainties": None}]
        for change in changes:
            with self.subTest(change=change):
                self.jobs = ConceptionJobs(lambda point_id: context(), lambda **kwargs: dict(conceived(), **change))
                ticket = self.jobs.start(POINT_ID)["id"]
                self.wait_ready(ticket)
                with self.assertRaises(RuntimeError):
                    self.jobs.consume(ticket, POINT_ID, "", self.cancel, 2)

    def test_photo_contract_accepts_first_photo_not_unprovided_second_photo(self):
        first, second = PHOTO, PHOTO + ":second"
        self.data["sources"].extend([
            {"type": "prior_photo", "source_id": first, "sha256": "f" * 64},
            {"type": "prior_photo", "source_id": second, "sha256": "e" * 64, "included_in_request": True}])
        self.jobs.conceive = lambda **kwargs: dict(conceived(), source_ids=[first])
        ticket = self.jobs.start(POINT_ID)["id"]
        self.wait_ready(ticket)
        self.assertEqual(self.jobs.consume(ticket, POINT_ID, "", self.cancel, 2)["source_ids"], [first])
        self.jobs.conceive = lambda **kwargs: dict(conceived(), source_ids=[second])
        ticket = self.jobs.start(POINT_ID)["id"]
        self.wait_ready(ticket)
        with self.assertRaises(RuntimeError):
            self.jobs.consume(ticket, POINT_ID, "", self.cancel, 2)

    def test_approved_literature_chunk_is_allowed_but_pending_source_is_not(self):
        literature = "point:" + POINT_ID + ":document:" + "c" * 32
        chunk = literature + ":chunk:1"
        self.data["reviewed_prior"].append({"source_id": literature, "reviewed": True,
                                             "chunks": [{"source_id": chunk, "text": "核验文献"}]})
        pending = "point:" + POINT_ID + ":document:" + "b" * 32
        self.data["sources"].append({"source_id": pending, "type": "literature",
                                     "reviewed": False, "status": "pending_extraction"})
        self.jobs.conceive = lambda **kwargs: dict(conceived(), source_ids=[chunk])
        ticket = self.jobs.start(POINT_ID)["id"]
        self.wait_ready(ticket)
        self.assertEqual(self.jobs.consume(ticket, POINT_ID, "", self.cancel, 2)["source_ids"], [chunk])
        self.jobs.conceive = lambda **kwargs: dict(conceived(), source_ids=[pending])
        ticket = self.jobs.start(POINT_ID)["id"]
        self.wait_ready(ticket)
        with self.assertRaises(RuntimeError):
            self.jobs.consume(ticket, POINT_ID, "", self.cancel, 2)

    def test_unreviewed_context_source_is_not_authoritative(self):
        self.data["reviewed_prior"][0]["reviewed"] = False
        ticket = self.jobs.start(POINT_ID)["id"]
        self.wait_ready(ticket)
        with self.assertRaises(RuntimeError):
            self.jobs.consume(ticket, POINT_ID, "", self.cancel, 2)

    def test_failed_callback_is_closed_and_next_start_can_recover(self):
        def fail(**kwargs):
            raise RuntimeError("Mock model unavailable")
        self.jobs.conceive = fail
        ticket = self.jobs.start(POINT_ID)["id"]
        self.wait_ready(ticket)
        with self.assertRaises(RuntimeError):
            self.jobs.consume(ticket, POINT_ID, "", self.cancel, 2)
        self.jobs.conceive = self.generate
        second = self.jobs.start(POINT_ID)["id"]
        self.wait_ready(second)
        self.assertEqual(self.jobs.consume(second, POINT_ID, "", self.cancel, 2)["status"], "success")

    def test_ttl_expiration_including_during_generation(self):
        now = [100.0]
        self.jobs = ConceptionJobs(lambda point_id: context(), self.generate, clock=lambda: now[0])
        ticket = self.jobs.start(POINT_ID)["id"]
        self.wait_ready(ticket)
        now[0] += 240
        with self.assertRaises(TimeoutError):
            self.jobs.consume(ticket, POINT_ID, "", self.cancel, 2)
        def expired(**kwargs):
            now[0] += 240
            return conceived()
        self.jobs.conceive = expired
        ticket = self.jobs.start(POINT_ID)["id"]
        self.wait_ready(ticket)
        with self.assertRaises(TimeoutError):
            self.jobs.binding(ticket, POINT_ID, "")

    def test_wait_timeout_cancels_background_job(self):
        entered = threading.Event()
        def generate(**kwargs):
            entered.set()
            kwargs["cancel"].wait(2)
            return conceived()
        self.jobs.conceive = generate
        ticket = self.jobs.start(POINT_ID)["id"]
        self.assertTrue(entered.wait(2))
        with self.assertRaises(TimeoutError):
            self.jobs.consume(ticket, POINT_ID, "", self.cancel, .05)
        self.wait_ready(ticket)
        self.assertEqual(self.jobs.jobs[ticket]["state"], "cancelled")
        self.assertTrue(self.jobs.jobs[ticket]["cancel"].is_set())

    def test_external_cancel_and_cancel_all_block_results(self):
        ticket = self.jobs.start(POINT_ID)["id"]
        self.wait_ready(ticket)
        self.cancel.set()
        with self.assertRaises(InterruptedError):
            self.jobs.consume(ticket, POINT_ID, "", self.cancel, 2)
        self.assertFalse(self.jobs.cancel(ticket))
        ticket = self.jobs.start(POINT_ID)["id"]
        self.wait_ready(ticket)
        self.assertEqual(self.jobs.cancel_all(), 1)
        with self.assertRaises(InterruptedError):
            self.jobs.binding(ticket, POINT_ID, "")

    def test_cancellation_while_waiting_discards_late_model_result(self):
        entered, returned = threading.Event(), threading.Event()
        def generate(**kwargs):
            entered.set()
            kwargs["cancel"].wait(2)
            returned.set()
            return conceived()
        self.jobs.conceive = generate
        ticket = self.jobs.start(POINT_ID)["id"]
        self.assertTrue(entered.wait(2))
        failures = []
        def consume():
            try:
                self.jobs.consume(ticket, POINT_ID, "", self.cancel, 2)
            except Exception as exc:
                failures.append(exc)
        worker = threading.Thread(target=consume)
        worker.start()
        self.cancel.set()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], InterruptedError)
        self.assertTrue(returned.wait(2))
        self.wait_ready(ticket)
        self.assertEqual(self.jobs.jobs[ticket]["state"], "cancelled")
        self.assertIsNone(self.jobs.jobs[ticket]["result"])

    def test_restart_has_no_old_ticket_and_unknown_cancel_is_harmless(self):
        ticket = self.jobs.start(POINT_ID)["id"]
        self.wait_ready(ticket)
        restarted = ConceptionJobs(lambda point_id: context(), self.generate)
        with self.assertRaises(ValueError):
            restarted.binding(ticket, POINT_ID, "")
        self.assertFalse(restarted.cancel(ticket))

    def test_input_validation_and_context_identity(self):
        for value in (0, -1, True, float("nan"), float("inf"), 241):
            with self.subTest(ttl=value), self.assertRaises(ValueError):
                ConceptionJobs(lambda point_id: context(), self.generate, ttl=value)
        for point_id, topic in (("bad", ""), (POINT_ID, None), (POINT_ID, "x" * 201)):
            with self.subTest(point_id=point_id, topic=topic), self.assertRaises(ValueError):
                self.jobs.start(point_id, topic)
        self.data["point"]["id"] = OTHER_ID
        with self.assertRaises(ValueError):
            self.jobs.start(POINT_ID)

    def test_invalid_wait_durations_never_consume_valid_result(self):
        ticket = self.jobs.start(POINT_ID)["id"]
        self.wait_ready(ticket)
        for timeout in (0, -1, True, float("nan"), float("inf"), "1"):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                self.jobs.consume(ticket, POINT_ID, "", self.cancel, timeout)
        self.assertEqual(self.jobs.consume(ticket, POINT_ID, "", self.cancel, 2)["status"], "success")


if __name__ == "__main__":
    unittest.main(verbosity=2)
