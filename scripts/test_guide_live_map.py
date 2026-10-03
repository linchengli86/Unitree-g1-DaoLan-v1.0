#!/usr/bin/env python3
"""Read-only live map tests; no ROS imports or real robot/network required."""

import copy
import math
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

from guide_live_map import LiveMapManager, validate_snapshot, empty_layer
from stream_live_map import (compose_snapshot, project_scan, project_costmap,
                             transform_point, FingerprintCache, LocalizationCheck, MapVersionGuard)

FINGERPRINT = "a" * 64


def ready(now=None):
    now = time.time() if now is None else now
    return compose_snapshot(FINGERPRINT, (True, False, now),
        {"x": 4.0, "y": -1.0, "yaw": 0.7, "stamp": now},
        {"state": "ready", "points": [[5.0, -1.0]], "stamp": now},
        empty_layer(), now)


class ProjectionTests(unittest.TestCase):
    def scan(self, **updates):
        values = {"angle_min": 0.0, "angle_increment": math.pi/2,
                  "range_min": 0.05, "range_max": 10.0, "ranges": [1.0, 2.0]}
        values.update(updates)
        return NS(**values)

    def test_scan_rotation_and_translation(self):
        quaternion = [0., 0., math.sin(math.pi/4), math.cos(math.pi/4)]
        points = project_scan(self.scan(), [3., 4., 1.], quaternion)
        self.assertAlmostEqual(points[0][0], 3.)
        self.assertAlmostEqual(points[0][1], 5.)
        self.assertAlmostEqual(points[1][0], 1.)
        self.assertAlmostEqual(points[1][1], 4.)

    def test_scan_uses_full_roll_not_only_yaw(self):
        points = project_scan(self.scan(), [0., 0., 0.], [1., 0., 0., 0.])
        self.assertAlmostEqual(points[1][1], -2.)

    def test_invalid_and_far_ranges_discarded(self):
        points = project_scan(self.scan(ranges=[float("nan"), float("inf"), -1., 0., .01, 7., 6.]),
                              [0., 0., 0.], [0., 0., 0., 1.])
        self.assertEqual(len(points), 1)

    def test_scan_downsampling_is_bounded(self):
        self.assertEqual(len(project_scan(self.scan(ranges=[1.] * 2000),
            [0., 0., 0.], [0., 0., 0., 1.])), 400)

    def test_scan_rejects_invalid_quaternion(self):
        with self.assertRaises(ValueError):
            project_scan(self.scan(), [0., 0., 0.], [0., 0., 0., 0.])

    def grid(self, data=None):
        return NS(info=NS(width=2, height=2, resolution=1., origin=NS(
            position=NS(x=1., y=2., z=0.), orientation=NS(x=0., y=0., z=1., w=0.))),
            data=data if data is not None else [100, 98, -1, 100])

    def test_costmap_only_lethal_and_origin_yaw(self):
        points = project_costmap(self.grid(), [0., 0., 0.], [0., 0., 0., 1.])
        self.assertEqual(len(points), 2)
        self.assertAlmostEqual(points[0][0], .5)
        self.assertAlmostEqual(points[0][1], 1.5)
        self.assertAlmostEqual(points[1][0], -.5)
        self.assertAlmostEqual(points[1][1], .5)

    def test_costmap_frame_transform_applied(self):
        points = project_costmap(self.grid(), [10., 20., 0.], [0., 0., 0., 1.])
        self.assertAlmostEqual(points[0][0], 10.5)
        self.assertAlmostEqual(points[0][1], 21.5)

    def test_noetic_inflation_99_and_98_not_drawn(self):
        self.assertEqual(project_costmap(self.grid([99, 98, -1, 0]),
                                        [0., 0., 0.], [0., 0., 0., 1.]), [])

    def test_transform_3d_rotation(self):
        point = transform_point([0., 1., 0.], [1., 2., 3.], [1., 0., 0., 0.])
        self.assertEqual(point, [1., 1., 3.])


class SnapshotTests(unittest.TestCase):
    def test_moving_pose_allowed(self):
        first = validate_snapshot(ready(100.), now=100.)
        second = ready(100.2)
        second["pose"].update(x=4.2, yaw=.9)
        self.assertEqual(validate_snapshot(second, now=100.2)["pose"]["x"], 4.2)
        self.assertNotEqual(first["pose"]["x"], second["pose"]["x"])

    def test_unlocalized_never_exposes_origin_or_obstacles(self):
        value = ready(100.)
        value.update(state="unlocalized", localized=False)
        value["pose"] = {"x": 0., "y": 0., "yaw": 0., "stamp": 100.}
        result = validate_snapshot(value, now=100.)
        self.assertIsNone(result["pose"])
        self.assertEqual(result["scan"]["points"], [])

    def test_false_relocalization_suppresses_worker_layers(self):
        source = ready(100.)
        result = compose_snapshot(FINGERPRINT, (False, False, 100.),
            source["pose"], source["scan"], source["costmap"], now=100.)
        self.assertEqual(result["state"], "unlocalized")
        self.assertIsNone(result["pose"])
        self.assertEqual(result["scan"]["points"], [])

    def test_check_and_tf_wall_freshness(self):
        source = ready(100.)
        result = compose_snapshot(FINGERPRINT, (True, False, 97.),
            source["pose"], source["scan"], source["costmap"], now=100.)
        self.assertEqual(result["state"], "stale")
        source["pose"]["stamp"] = 98.
        result = compose_snapshot(FINGERPRINT, (True, False, 100.),
            source["pose"], source["scan"], source["costmap"], now=100.)
        self.assertIsNone(result["pose"])

    def test_simulated_time_is_not_real_pose(self):
        source = ready(100.)
        result = compose_snapshot(FINGERPRINT, (True, True, 100.),
            source["pose"], source["scan"], source["costmap"], now=100.)
        self.assertIsNone(result["pose"])

    def test_parent_independently_rejects_false_ready(self):
        for key, bad in (("localized", False), ("use_sim_time", True),
                         ("localization_checked_at", 96.), ("updated_at", 96.)):
            value = ready(100.)
            value[key] = bad
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_snapshot(value, now=100.)

    def test_parent_rejects_bad_fingerprint_or_points(self):
        for mutate in (lambda data: data.update(map_fingerprint="bad"),
                       lambda data: data["scan"].update(points=[[float("nan"), 1.]]),
                       lambda data: data["scan"].update(points=[[1., 2.]] * 401)):
            value = ready(100.)
            mutate(value)
            with self.assertRaises(ValueError):
                validate_snapshot(value, now=100.)

    def test_expired_sensor_layer_removed(self):
        source = ready(100.)
        source["scan"]["stamp"] = 95.
        result = compose_snapshot(FINGERPRINT, (True, False, 100.),
            source["pose"], source["scan"], source["costmap"], now=100.)
        self.assertEqual(result["scan"]["state"], "stale")
        self.assertEqual(result["scan"]["points"], [])

    def test_parent_expires_scan_independently_of_pose(self):
        source = ready(100.)
        source["scan"]["stamp"] = 98.4
        value = validate_snapshot(source, now=100.)
        self.assertEqual(value["state"], "ready")
        self.assertIsNotNone(value["pose"])
        self.assertEqual(value["scan"]["state"], "stale")


class CacheAndProbeTests(unittest.TestCase):
    def test_map_change_needs_new_false_then_true_check(self):
        guard = MapVersionGuard()
        self.assertEqual(guard.apply(FINGERPRINT, (True, False, 100.), 100.)[0], True)
        new_map = "b" * 64
        self.assertEqual(guard.apply(new_map, (True, False, 100.1), 100.1)[0], False)
        # Old-map True results do not make the new overlays valid again.
        self.assertEqual(guard.apply(new_map, (True, False, 101.), 101.)[0], False)
        # A False result captured before map change is not sufficient.
        guard.apply(new_map, (False, False, 100.), 101.)
        self.assertEqual(guard.apply(new_map, (True, False, 101.1), 101.1)[0], False)
        guard.apply(new_map, (False, False, 101.2), 101.2)
        self.assertEqual(guard.apply(new_map, (True, False, 101.3), 101.3)[0], True)

    def test_fingerprint_cached_until_stat_changed(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "map").mkdir()
            (project / "map/map.yaml").write_text("image: map.pgm\n")
            (project / "map/map.pgm").write_bytes(b"map")
            pcd = project / "G1Nav2D/src/fastlio2/PCD/map.pcd"
            pcd.parent.mkdir(parents=True)
            pcd.write_bytes(b"pcd")
            compute = mock.Mock(side_effect=[FINGERPRINT, "b" * 64])
            cache = FingerprintCache(project, compute)
            self.assertEqual(cache.get(), FINGERPRINT)
            self.assertEqual(cache.get(), FINGERPRINT)
            self.assertEqual(compute.call_count, 1)
            pcd.write_bytes(b"changed")
            self.assertEqual(cache.get(), "b" * 64)
            self.assertEqual(compute.call_count, 2)

    def test_hung_service_single_flight_does_not_block_stream(self):
        release = threading.Event()
        called = threading.Event()
        calls = []
        def probe():
            calls.append(True)
            called.set()
            release.wait(1)
            return True, False
        check = LocalizationCheck(probe)
        check.refresh()
        self.assertTrue(called.wait(.2))
        began = time.monotonic()
        for unused in range(10):
            check.refresh()
            result = compose_snapshot(FINGERPRINT, check.snapshot(), ready()["pose"],
                                      empty_layer(), empty_layer())
            self.assertIsNone(result["pose"])
        self.assertLess(time.monotonic()-began, .1)
        self.assertEqual(len(calls), 1)
        release.set()
        check.thread.join(1)

    def test_slow_success_does_not_refresh_localization(self):
        times = iter([100., 102.])
        check = LocalizationCheck(lambda: (True, False), clock=lambda: next(times))
        check.refresh()
        check.thread.join(.2)
        self.assertEqual(check.snapshot(), (False, True, None))


class ManagerTests(unittest.TestCase):
    def manager(self, idle_seconds=60):
        started = threading.Event()
        calls = []
        def runner(stop, emit, should_stop):
            calls.append(True)
            emit(ready())
            started.set()
            while not should_stop():
                stop.wait(.01)
        manager = LiveMapManager(runner=runner, idle_seconds=idle_seconds)
        self.addCleanup(manager.close)
        return manager, started, calls

    def test_lazy_and_multiple_browsers_share_one_runner(self):
        manager, started, calls = self.manager()
        self.assertEqual(calls, [])
        manager.snapshot()
        self.assertTrue(started.wait(.2))
        for unused in range(20):
            self.assertEqual(manager.snapshot()["state"], "ready")
        self.assertEqual(len(calls), 1)

    def test_snapshot_copy_does_not_mutate_cache(self):
        manager, started, unused = self.manager()
        manager.snapshot()
        self.assertTrue(started.wait(.2))
        value = manager.snapshot()
        value["pose"]["x"] = 99
        self.assertEqual(manager.snapshot()["pose"]["x"], 4.)

    def test_manager_removes_expired_cache(self):
        manager, started, unused = self.manager()
        manager.snapshot()
        self.assertTrue(started.wait(.2))
        manager.raw["pose"]["stamp"] = time.time()-4
        result = manager.snapshot()
        self.assertEqual(result["state"], "stale")
        self.assertIsNone(result["pose"])
        self.assertEqual(result["scan"]["points"], [])

    def test_close_stops_only_owned_runner_and_prevents_restart(self):
        manager, started, calls = self.manager()
        manager.snapshot()
        self.assertTrue(started.wait(.2))
        manager.close()
        self.assertFalse(manager.thread.is_alive())
        self.assertEqual(manager.snapshot()["state"], "offline")
        self.assertEqual(len(calls), 1)

    def test_idle_subscriber_exits(self):
        manager, started, calls = self.manager(idle_seconds=.04)
        manager.snapshot()
        self.assertTrue(started.wait(.2))
        manager.thread.join(.3)
        self.assertFalse(manager.thread.is_alive())
        self.assertEqual(len(calls), 1)

    def test_runner_failure_is_offline_and_backed_off(self):
        runner = mock.Mock(side_effect=RuntimeError("private config path"))
        manager = LiveMapManager(runner=runner)
        self.addCleanup(manager.close)
        manager.snapshot()
        manager.thread.join(.2)
        value = manager.snapshot()
        self.assertEqual(value["state"], "offline")
        self.assertNotIn("private", value["message"])
        self.assertEqual(runner.call_count, 1)

    def test_parent_map_change_guard_survives_worker_cache_reset(self):
        manager = LiveMapManager(runner=mock.Mock())
        self.addCleanup(manager.close)
        manager._emit(ready())
        changed = ready()
        changed["map_fingerprint"] = "b" * 64
        changed["localization_service_status"] = True
        manager._emit(changed)
        self.assertEqual(manager.state["state"], "unlocalized")
        # Clearing the per-child cache, as an idle restart does, must not
        # erase proof that the still-running ROS localization used old map.
        manager.raw = None
        manager._emit(copy.deepcopy(changed))
        self.assertIsNone(manager.state["pose"])
        false_status = copy.deepcopy(changed)
        false_status.update(state="unlocalized", localized=False, localization_service_status=False,
                            localization_checked_at=time.time())
        manager._emit(false_status)
        resumed = ready()
        resumed["map_fingerprint"] = "b" * 64
        resumed["localization_service_status"] = True
        manager._emit(resumed)
        self.assertEqual(manager.state["state"], "ready")

    def test_invalid_snapshot_does_not_terminate_stream(self):
        manager = LiveMapManager(runner=mock.Mock())
        self.addCleanup(manager.close)
        broken = ready()
        broken["pose"]["stamp"] -= 10
        manager._emit(broken)
        self.assertEqual(manager.state["state"], "stale")
        manager._emit(ready())
        self.assertEqual(manager.state["state"], "ready")


if __name__ == "__main__":
    unittest.main()
