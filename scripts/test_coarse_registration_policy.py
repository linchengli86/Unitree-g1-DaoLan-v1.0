#!/usr/bin/env python3
"""Compile/run the bounded coarse-position policy without ROS, PCL or a robot."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
POLICY_INCLUDE = PROJECT / "G1Nav2D/src/fastlio2/include"

CPP = r'''
#include "localizer/coarse_registration_policy.h"
#include <cassert>
#include <cstdlib>
#include <limits>
#include <set>
#include <tuple>
using namespace fastlio;
using namespace fastlio::coarse_policy;
CoarseReport good(double score = 0.01, std::size_t inliers = 120) {
    CoarseReport r; r.point_count = 200; r.inlier_count = inliers;
    r.inlier_ratio = static_cast<double>(inliers) / r.point_count;
    r.score = score; return r;
}
int main(int argc, char **argv) {
    assert(argc == 2);
    const int test = std::atoi(argv[1]);
    const double nan = std::numeric_limits<double>::quiet_NaN();
    const double inf = std::numeric_limits<double>::infinity();
    if (test == 0) {
        const auto list = candidates(); assert(list.size() == 65);
        std::set<std::pair<double, double>> positions;
        std::set<std::tuple<double,double,double>> unique;
        for (const auto &c : list) {
            assert(insideDisk(c.x, c.y)); assert(std::abs(c.yaw) <= kYawLimit);
            positions.insert({c.x,c.y}); unique.insert({c.x,c.y,c.yaw});
        }
        assert(positions.size() == 13); assert(unique.size() == 65);
        assert(positions.count({1.0,0.0})); assert(positions.count({0.0,-1.0}));
        assert(!positions.count({1.0,1.0}));
    } else if (test == 1) {
        assert(insideDisk(0.8,0.6)); assert(!insideDisk(0.8,0.61));
        assert(!insideDisk(1.000002,0.0)); assert(!insideDisk(nan,0));
        assert(!insideDisk(0,inf));
    } else if (test == 2) {
        assert(boundedPose(0.8,0.6,0.5,0.35,-0.35,kYawLimit));
        assert(!boundedPose(0,0,0.50001,0,0,0));
        assert(!boundedPose(0,0,0,0.35001,0,0));
        assert(!boundedPose(0,0,0,0,-0.35001,0));
        assert(!boundedPose(0,0,0,0,0,kYawLimit+0.00001));
        assert(!boundedPose(0,0,nan,0,0,0));
    } else if (test == 3) {
        assert(std::abs(wrappedAngle(3.13 - -3.13)) < 0.03);
        assert(!independent(0.1,0.1,3.13 - -3.13));
        assert(independent(0.401,0,0)); assert(independent(0,0,0.251));
        assert(!independent(0.4,0,0.25));
    } else if (test == 4) {
        assert(quality(good())); assert(quality(good(0.18*0.18,110)));
        CoarseReport r = good(); r.point_count = 80; r.inlier_count = 80;
        r.inlier_ratio = 1; assert(quality(r));
        r.point_count = 79; r.inlier_count = 79; assert(!quality(r));
    } else if (test == 5) {
        CoarseReport r = good(); r.inlier_count = 79;
        r.inlier_ratio = 79.0/200; assert(!quality(r));
        assert(!quality(good(0.01,109))); assert(!quality(good(0.032401)));
    } else if (test == 6) {
        assert(!quality(good(nan))); assert(!quality(good(inf)));
        assert(!quality(good(-1.0))); CoarseReport r = good();
        r.inlier_ratio = nan; assert(!quality(r));
        r = good(); r.inlier_ratio = 0.99; assert(!quality(r));
        r = good(); r.inlier_count = 201; r.inlier_ratio = 1.005; assert(!quality(r));
        r = good(); r.point_count = 2001; r.inlier_count = 2001;
        r.inlier_ratio = 1.0; assert(!quality(r));
    } else if (test == 7) {
        assert(!clearlyBetter(good(0.01),good(0.01)));
        assert(!clearlyBetter(good(0.01),good(0.014)));
        assert(clearlyBetter(good(0.01),good(0.02)));
        assert(!clearlyBetter(good(0.025),good(0.03)));
    } else if (test == 8) {
        // Lower error cannot win by discarding substantially more of the scan.
        assert(!clearlyBetter(good(0.001,110),good(0.02,140)));
        assert(clearlyBetter(good(0.001,140),good(0.02,140)));
        assert(!clearlyBetter(good(0.001),good(0.04)));
    } else if (test == 9) {
        CoarseReport r; assert(!r.status); assert(r.score == -1);
        assert(r.best_second_ratio == -1); assert(!quality(r));
        assert(kSourceCap == 2000); assert(kMaximumRefinements == 4);
        assert(kDeadlineSeconds == 20.0); assert(kMaximumRefinementDrift == 0.20);
    } else return 2;
    return 0;
}
'''


class CoarseRegistrationPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("g++")
        if not compiler:
            raise unittest.SkipTest("g++ unavailable; pure C++ policy requires a compiler")
        cls.temp = tempfile.TemporaryDirectory(prefix="daolan-coarse-policy-")
        cls.addClassCleanup(cls.temp.cleanup)
        source = Path(cls.temp.name) / "policy_test.cpp"
        cls.binary = Path(cls.temp.name) / "policy_test"
        source.write_text(CPP, encoding="utf-8")
        result = subprocess.run(
            [compiler, "-std=c++14", "-Wall", "-Wextra", "-pedantic", "-Werror",
             "-I", str(POLICY_INCLUDE), str(source), "-o", str(cls.binary)],
            capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise AssertionError(result.stderr)

    def run_case(self, number):
        result = subprocess.run([str(self.binary), str(number)],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_exact_65_candidates_in_circular_not_square_envelope(self): self.run_case(0)
    def test_radius_and_nonfinite_positions_fail_closed(self): self.run_case(1)
    def test_vertical_tilt_and_heading_bounds(self): self.run_case(2)
    def test_wraparound_and_same_solution_deduplication(self): self.run_case(3)
    def test_quality_boundary_is_accepted(self): self.run_case(4)
    def test_sparse_low_overlap_and_high_residual_fail(self): self.run_case(5)
    def test_invalid_statistics_fail_closed(self): self.run_case(6)
    def test_similar_distinct_solutions_are_ambiguous(self): self.run_case(7)
    def test_score_cannot_win_by_discarding_more_points(self): self.run_case(8)
    def test_defaults_and_compute_caps(self): self.run_case(9)


if __name__ == "__main__":
    unittest.main()
