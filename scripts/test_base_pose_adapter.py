#!/usr/bin/env python3
"""Compile actual standard-base pose math, without ROS or robot access."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class BasePoseAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("g++")
        if not compiler or not Path("/usr/include/eigen3/Eigen/Geometry").exists():
            raise unittest.SkipTest("g++ and Eigen3 are required for the pure C++ tests")
        cls.directory = tempfile.TemporaryDirectory(prefix="daolan-base-adapter-")
        cls.binary = Path(cls.directory.name) / "pose_test"
        package = Path(__file__).resolve().parents[1] / "G1Nav2D/src/fastlio2"
        subprocess.run([compiler, "-std=c++14", "-O1", "-I/usr/include/eigen3",
                        "-I" + str(package / "include"),
                        str(package / "test/base_pose_adapter_test.cpp"), "-o", str(cls.binary)],
                       check=True, capture_output=True, text=True, timeout=60)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "directory"):
            cls.directory.cleanup()

    def run_case(self, name):
        subprocess.run([str(self.binary), name], check=True, capture_output=True, timeout=5)

    def test_identity(self): self.run_case("identity")
    def test_actual_upside_down_body_tf(self): self.run_case("upside_down_body")
    def test_translated_extrinsic(self): self.run_case("translated_extrinsic")
    def test_standard_rpy(self): self.run_case("standard_rpy")
    def test_yaw_wrap(self): self.run_case("wrap")
    def test_nonfinite_rejected(self): self.run_case("nonfinite")
    def test_nonrigid_rejected(self): self.run_case("nonrigid")


if __name__ == "__main__":
    unittest.main()
