#!/usr/bin/env python3
"""Knowledge/prior regressions: temporary fixtures, no ROS, network or robot."""

import copy
import hashlib
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from guide_knowledge import KnowledgeStore, MAX_DOCUMENT_BYTES, validate_evidence
from guide_points import GuidePointStore


POINT_ID = "a" * 32
OTHER_ID = "b" * 32
IMAGE = b"\xff\xd8\xffimmutable prior photo\xff\xd9"
IMAGE_HASH = hashlib.sha256(IMAGE).hexdigest()
EVIDENCE = {"regions": [{"region": "整图", "facts": ["展板介绍技术甲。"],
                         "needs_review": False, "uncertainties": []}], "failed_regions": []}


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.project = Path(self.directory.name)
        config = self.project / "config"
        config.mkdir()
        self.points_path = config / "guide_points.json"
        point = {"id": POINT_ID, "name": "展点甲", "frame_id": "map",
                 "pose": {"x": 4.2, "y": -1.0, "z": 0, "yaw": .7},
                 "map_fingerprint": "c" * 64, "speech": "原先经核对的展板摘要。",
                 "gesture": "", "image": "/api/points/" + POINT_ID + "/image"}
        other = {**point, "id": OTHER_ID, "name": "展点乙", "speech": "", "image": ""}
        self.points_path.write_text(json.dumps({"version": 1, "points": [point, other]}), encoding="utf-8")
        folder = config / "guide_point_images"
        folder.mkdir()
        (folder / (POINT_ID + ".jpg")).write_bytes(IMAGE)
        self.original_points = self.points_path.read_bytes()
        self.store = KnowledgeStore(self.project, GuidePointStore(self.points_path))

    def tearDown(self):
        self.assertEqual(self.points_path.read_bytes(), self.original_points)
        self.directory.cleanup()

    def test_context_anchor_legacy_summary_is_prior_not_script(self):
        context = self.store.prepare_context(POINT_ID)
        self.assertEqual(context["point"]["name"], "展点甲")
        self.assertEqual(context["point"]["map_fingerprint"], "c" * 64)
        self.assertEqual(context["reviewed_prior"][0]["type"], "legacy_reviewed_summary")
        self.assertEqual(context["reviewed_prior"][0]["usage"], "prior_facts_only_not_a_presentation_script")
        self.assertEqual(context["instructions"]["presentation"], "compose_on_demand_not_fixed_script")
        self.assertEqual(context["sources"][0]["sha256"], IMAGE_HASH)
        self.assertFalse(self.store.path.exists())
        self.assertFalse(self.store.path.with_suffix(".lock").exists())

    def test_point_summaries_and_no_cross_point_knowledge(self):
        self.store.put_board_evidence(POINT_ID, EVIDENCE, IMAGE_HASH)
        rows = self.store.list_points()
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0]["prior_ready"])
        self.assertEqual(rows[0]["candidate_fact_count"], 1)
        self.assertFalse(rows[1]["prior_ready"])
        self.assertFalse(self.store.get(OTHER_ID)["reviewed_prior"])
        self.assertFalse(self.store.get(OTHER_ID)["candidates"])

    def test_unknown_points_path_injection_and_wrong_image_rejected(self):
        invalid = ["../" + POINT_ID, "d" * 32, None]
        for point_id in invalid:
            with self.subTest(point_id=point_id), self.assertRaises(ValueError):
                self.store.put_board_evidence(point_id, EVIDENCE, IMAGE_HASH)
        with self.assertRaises(ValueError):
            self.store.put_board_evidence(OTHER_ID, EVIDENCE, IMAGE_HASH)
        with self.assertRaises(ValueError):
            self.store.put_board_evidence(POINT_ID, EVIDENCE, "0" * 64)
        self.assertFalse(self.store.path.exists())

    def test_model_review_flag_cannot_promote_evidence(self):
        evidence = self.store.put_board_evidence(POINT_ID, EVIDENCE, IMAGE_HASH)
        self.assertFalse(evidence["reviewed"])
        context = self.store.get(POINT_ID)
        self.assertEqual(len(context["reviewed_prior"]), 1)
        self.assertEqual(context["candidates"][0]["evidence"], EVIDENCE)
        with self.assertRaises(ValueError):
            self.store.put_board_evidence(POINT_ID, EVIDENCE, IMAGE_HASH, reviewed=1)

    def test_reviewed_prior_and_new_candidate_coexist_versions_preserved(self):
        first = self.store.put_board_evidence(POINT_ID, EVIDENCE, IMAGE_HASH, reviewed=True)
        changed = copy.deepcopy(EVIDENCE)
        changed["regions"][0]["facts"] = ["候选新信息，尚未核对。"]
        second = self.store.put_board_evidence(POINT_ID, changed, IMAGE_HASH)
        context = self.store.get(POINT_ID)
        self.assertEqual(context["reviewed_prior"][1]["evidence"], EVIDENCE)
        self.assertEqual(context["candidates"][0]["evidence"], changed)
        self.assertEqual((first["version"], second["version"]), (1, 2))
        reloaded = KnowledgeStore(self.project).get(POINT_ID)
        self.assertEqual(reloaded, context)

    def test_changed_photo_does_not_silently_reuse_old_board_facts(self):
        self.store.put_board_evidence(POINT_ID, EVIDENCE, IMAGE_HASH, reviewed=True)
        path = self.store.points.image_path(POINT_ID)
        path.write_bytes(b"\xff\xd8\xffnew photograph\xff\xd9")
        context = self.store.get(POINT_ID)
        self.assertEqual(len(context["reviewed_prior"]), 1)
        self.assertTrue(any(s.get("status") == "stale_photo_not_used" for s in context["sources"]))

    def test_evidence_validation_bounds(self):
        invalid = [[], {**EVIDENCE, "command": "move"}, {**EVIDENCE, "regions": []},
                   {**EVIDENCE, "regions": EVIDENCE["regions"] * 6},
                   {**EVIDENCE, "failed_regions": ["x"] * 6},
                   {**EVIDENCE, "failed_regions": ["整图"]},
                   {**EVIDENCE, "failed_regions": ["x", "x"]}]
        for field, value in (("facts", ["x"] * 25), ("facts", ["x" * 161]),
                             ("facts", []), ("uncertainties", ["x"] * 9),
                             ("uncertainties", ["x" * 201]), ("needs_review", 1)):
            invalid.append({"regions": [{**EVIDENCE["regions"][0], field: value}], "failed_regions": []})
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_evidence(value)
        pending = copy.deepcopy(EVIDENCE)
        pending["regions"][0]["uncertainties"] = ["年份模糊。"]
        self.assertTrue(validate_evidence(pending)["regions"][0]["needs_review"])

    def test_document_unreviewed_text_not_used_as_facts(self):
        metadata = self.store.add_document(POINT_ID, "背景.txt", "候选文献信息。".encode())
        self.assertEqual(metadata["status"], "text_available")
        self.assertFalse(metadata["reviewed"])
        self.assertNotIn("text", metadata)
        context = self.store.get(POINT_ID)
        self.assertEqual(len(context["reviewed_prior"]), 1)
        self.assertFalse(context["candidates"][0]["usable_as_facts"])
        self.assertNotIn("chunks", context["documents"][0])
        self.assertEqual(self.store.document_path(POINT_ID, metadata["id"]).read_bytes(), "候选文献信息。".encode())

    def test_reviewed_text_chunks_and_citations(self):
        raw = ("已审核文献。" * 300).encode()
        metadata = self.store.add_document(POINT_ID, "background.MD", raw, reviewed=True)
        context = self.store.get(POINT_ID)
        literature = context["reviewed_prior"][1]
        self.assertEqual(literature["type"], "reviewed_literature")
        self.assertEqual("".join(c["text"] for c in literature["chunks"]), raw.decode())
        self.assertTrue(all(c["source_id"].startswith(metadata["source_id"] + ":chunk:") for c in literature["chunks"]))
        self.assertTrue(all(len(c["text"]) <= 1000 for c in literature["chunks"]))

    def test_pdf_is_attachment_not_read_even_if_reviewed(self):
        raw = b"%PDF-1.7\nplaceholder, not extracted"
        document = self.store.add_document(POINT_ID, "paper.pdf", raw, reviewed=True)
        self.assertEqual(document["status"], "pending_extraction")
        context = self.store.get(POINT_ID)
        self.assertEqual(len(context["reviewed_prior"]), 1)
        self.assertEqual(context["candidates"][0]["status"], "pending_extraction")
        self.assertFalse(context["candidates"][0]["usable_as_facts"])

    def test_document_bounds_types_and_filename_paths(self):
        invalid = [("../x.txt", b"valid"), ("dir\\x.txt", b"valid"), ("x\n.txt", b"valid"),
                   ("script.py", b"print(1)"), ("x.pdf", b"not a PDF"), ("x.txt", b"\xff"),
                   ("x.txt", b""), ("x.txt", b"x" * (MAX_DOCUMENT_BYTES + 1)),
                   ("x.txt", b"x" * 20001), ("x.txt", b"x\x00x"), ("x.txt", "not bytes")]
        for filename, raw in invalid:
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                self.store.add_document(POINT_ID, filename, raw)
        with self.assertRaises(ValueError):
            self.store.add_document(POINT_ID, "x.txt", b"valid", reviewed=1)
        self.assertFalse(self.store.path.exists())

    def test_duplicate_filenames_immutable_ids_and_no_cross_point_download(self):
        first = self.store.add_document(POINT_ID, "same.txt", b"first")
        second = self.store.add_document(POINT_ID, "same.txt", b"second")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.store.document_path(POINT_ID, first["id"]).read_bytes(), b"first")
        with self.assertRaises(ValueError):
            self.store.document_path(OTHER_ID, first["id"])
        with self.assertRaises(ValueError):
            self.store.document_path(POINT_ID, "../../secret")

    def test_document_tampering_rejected_preserved(self):
        document = self.store.add_document(POINT_ID, "x.txt", b"original", reviewed=True)
        path = self.store.document_path(POINT_ID, document["id"])
        path.write_bytes(b"altered")
        with self.assertRaises(RuntimeError):
            self.store.get(POINT_ID)
        self.assertEqual(path.read_bytes(), b"altered")

    def test_corrupt_registry_preserved(self):
        self.store.path.write_bytes(b"broken { original")
        before = self.store.path.read_bytes()
        with self.assertRaises(RuntimeError):
            self.store.add_document(POINT_ID, "x.txt", b"valid")
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertFalse(self.store.document_dir.exists())

    def test_concurrent_cross_instance_writes_no_lost_versions(self):
        def write(i):
            store = KnowledgeStore(self.project)
            evidence = copy.deepcopy(EVIDENCE)
            evidence["regions"][0]["facts"] = ["事实 {}".format(i)]
            return store.put_board_evidence(POINT_ID, evidence, IMAGE_HASH)["version"]
        with ThreadPoolExecutor(max_workers=5) as pool:
            versions = list(pool.map(write, range(10)))
        self.assertEqual(sorted(versions), list(range(1, 11)))
        self.assertEqual(self.store.get(POINT_ID)["board_evidence_versions"], 10)

    def test_atomic_write_failure_keeps_registry_and_removes_new_document_only(self):
        original = self.store.add_document(POINT_ID, "existing.txt", b"original")
        before = self.store.path.read_bytes()
        with patch("guide_knowledge.os.replace", side_effect=OSError("disk failure")), self.assertRaises(OSError):
            self.store.add_document(POINT_ID, "new.txt", b"new")
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(len(list(self.store.document_dir.iterdir())), 1)
        self.assertEqual(self.store.document_path(POINT_ID, original["id"]).read_bytes(), b"original")


if __name__ == "__main__":
    unittest.main()
