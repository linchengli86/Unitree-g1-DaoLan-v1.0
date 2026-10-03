#pragma once

// Pure, hardware-independent acceptance policy for an operator's approximate
// map position.  Deliberately separate from the legacy 20 cm seed mode.
#include <cmath>
#include <cstddef>
#include <limits>
#include <string>
#include <vector>

namespace fastlio
{
    struct CoarseReport
    {
        bool status = false;
        std::string reason = "not_requested";
        double score = -1.0;  // bounded-nearest-neighbour MSE, m^2; -1 = unavailable
        double inlier_ratio = 0.0;
        std::size_t point_count = 0;
        std::size_t inlier_count = 0;
        double best_second_ratio = -1.0;  // -1 = no distinct second solution
        int candidate_count = 0;
        int refined_count = 0;
        int distinct_solution_count = 0;
        double elapsed_seconds = 0.0;
    };

    namespace coarse_policy
    {
        constexpr double kRadius = 1.0;
        constexpr double kGridSpacing = 0.5;
        constexpr double kYawStep = 0.3926990816987241548;  // 22.5 deg
        constexpr double kYawLimit = 2.0 * kYawStep;
        constexpr double kDeadlineSeconds = 20.0;
        constexpr std::size_t kSourceCap = 2000;
        constexpr std::size_t kMinimumPoints = 80;
        constexpr double kCorrespondenceDistance = 0.25;
        constexpr double kMinimumOverlap = 0.55;
        constexpr double kMaximumRmse = 0.18;
        constexpr double kMaximumZDrift = 0.5;
        constexpr double kMaximumTilt = 0.35;
        constexpr double kMaximumRefinementDrift = 0.20;
        constexpr double kDistinctXY = 0.4;
        constexpr double kDistinctYaw = 0.25;
        constexpr int kMaximumRefinements = 4;

        struct CandidateOffset { double x, y, yaw; };

        inline double wrappedAngle(double radians)
        {
            return std::atan2(std::sin(radians), std::cos(radians));
        }

        inline bool insideDisk(double x, double y)
        {
            return std::isfinite(x) && std::isfinite(y) &&
                   std::hypot(x, y) <= kRadius + 1e-6;
        }

        inline std::vector<CandidateOffset> candidates()
        {
            std::vector<CandidateOffset> result;
            for (int x = -2; x <= 2; ++x)
                for (int y = -2; y <= 2; ++y)
                {
                    const double dx = x * kGridSpacing, dy = y * kGridSpacing;
                    if (!insideDisk(dx, dy)) continue;
                    for (int yaw = -2; yaw <= 2; ++yaw)
                        result.push_back({dx, dy, yaw * kYawStep});
                }
            return result;
        }

        inline bool boundedPose(double dx, double dy, double dz,
                                double roll, double pitch, double yaw)
        {
            return insideDisk(dx, dy) && std::isfinite(dz) &&
                   std::isfinite(roll) && std::isfinite(pitch) && std::isfinite(yaw) &&
                   std::abs(dz) <= kMaximumZDrift &&
                   std::abs(roll) <= kMaximumTilt &&
                   std::abs(pitch) <= kMaximumTilt &&
                   std::abs(wrappedAngle(yaw)) <= kYawLimit + 1e-6;
        }

        inline bool independent(double dx, double dy, double yaw)
        {
            return std::hypot(dx, dy) > kDistinctXY ||
                   std::abs(wrappedAngle(yaw)) > kDistinctYaw;
        }

        inline bool quality(const CoarseReport &report)
        {
            return report.point_count >= kMinimumPoints &&
                   report.point_count <= kSourceCap &&
                   report.inlier_count >= kMinimumPoints &&
                   report.inlier_count <= report.point_count &&
                   std::isfinite(report.score) && report.score >= 0.0 &&
                   report.score <= kMaximumRmse * kMaximumRmse &&
                   std::isfinite(report.inlier_ratio) &&
                   report.inlier_ratio >= kMinimumOverlap &&
                   report.inlier_ratio <= 1.0 &&
                   std::abs(report.inlier_ratio -
                       static_cast<double>(report.inlier_count) / report.point_count) < 1e-6;
        }

        // Distinct solutions are not interchangeable: require a substantial
        // residual advantage without winning by fitting a smaller scan subset.
        inline bool clearlyBetter(const CoarseReport &best, const CoarseReport &second)
        {
            if (!quality(best) || !quality(second)) return false;
            return second.score - best.score >= 0.005 &&
                   best.score <= 0.8 * second.score &&
                   best.inlier_ratio + 0.02 >= second.inlier_ratio;
        }
    }
}
