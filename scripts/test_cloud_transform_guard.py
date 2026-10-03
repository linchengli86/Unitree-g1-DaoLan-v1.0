#!/usr/bin/env python3
"""Source-level guards; the deployed ROS/PCL target must also be compiled."""

from pathlib import Path
import re
import unittest


SOURCE = (Path(__file__).resolve().parents[1]
          / "G1Nav2D/src/tool/src/body2any_pointcloud.cpp")


class CloudTransformGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text(encoding="utf-8")

    def require_drop_guard(self, function):
        pattern = (r"if\s*\(\s*!" + re.escape(function)
                   + r"\([\s\S]*?\)\s*\)\s*\{([^{}]*)\}")
        match = re.search(pattern, self.source)
        self.assertIsNotNone(match, "failed transform must short-circuit")
        self.assertIn("ROS_WARN_THROTTLE(1.0,", match.group(1))
        self.assertIn("return;", match.group(1))
        self.assertLess(match.end(), self.source.index("pub_.publish(cloud_out);"))
        return match

    def test_unavailable_transform_is_not_published(self):
        self.require_drop_guard("listener_.waitForTransform")

    def test_failed_pcl_transform_is_not_published(self):
        self.require_drop_guard("pcl_ros::transformPointCloud")

    def test_conversion_waits_before_transforming(self):
        waiting = self.require_drop_guard("listener_.waitForTransform")
        conversion = self.require_drop_guard("pcl_ros::transformPointCloud")
        self.assertLess(waiting.end(), conversion.start())

    def test_measurement_timestamp_and_target_frame_are_preserved(self):
        self.assertRegex(self.source,
                         r"waitForTransform\([^;]*cloud_msg->header\.stamp")
        self.assertIn("cloud_out.header.stamp = cloud_msg->header.stamp;", self.source)
        self.assertIn("cloud_out.header.frame_id = target_frame_;", self.source)
        self.assertNotIn("ros::Time::now()", self.source)
        conversion = self.require_drop_guard("pcl_ros::transformPointCloud")
        self.assertLess(conversion.end(),
                        self.source.index("cloud_out.header.stamp = cloud_msg->header.stamp;"))

    def test_exception_warning_is_rate_limited(self):
        self.assertRegex(self.source,
                         r"catch\s*\(tf::TransformException&\s+ex\)\s*\{\s*"
                         r"ROS_WARN_THROTTLE\(1\.0,")


if __name__ == "__main__":
    unittest.main()
