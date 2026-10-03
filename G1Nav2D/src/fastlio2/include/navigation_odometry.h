#pragma once

#include <cmath>
#include <Eigen/Core>
#include <Eigen/Geometry>

namespace fastlio
{
// ROS-independent measured-state adapter. R_A_B maps B coordinates into A.
// The LIO "body" is its IMU frame; velocity is expressed in the local frame.
struct NavigationOdomInput
{
    double stamp = 0.0;
    double now = 0.0;
    double gyro_stamp = 0.0;
    Eigen::Matrix3d local_body_rotation = Eigen::Matrix3d::Identity();
    Eigen::Vector3d local_body_position = Eigen::Vector3d::Zero();
    Eigen::Vector3d local_body_velocity = Eigen::Vector3d::Zero();
    Eigen::Vector3d body_gyro = Eigen::Vector3d::Zero();
    Eigen::Vector3d body_gyro_bias = Eigen::Vector3d::Zero();
    Eigen::Matrix3d body_base_rotation = Eigen::Matrix3d::Identity();
    Eigen::Vector3d body_base_translation = Eigen::Vector3d::Zero();
};

struct NavigationOdomState
{
    Eigen::Matrix3d rotation = Eigen::Matrix3d::Identity();
    Eigen::Vector3d position = Eigen::Vector3d::Zero();
    Eigen::Vector3d linear = Eigen::Vector3d::Zero();
    Eigen::Vector3d angular = Eigen::Vector3d::Zero();
};

class NavigationOdometry
{
public:
    // These reject invalid measurements; they are NOT commanded-speed limits.
    static constexpr double MAX_AGE = 0.5;
    static constexpr double MAX_IMU_AGE = 0.05;
    static constexpr double MAX_LINEAR_SPEED = 3.0;
    static constexpr double MAX_ANGULAR_SPEED = 8.0;

    void reset() { have_previous_ = false; }

    static bool validRotation(const Eigen::Matrix3d &rotation)
    {
        return rotation.allFinite() &&
               std::abs(rotation.determinant() - 1.0) <= 1e-5 &&
               (rotation.transpose() * rotation - Eigen::Matrix3d::Identity()).norm() <= 1e-5;
    }

    bool update(const NavigationOdomInput &input, NavigationOdomState &output,
                const char *&reason)
    {
        reason = "invalid_state";
        const double age = input.now - input.stamp;
        const double gyro_age = input.stamp - input.gyro_stamp;
        if (!std::isfinite(input.now) || !std::isfinite(input.stamp) ||
            !std::isfinite(input.gyro_stamp) || input.stamp <= 0.0 ||
            input.gyro_stamp <= 0.0 || !input.local_body_position.allFinite() ||
            !input.local_body_velocity.allFinite() || !input.body_gyro.allFinite() ||
            !input.body_gyro_bias.allFinite() || !input.body_base_translation.allFinite() ||
            !validRotation(input.local_body_rotation) || !validRotation(input.body_base_rotation))
        {
            reset();
            return false;
        }
        if (age < -0.1 || age > MAX_AGE || gyro_age < -0.001 || gyro_age > MAX_IMU_AGE)
        {
            reason = "stale_or_future_measurement";
            reset();
            return false;
        }

        NavigationOdomState candidate;
        const Eigen::Vector3d omega_body = input.body_gyro - input.body_gyro_bias;
        candidate.rotation = input.local_body_rotation * input.body_base_rotation;
        candidate.position = input.local_body_position +
                             input.local_body_rotation * input.body_base_translation;
        // Include the base-origin lever arm, even though deployed translation is zero.
        const Eigen::Vector3d base_velocity_local = input.local_body_velocity +
            input.local_body_rotation * omega_body.cross(input.body_base_translation);
        candidate.linear = candidate.rotation.transpose() * base_velocity_local;
        candidate.angular = input.body_base_rotation.transpose() * omega_body;
        if (!candidate.position.allFinite() || !candidate.linear.allFinite() ||
            !candidate.angular.allFinite() || candidate.linear.norm() > MAX_LINEAR_SPEED ||
            candidate.angular.norm() > MAX_ANGULAR_SPEED)
        {
            reason = "implausible_measured_velocity";
            reset();
            return false;
        }

        if (!have_previous_)
        {
            remember(input.stamp, candidate);
            reason = "waiting_for_continuous_measurements";
            return false;
        }
        const double dt = input.stamp - previous_stamp_;
        if (dt <= 0.0)
        {
            reason = "non_monotonic_measurement";
            reset();
            return false;
        }
        if (dt > MAX_AGE)
        {
            remember(input.stamp, candidate);
            reason = "measurement_gap";
            return false;
        }
        const Eigen::Vector3d previous_velocity_local = previous_.rotation * previous_.linear;
        const Eigen::Vector3d predicted_delta =
            0.5 * (previous_velocity_local + base_velocity_local) * dt;
        const double position_residual =
            (candidate.position - previous_.position - predicted_delta).norm();
        const double rotation_delta =
            Eigen::AngleAxisd(previous_.rotation.transpose() * candidate.rotation).angle();
        // Reject a localization reset/jump; re-establish continuity before publishing.
        if (position_residual > 0.25 || rotation_delta > MAX_ANGULAR_SPEED * dt + 0.1)
        {
            remember(input.stamp, candidate);
            reason = "pose_discontinuity";
            return false;
        }
        remember(input.stamp, candidate);
        output = candidate;
        reason = "measured_state";
        return true;
    }

private:
    void remember(double stamp, const NavigationOdomState &state)
    {
        previous_stamp_ = stamp;
        previous_ = state;
        have_previous_ = true;
    }

    bool have_previous_ = false;
    double previous_stamp_ = 0.0;
    NavigationOdomState previous_;
};
} // namespace fastlio
