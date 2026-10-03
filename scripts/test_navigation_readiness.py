import math
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from navigation_readiness import navigation_tf_limit, validate_navigation_odom, validate_tf_age
from navigate_to_point_safe import RosBackend


def odom(stamp=99.9, frame="local", child="base_link", vx=.2):
    vector = lambda x=0.: SimpleNamespace(x=x, y=0., z=0.)
    return SimpleNamespace(header=SimpleNamespace(frame_id=frame, stamp=SimpleNamespace(to_sec=lambda: stamp)),
        child_frame_id=child, twist=SimpleNamespace(twist=SimpleNamespace(linear=vector(vx), angular=vector())),
        pose=SimpleNamespace(pose=SimpleNamespace(position=vector(),
            orientation=SimpleNamespace(x=0., y=0., z=0., w=1.))))


class NavigationReadinessTests(unittest.TestCase):
    def test_limit_matches_both_costmaps_without_looser_fallback(self):
        self.assertEqual(navigation_tf_limit(.5, .5), .5)
        self.assertEqual(navigation_tf_limit(.5, .2), .2)
        self.assertEqual(navigation_tf_limit(2., 2.), 1.5)
        for bad in (None, True, "0.5", math.nan, math.inf, 0., -.1):
            for pair in ((bad, .5), (.5, bad)):
                with self.assertRaises(RuntimeError):
                    navigation_tf_limit(*pair)

    def test_current_and_boundary_ages(self):
        for age in (-.1, 0., .1, .5):
            validate_tf_age(age, .5)
        for age in (-.101, .50001, .6, 1.4, math.nan, math.inf, True, None):
            with self.assertRaises(RuntimeError):
                validate_tf_age(age, .5)

    def test_invalid_maximum_rejected(self):
        for value in (None, True, 0, -.1, math.nan, math.inf, 1.6):
            with self.assertRaises(RuntimeError):
                validate_tf_age(.1, value)

    def test_actual_base_link_velocity_feedback_required(self):
        validate_navigation_odom(odom(), 100, .5)
        # Real stationary velocity is valid; zero is not itself a fault.
        validate_navigation_odom(odom(vx=0.), 100, .5)
        for msg in (None, object(), odom(child="body"), odom(frame="map"),
                    odom(stamp=99.4), odom(stamp=100.2), odom(vx=math.nan),
                    odom(stamp=0.), odom(stamp=True), odom(stamp=math.nan)):
            with self.assertRaises(RuntimeError):
                validate_navigation_odom(msg, 100, .5)

    def backend(self, stamps):
        backend = object.__new__(RosBackend)
        backend.tf_max_age = .5
        backend.navigation_feedback_required = True
        backend.odom_lock = threading.Lock()
        backend.navigation_odom = odom()
        backend.listener = Mock()
        backend.listener.getLatestCommonTime.side_effect = [
            SimpleNamespace(to_sec=lambda stamp=stamp: stamp) for stamp in stamps]
        backend.listener.lookupTransform.return_value = ([0., 0., 0.], [0., 0., 0., 1.])
        backend.tf = Mock()
        backend.tf.transformations.euler_from_quaternion.return_value = [0., 0., 0.]
        return backend

    def test_backend_freshness_rejects_06_even_if_old_capture_policy_would_accept(self):
        backend = self.backend([99.4])
        with patch("navigate_to_point_safe.time.time", return_value=100):
            with self.assertRaisesRegex(RuntimeError, "上限 0.500"):
                backend._navigation_freshness()

    def test_backend_rejects_missing_or_stale_velocity_even_with_fresh_tf(self):
        for message in (None, odom(stamp=99.4), odom(child="body")):
            backend = self.backend([99.9])
            backend.navigation_odom = message
            with patch("navigate_to_point_safe.time.time", return_value=100):
                with self.assertRaises(RuntimeError):
                    backend._navigation_freshness()

    def test_owned_plan_wait_rejects_stale_tf_without_enabling_motion(self):
        backend = self.backend([99.4])
        backend.cancel = threading.Event()
        backend.ensure_owned = Mock()
        backend.goal_state = Mock(return_value=1)
        backend.set_motion = Mock()
        with patch("navigate_to_point_safe.time.time", return_value=100):
            with self.assertRaisesRegex(RuntimeError, "TF 已过期"):
                backend.wait_owned_plan(__import__("time").monotonic()+2)
        backend.set_motion.assert_not_called()

    def test_arm_rechecks_freshness_after_localization_rpc(self):
        backend = self.backend([99.9, 99.9])
        backend.cancel = threading.Event()
        backend.enable = Mock(return_value=SimpleNamespace(success=True))
        now = [100.]
        backend._check_localized = Mock(side_effect=lambda: now.__setitem__(0, 100.6))
        with patch("navigate_to_point_safe.time.time", side_effect=lambda: now[0]):
            with self.assertRaisesRegex(RuntimeError, "TF 已过期"):
                backend.set_motion(True)
        backend.enable.assert_not_called()

    def test_disable_is_not_blocked_by_stale_tf_or_lost_localization(self):
        backend = self.backend([98.])
        backend.cancel = threading.Event()
        backend.cancel.set()
        backend.enable = Mock(return_value=SimpleNamespace(success=True))
        backend._check_localized = Mock(side_effect=RuntimeError("lost"))
        backend.set_motion(False)
        backend.enable.assert_called_once_with(data=False)
        backend.listener.getLatestCommonTime.assert_not_called()
        backend._check_localized.assert_not_called()


if __name__ == "__main__":
    unittest.main()
