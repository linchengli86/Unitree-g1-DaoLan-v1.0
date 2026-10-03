#!/usr/bin/env python3
"""Hardware-free map/initial-pose tests; all fixture writes are temporary."""

import io
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

import capture_guide_pose
from guide_map import GuideMap, MAX_PIXELS, pixel_to_world, world_to_pixel
from guide_relocalization import validate_seed


class GuideMapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        (self.project / "map").mkdir()
        self.pcd = self.project / "G1Nav2D/src/fastlio2/PCD/map.pcd"
        self.pcd.parent.mkdir(parents=True)
        self.pcd.write_bytes(b"test PCD fingerprint input\n")
        self.image = self.project / "map/map.pgm"
        pixels = Image.new("L", (3, 2))
        # PGM top: free, unknown, occupied; bottom: free, free, unknown.
        pixels.putdata([255, 205, 0, 255, 206, 90])
        pixels.save(str(self.image))
        self.write_yaml()
        self.maps = GuideMap(self.project)

    def tearDown(self):
        self.temp.cleanup()

    def write_yaml(self, **changes):
        values = {"image": "map.pgm", "resolution": 0.5,
                  "origin": "[10.0, -3.0, 0.0]", "negate": 0,
                  "occupied_thresh": 0.65, "free_thresh": 0.196}
        values.update(changes)
        (self.project / "map/map.yaml").write_text(
            "\n".join("%s: %s" % (key, value) for key, value in values.items()) + "\n", encoding="utf-8")

    def payload(self, column=0, row=0, yaw=0.0):
        metadata = self.maps.metadata()
        return {**pixel_to_world(column, row, metadata), "yaw": yaw,
                "map_fingerprint": metadata["map_fingerprint"]}

    def test_metadata_and_exact_existing_fingerprint(self):
        metadata = self.maps.metadata()
        self.assertEqual(metadata["frame_id"], "map")
        self.assertEqual((metadata["width"], metadata["height"]), (3, 2))
        self.assertEqual(metadata["origin"], {"x": 10.0, "y": -3.0, "yaw": 0.0})
        self.assertEqual(metadata["image_url"], "/api/map/image")
        with mock.patch.object(capture_guide_pose, "PROJECT", self.project):
            self.assertEqual(metadata["map_fingerprint"], capture_guide_pose.map_fingerprint())

    def test_png_is_unflipped_and_lossless(self):
        data = self.maps.image_png()
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(list(image.getdata()), [255, 205, 0, 255, 206, 90])

    def test_y_flip_and_world_centers(self):
        metadata = self.maps.metadata()
        self.assertEqual(pixel_to_world(0, 0, metadata), {"x": 10.25, "y": -2.25})
        self.assertEqual(pixel_to_world(0, 1, metadata), {"x": 10.25, "y": -2.75})
        self.assertEqual(world_to_pixel(10.25, -2.25, metadata), (0, 0))
        self.assertEqual(world_to_pixel(10.25, -2.75, metadata), (0, 1))

    def test_rotated_origin_round_trip(self):
        self.write_yaml(origin="[10.0, -3.0, 1.5707963267948966]")
        metadata = self.maps.metadata()
        position = pixel_to_world(0, 0, metadata)
        self.assertAlmostEqual(position["x"], 9.25)
        self.assertAlmostEqual(position["y"], -2.75)
        for column in range(3):
            for row in range(2):
                position = pixel_to_world(column, row, metadata)
                self.assertEqual(world_to_pixel(position["x"], position["y"], metadata), (column, row))
        seed = self.maps.validate_manual_seed(self.payload())
        self.assertAlmostEqual(seed["pose"]["x"], 9.25)

    def test_free_seed_contract_and_normalized_yaw(self):
        payload = self.payload(yaw=4 * math.pi + 0.25)
        seed = self.maps.validate_manual_seed(payload)
        self.assertEqual(seed["name"], "地图点选当前位置")
        self.assertEqual(seed["pose"]["z"], 0.0)
        self.assertAlmostEqual(seed["pose"]["yaw"], 0.25)
        self.assertEqual(seed["localization_mode"], "coarse_1m")
        self.assertEqual(seed["radius"], 1.0)
        self.assertAlmostEqual(seed["yaw_uncertainty"], math.pi / 4)
        self.assertEqual(validate_seed(seed), seed)

    def test_occupied_and_unknown_rejected(self):
        with self.assertRaisesRegex(ValueError, "障碍"):
            self.maps.validate_manual_seed(self.payload(2, 0))
        for column, row in [(1, 0), (2, 1)]:
            with self.assertRaisesRegex(ValueError, "未知"):
                self.maps.validate_manual_seed(self.payload(column, row))
        self.maps.validate_manual_seed(self.payload(1, 1))  # 206: just below free threshold.

    def test_threshold_equality_is_unknown(self):
        # 205 occupies exactly 50/255. map_server comparisons are strict.
        self.write_yaml(free_thresh=50 / 255.0)
        with self.assertRaisesRegex(ValueError, "未知"):
            self.maps.validate_manual_seed(self.payload(1, 0))
        self.write_yaml(occupied_thresh=1.0)
        with self.assertRaisesRegex(ValueError, "未知"):
            self.maps.validate_manual_seed(self.payload(2, 0))

    def test_negated_map_thresholds(self):
        self.write_yaml(negate=1)
        self.maps.validate_manual_seed(self.payload(2, 0))
        with self.assertRaisesRegex(ValueError, "障碍"):
            self.maps.validate_manual_seed(self.payload(0, 0))

    def test_bounds_are_half_open(self):
        metadata = self.maps.metadata()
        for x, y in [(9.999, -2.5), (11.5, -2.5), (10.5, -3.001), (10.5, -2.0)]:
            with self.assertRaisesRegex(ValueError, "范围"):
                world_to_pixel(x, y, metadata)
        self.assertEqual(world_to_pixel(10.0, -3.0, metadata), (0, 1))

    def test_manual_payload_is_strict_and_finite(self):
        valid = self.payload()
        variants = [{**valid, "topic": "move"}, {key: value for key, value in valid.items() if key != "yaw"},
                    {**valid, "x": True}, {**valid, "y": float("nan")},
                    {**valid, "yaw": float("inf")}, {**valid, "x": "10.25"},
                    {**valid, "x": 10 ** 1000}, {**valid, "map_fingerprint": "A" * 64}]
        for payload in variants:
            with self.subTest(payload_type=str(list(payload))):
                with self.assertRaises(ValueError):
                    self.maps.validate_manual_seed(payload)

    def test_stale_map_seed_rejected(self):
        payload = self.payload()
        self.pcd.write_bytes(b"different PCD input\n")
        with self.assertRaisesRegex(ValueError, "地图已变化"):
            self.maps.validate_manual_seed(payload)
        self.assertNotEqual(self.maps.metadata()["map_fingerprint"], payload["map_fingerprint"])

    def test_cache_invalidation_and_copy_isolation(self):
        old = self.maps.metadata()
        old["origin"]["x"] = 500
        self.assertEqual(self.maps.metadata()["origin"]["x"], 10.0)
        with mock.patch.object(self.maps, "_run", wraps=self.maps._run) as run:
            self.maps.metadata()
            self.maps.image_png()
            run.assert_not_called()
            self.write_yaml(resolution=0.25)
            self.assertEqual(self.maps.metadata()["resolution"], 0.25)
            self.assertEqual(run.call_count, 1)

    def test_image_symlink_target_change_invalidates_cache(self):
        first = self.project / "map/first.pgm"
        second = self.project / "map/second.pgm"
        self.image.replace(first)
        Image.new("L", (3, 2), 255).save(str(second))
        self.image.symlink_to(first.name)
        old = self.maps.metadata()["map_fingerprint"]
        self.image.unlink()
        self.image.symlink_to(second.name)
        self.assertNotEqual(self.maps.metadata()["map_fingerprint"], old)

    def test_external_image_and_pcd_paths_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / "external.pgm"
            Image.new("L", (2, 2), 255).save(str(external))
            self.write_yaml(image=str(external))
            with self.assertRaisesRegex(ValueError, "超出"):
                self.maps.metadata()
            self.write_yaml()
            external_pcd = Path(outside) / "external.pcd"
            external_pcd.write_bytes(b"outside")
            self.pcd.unlink()
            self.pcd.symlink_to(external_pcd)
            with self.assertRaisesRegex(ValueError, "超出"):
                self.maps.metadata()

    def test_invalid_yaml_geometry_and_thresholds_rejected(self):
        for changes in [{"origin": "[0, 0]"}, {"origin": "[0, .nan, 0]"},
                        {"origin": "[true, 0, 0]"}, {"resolution": 0},
                        {"resolution": ".inf"}, {"negate": 2}, {"negate": "true"},
                        {"free_thresh": 0.8, "occupied_thresh": 0.65}]:
            with self.subTest(changes=changes):
                self.write_yaml(**changes)
                with self.assertRaises(ValueError):
                    self.maps.metadata()

    def test_bad_pgm_and_oversized_dimensions_rejected(self):
        self.image.write_bytes(b"not a PGM")
        with self.assertRaisesRegex(ValueError, "PGM"):
            self.maps.metadata()
        self.image.write_bytes(("P5\n%d 1\n255\n" % (MAX_PIXELS + 1)).encode("ascii") + b"\x00")
        with self.assertRaisesRegex(ValueError, "尺寸"):
            self.maps.metadata()

    def test_png_disguised_as_pgm_is_rejected(self):
        Image.new("L", (3, 2), 255).save(str(self.image), format="PNG")
        with self.assertRaisesRegex(ValueError, "PGM"):
            self.maps.metadata()

    def test_manual_seed_never_writes_assets(self):
        paths = [self.project / "map/map.yaml", self.image, self.pcd]
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
        self.maps.metadata()
        self.maps.image_png()
        self.maps.validate_manual_seed(self.payload())
        self.assertEqual(before, {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths})
        self.assertFalse((self.project / "config").exists())


if __name__ == "__main__":
    unittest.main()
