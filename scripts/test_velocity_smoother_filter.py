#!/usr/bin/env python3
"""Offline C++ behavioral regression and checks binding that helper to ROS."""

from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

import yaml


PACKAGE = Path(__file__).resolve().parents[1] / "G1Nav2D/src/velocity_smoother_ema"
SOURCE = PACKAGE / "src/velocity_smoother_ema.cpp"


class SmootherBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("g++")
        if compiler is None:
            raise unittest.SkipTest("g++ is required for offline smoother tests")
        cls.temp = tempfile.TemporaryDirectory(prefix="daolan-smoother-test-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.executable = Path(cls.temp.name) / "command_filter_test"
        subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror", "-pedantic",
                        "-I", str(PACKAGE / "include"),
                        str(PACKAGE / "test/command_filter_test.cpp"),
                        "-o", str(cls.executable)], check=True, timeout=30, capture_output=True)

    def scenario(self, name):
        subprocess.run([str(self.executable), name], check=True, timeout=5, capture_output=True)

    def test_five_hz_raw_reaches_requested_velocity_without_artificial_decay(self):
        self.scenario("five_hz")

    def test_published_output_does_not_replace_raw_command(self):
        self.scenario("raw_separation")

    def test_acceleration_limits_apply_to_actual_elapsed_time(self):
        self.scenario("acceleration")

    def test_explicit_zero_stops_without_ema_or_deceleration_delay(self):
        self.scenario("immediate_stop")

    def test_exact_watchdog_boundary_drops_command_until_fresh_input(self):
        self.scenario("timeout_boundary")

    def test_nonfinite_input_fails_zero_and_cannot_reuse_old_raw(self):
        self.scenario("invalid_command")

    def test_backward_nonfinite_and_negative_clock_fail_zero(self):
        self.scenario("bad_clock")

    def test_timer_stall_cannot_be_hidden_by_new_raw(self):
        self.scenario("callback_gap")

    def test_configuration_cannot_extend_watchdog_or_acceleration_limits(self):
        self.scenario("configuration")


class SmootherRosBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text(encoding="utf-8")

    def test_subscriber_is_latest_only_and_publisher_does_not_latch_old_motion(self):
        self.assertRegex(self.source, r"subscribe\(raw_cmd_topic,\s*1,")
        self.assertRegex(self.source, r"advertise<geometry_msgs::Twist>\(cmd_topic,\s*1,\s*false\)")

    def test_timer_and_freshness_use_steady_clock(self):
        self.assertIn("std::chrono::steady_clock::now()", self.source)
        self.assertIn("createSteadyTimer(", self.source)
        self.assertIn("filter_.update(steady_seconds())", self.source)
        self.assertNotIn("ros::Time::now()", self.source)
        self.assertNotIn("stop_counter", self.source)

    def test_every_twist_component_must_be_finite(self):
        callback = self.source.split("void VelocitySmootherEma::twist_callback", 1)[1].split(
            "void VelocitySmootherEma::update", 1)[0]
        for field in ("linear.x", "linear.y", "linear.z", "angular.x", "angular.y", "angular.z"):
            self.assertIn("!std::isfinite(msg->" + field + ")", callback)
        self.assertIn("filter_.reject(now)", callback)
        self.assertIn("velocity_pub_.publish(geometry_msgs::Twist())", callback)

    def test_output_reconstructed_with_only_planar_fields(self):
        update = self.source.split("void VelocitySmootherEma::update", 1)[1].split("int main", 1)[0]
        self.assertIn("geometry_msgs::Twist output;", update)
        assignments = re.findall(r"output\.(\w+\.\w+)\s*=", update)
        self.assertEqual(assignments, ["linear.x", "linear.y", "angular.z"])
        self.assertIn("velocity_pub_.publish(output)", update)

    def test_existing_topics_alpha_rate_and_safety_profile_are_preserved(self):
        parameters = yaml.safe_load((PACKAGE / "param/smoother.yaml").read_text(encoding="utf-8"))
        for key, wanted in {"raw_cmd_topic": "cmd_vel", "cmd_topic": "cmd_vel_smooth",
                            "alpha_v": 0.2, "alpha_w": 0.2, "cmd_rate": 30.0,
                            "acc_lim_x": 0.3, "acc_lim_theta": 0.5}.items():
            self.assertEqual(parameters[key], wanted)
        self.assertEqual(parameters["raw_timeout"], 1.0 / 3.0)
        for name in ("raw_timeout", "acc_lim_x", "acc_lim_theta"):
            self.assertIn('"/' + name + '"', self.source)
        self.assertIn("add_compile_options(-std=c++11)", (PACKAGE / "CMakeLists.txt").read_text())


if __name__ == "__main__":
    unittest.main()
