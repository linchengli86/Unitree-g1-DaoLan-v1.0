#!/usr/bin/env python3
"""ROS-free functional adapter tests plus deployment-source contracts."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[1]
FASTLIO = PROJECT / "G1Nav2D/src/fastlio2"


class NavigationOdometryTests(unittest.TestCase):
    def test_cpp_measured_state_frame_and_failure_cases(self):
        compiler = shutil.which("g++")
        eigen = Path("/usr/include/eigen3")
        if not compiler or not (eigen / "Eigen/Core").is_file():
            self.skipTest("g++ and Eigen headers required for functional C++ test")
        with tempfile.TemporaryDirectory(prefix="daolan-odom-test-") as directory:
            binary = str(Path(directory) / "test_navigation_odometry")
            built = subprocess.run(
                [compiler, "-std=c++14", "-Wall", "-Wextra", "-Werror", "-O1",
                 "-I" + str(eigen), "-I" + str(FASTLIO / "include"),
                 str(FASTLIO / "test/test_navigation_odometry.cpp"), "-o", binary],
                text=True, capture_output=True, timeout=60,
            )
            self.assertEqual(built.returncode, 0, built.stderr)
            ran = subprocess.run([binary], text=True, capture_output=True, timeout=5)
            self.assertEqual(ran.returncode, 0, ran.stderr)
            self.assertIn("functional checks passed", ran.stdout)

    def test_legacy_slam_odom_and_tf_contract_remain(self):
        source = (FASTLIO / "src/localizer_node.cpp").read_text(encoding="utf-8")
        self.assertIn('advertise<nav_msgs::Odometry>("slam_odom", 1000)', source)
        self.assertIn("publishOdom(eigen2Odometry(current_state_.rot.toRotationMatrix(),", source)
        self.assertIn("body_frame_,\n                                       current_time_));", source)
        self.assertEqual(source.count("br_.sendTransform(eigen2Transform("), 2)
        self.assertIn("current_time_ = measure_group_.lidar_time_end;", source)

    def test_new_topic_preserves_stamp_and_publishes_only_valid_measured_state(self):
        source = (FASTLIO / "src/localizer_node.cpp").read_text(encoding="utf-8")
        self.assertIn('advertise<nav_msgs::Odometry>("navigation_odom", 1)', source)
        self.assertIn("input.local_body_velocity = current_state_.vel;", source)
        self.assertIn("input.body_gyro = imu.gyro;", source)
        self.assertIn("input.body_gyro_bias = current_state_.bg;", source)
        self.assertIn('local_frame_, "base_link", current_time_', source)
        self.assertIn("if (!navigation_odometry_.update(input, state, reason))", source)
        for component in ("linear.x", "linear.y", "linear.z", "angular.x", "angular.y", "angular.z"):
            self.assertIn("odom.twist.twist." + component + " = state.", source)
        method = source[source.index("void publishNavigationOdom()"):source.index("void systemReset()")]
        self.assertNotIn("cmd_vel", method)
        self.assertNotIn("ros::Duration", method)  # No blocking transform lookup in LIO loop.
        self.assertIn("!transform.header.stamp.isZero()", method)
        self.assertIn("navigation_odometry_.reset();", source[source.index("void systemReset()") :])

    def test_only_teb_feedback_topic_changes_not_speed_profile(self):
        config = (PROJECT / "G1Nav2D/src/movebase/param/teb_local_planner_params.yaml").read_text(encoding="utf-8")
        self.assertIn("odom_topic: /navigation_odom", config)
        for key, value in (("max_vel_x", "0.60"), ("max_vel_theta", "0.70"),
                           ("acc_lim_x", "0.30"), ("acc_lim_theta", "0.50")):
            self.assertIn(key + ": " + value, config)


if __name__ == "__main__":
    unittest.main()
