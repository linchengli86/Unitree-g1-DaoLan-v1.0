#!/usr/bin/env python3
"""Dynamic conception and citation regressions; no ROS, SDK, hardware or cloud."""

import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from guide_conception import conceive_point, validate_presentation
from guide_knowledge import KnowledgeStore
from guide_points import GuidePointStore


POINT_ID = "a" * 32
OTHER_ID = "b" * 32
SUMMARY_SOURCE = "point:{}:legacy_summary".format(POINT_ID)
PHOTO_SOURCE = "point:{}:photo".format(POINT_ID)
CANDIDATE_SOURCE = "point:{}:board:{}".format(POINT_ID, "c" * 32)
IMAGE = b"\xff\xd8\xfffixture photo\xff\xd9"


def context():
    return {"point": {"id": POINT_ID, "name": "展点甲", "frame_id": "map",
                      "pose": {"x": 4.2, "y": -1, "z": 0, "yaw": .7},
                      "map_fingerprint": "d" * 64},
            "reviewed_prior": [{"source_id": SUMMARY_SOURCE, "type": "legacy_reviewed_summary",
                                "text": "旧的已审核摘要。", "reviewed": True,
                                "usage": "prior_facts_only_not_a_presentation_script"}],
            "candidates": [{"source_id": CANDIDATE_SOURCE, "type": "board_evidence", "reviewed": False,
                            "evidence": {"regions": [{"facts": ["未经核对的新信息。"]}]}}],
            "sources": [{"source_id": SUMMARY_SOURCE, "type": "legacy_reviewed_summary", "reviewed": True}],
            "documents": []}


def presentation(segments=None, sources=None, needs_review=False, uncertainties=None):
    return {"segments": segments if segments is not None else ["这是一段现场组织的新讲解。"],
            "source_ids": sources if sources is not None else [SUMMARY_SOURCE],
            "needs_review": needs_review,
            "uncertainties": uncertainties if uncertainties is not None else []}


def response(data=None, **extras):
    return {"status": "success", "text": json.dumps(data or presentation(), ensure_ascii=False),
            "actions": [], "tool_calls": [], **extras}


class FakeStore:
    def __init__(self, data, photo_path=None):
        self.data = data
        self.requests = []
        self.points = Mock()
        self.points.image_path.return_value = photo_path

    def prepare_context(self, point_id):
        self.requests.append(point_id)
        if point_id != self.data["point"]["id"]:
            raise ValueError("Unknown point")
        return copy.deepcopy(self.data)


class PresentationValidationTests(unittest.TestCase):
    def test_reviewed_prior_allowed_and_citations_deduplicated(self):
        valid = presentation(segments=[" 新讲解 "], sources=[SUMMARY_SOURCE, SUMMARY_SOURCE])
        result = validate_presentation(json.dumps(valid), context())
        self.assertEqual(result["segments"], ["新讲解"])
        self.assertEqual(result["source_ids"], [SUMMARY_SOURCE])

    def test_candidate_and_other_point_citations_rejected(self):
        for source in (CANDIDATE_SOURCE, "point:{}:legacy_summary".format(OTHER_ID), "invented-reference"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                validate_presentation(json.dumps(presentation(sources=[source])), context())

    def test_photo_citation_requires_actually_included_photo(self):
        prior = context()
        photo = {"source_id": PHOTO_SOURCE, "type": "prior_photo", "included_in_request": False}
        prior["sources"].append(photo)
        with self.assertRaises(ValueError):
            validate_presentation(json.dumps(presentation(sources=[PHOTO_SOURCE])), prior)
        photo["included_in_request"] = True
        self.assertEqual(validate_presentation(json.dumps(presentation(sources=[PHOTO_SOURCE])), prior)["source_ids"], [PHOTO_SOURCE])

    def test_empty_segments_only_allowed_for_needs_review(self):
        valid = presentation(segments=[], sources=[], needs_review=True, uncertainties=["展板内容不足。"])
        self.assertEqual(validate_presentation(json.dumps(valid), context())["segments"], [])
        with self.assertRaises(ValueError):
            validate_presentation(json.dumps(presentation(segments=[], sources=[])), context())

    def test_spoken_segments_without_source_rejected(self):
        with self.assertRaises(ValueError):
            validate_presentation(json.dumps(presentation(sources=[])), context())

    def test_format_and_length_bounds(self):
        invalid = [[], {**presentation(), "command": "move"}, {"segments": ["x"]},
                   presentation(segments=["x"] * 5), presentation(segments=["x" * 151]),
                   presentation(segments=["x" * 150] * 4), presentation(segments=[" "]),
                   presentation(segments=[123]), presentation(sources=[SUMMARY_SOURCE] * 17),
                   presentation(sources=[False]), presentation(sources=["x" * 129]),
                   presentation(needs_review=1), presentation(uncertainties=["x"] * 9),
                   presentation(uncertainties=["x" * 201]), presentation(uncertainties=[False])]
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ValueError):
                validate_presentation(json.dumps(data), context())

    def test_uncertainty_forces_review_even_when_model_says_ready(self):
        result = validate_presentation(json.dumps(presentation(uncertainties=["年份不清晰。"])), context())
        self.assertTrue(result["needs_review"], "Uncertainty must prevent automatic presentation")

    def test_reviewed_literature_chunk_citations_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            config = project / "config"
            config.mkdir()
            point = {**context()["point"], "speech": "已有摘要", "gesture": ""}
            points_path = config / "guide_points.json"
            points_path.write_text(json.dumps({"version": 1, "points": [point]}), encoding="utf-8")
            store = KnowledgeStore(project, GuidePointStore(points_path))
            document = store.add_document(POINT_ID, "source.txt", "已审核文献，供讲解引用。".encode(), reviewed=True)
            prior = store.prepare_context(POINT_ID)
            literature = next(item for item in prior["reviewed_prior"] if item["type"] == "reviewed_literature")
            chunk_id = literature["chunks"][0]["source_id"]
            self.assertEqual(literature["source_id"], document["source_id"])
            self.assertNotEqual(chunk_id, literature["source_id"])
            valid = validate_presentation(json.dumps(presentation(sources=[chunk_id])), prior)
            self.assertEqual(valid["source_ids"], [chunk_id])


class ConceptionTests(unittest.TestCase):
    def test_dynamic_payload_prior_not_script_and_no_fixed_speech(self):
        store = FakeStore(context())
        query = Mock(return_value=response())
        result = conceive_point(point_id=POINT_ID, topic=" 科普版 ", store=store, query=query)
        self.assertEqual(store.requests, [POINT_ID])
        payload, timeout = query.call_args.args
        self.assertEqual(payload["mode"], "point_conception")
        material = json.loads(payload["text"])
        self.assertEqual(material["topic"], "科普版")
        self.assertEqual(material["point"]["id"], POINT_ID)
        self.assertEqual(material["reviewed_prior"][0]["type"], "legacy_reviewed_summary")
        self.assertIn("旧摘要不是必须复述", material["instructions"])
        self.assertNotIn("speech", payload)
        self.assertNotIn("speech", result)
        self.assertNotIn("image_jpeg_base64", payload)
        self.assertNotEqual(result["segments"][0], context()["reviewed_prior"][0]["text"])
        self.assertEqual(result["presentation_mode"], "dynamic_conception")
        self.assertEqual(timeout, 35)
        store.points.image_path.assert_not_called()

    def test_photo_prepared_and_marked_as_included(self):
        with tempfile.TemporaryDirectory() as directory:
            photo = Path(directory) / "photo.jpg"
            photo.write_bytes(IMAGE)
            data = context()
            data["sources"].append({"source_id": PHOTO_SOURCE, "type": "prior_photo"})
            store = FakeStore(data, photo)
            query = Mock(return_value=response(presentation(sources=[PHOTO_SOURCE])))
            with patch("guide_conception.prepare_tiles", return_value=[{"image_jpeg_base64": "prepared-jpeg"}]) as prepare:
                result = conceive_point(point_id=POINT_ID, store=store, query=query)
            self.assertEqual(result["source_ids"], [PHOTO_SOURCE])
            prepare.assert_called_once()
            payload = query.call_args.args[0]
            self.assertEqual(payload["image_jpeg_base64"], "prepared-jpeg")
            material = json.loads(payload["text"])
            self.assertTrue(material["sources"][-1]["included_in_request"])
            self.assertNotIn("included_in_request", data["sources"][-1])

    def test_candidate_only_or_pending_pdf_is_not_usable_prior(self):
        data = context()
        data["reviewed_prior"] = []
        data["sources"] = []
        data["candidates"].append({"source_id": "pending-pdf", "type": "literature_attachment", "status": "pending_extraction"})
        query = Mock()
        with self.assertRaises(ValueError):
            conceive_point(point_id=POINT_ID, store=FakeStore(data), query=query)
        query.assert_not_called()

    def test_tools_actions_and_failed_response_rejected(self):
        invalid = [response(actions=[{"type": "speak"}]), response(tool_calls=[{"name": "stop_navigation"}]),
                   response(status="error")]
        for returned in invalid:
            with self.subTest(returned=returned), self.assertRaises(RuntimeError):
                conceive_point(point_id=POINT_ID, store=FakeStore(context()), query=Mock(return_value=returned))
        # A rejected tool response must not leave the single-flight lock held.
        self.assertEqual(conceive_point(point_id=POINT_ID, store=FakeStore(context()), query=Mock(return_value=response()))["status"], "success")

    def test_cancel_before_cloud_and_after_response(self):
        cancel = threading.Event()
        cancel.set()
        query = Mock(return_value=response())
        with self.assertRaises(InterruptedError):
            conceive_point(point_id=POINT_ID, cancel=cancel, store=FakeStore(context()), query=query)
        query.assert_not_called()
        cancel.clear()

        def cancelled(payload, timeout):
            cancel.set()
            return response()
        with self.assertRaises(InterruptedError):
            conceive_point(point_id=POINT_ID, cancel=cancel, store=FakeStore(context()), query=cancelled)

    def test_cancel_during_prior_preparation_does_not_start_cloud_request(self):
        cancel = threading.Event()
        store = FakeStore(context())
        original = store.prepare_context

        def prepared(point_id):
            result = original(point_id)
            cancel.set()
            return result
        store.prepare_context = prepared
        query = Mock(return_value=response())
        with self.assertRaises(InterruptedError):
            conceive_point(point_id=POINT_ID, cancel=cancel, store=store, query=query)
        query.assert_not_called()

    def test_topic_and_material_limits(self):
        for topic in ("x" * 201, 123, None):
            query = Mock()
            with self.subTest(topic=topic), self.assertRaises(ValueError):
                conceive_point(point_id=POINT_ID, topic=topic, store=FakeStore(context()), query=query)
            query.assert_not_called()
        data = context()
        data["reviewed_prior"][0]["text"] = "x" * 60001
        query = Mock()
        with self.assertRaises(ValueError):
            conceive_point(point_id=POINT_ID, store=FakeStore(data), query=query)
        query.assert_not_called()

    def test_only_one_conception_allowed_and_lock_released(self):
        entered, release = threading.Event(), threading.Event()
        errors = []

        def query(payload, timeout):
            entered.set()
            if not release.wait(2):
                raise AssertionError("test release timeout")
            return response()

        def run():
            try:
                conceive_point(point_id=POINT_ID, store=FakeStore(context()), query=query)
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=run)
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            with self.assertRaises(RuntimeError):
                conceive_point(point_id=POINT_ID, store=FakeStore(context()), query=Mock(return_value=response()))
        finally:
            release.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(conceive_point(point_id=POINT_ID, store=FakeStore(context()), query=Mock(return_value=response()))["status"], "success")


if __name__ == "__main__":
    unittest.main()
