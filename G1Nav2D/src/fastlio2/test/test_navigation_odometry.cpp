#include <cassert>
#include <iostream>
#include <limits>
#include "navigation_odometry.h"

using fastlio::NavigationOdometry;
using fastlio::NavigationOdomInput;
using fastlio::NavigationOdomState;

static NavigationOdomInput sample(double stamp = 100.0)
{
    NavigationOdomInput input;
    input.stamp = stamp;
    input.now = stamp + 0.05;
    input.gyro_stamp = stamp - 0.005;
    return input;
}

static void advance(NavigationOdomInput &input, double dt = 0.1)
{
    input.stamp += dt;
    input.now = input.stamp + 0.05;
    input.gyro_stamp = input.stamp - 0.005;
    input.local_body_position += input.local_body_velocity * dt;
}

static bool close(const Eigen::Vector3d &first, const Eigen::Vector3d &second)
{
    return (first - second).norm() < 1e-8;
}

static void expectRejected(const NavigationOdomInput &input, const char *expected)
{
    NavigationOdometry adapter;
    NavigationOdomState output;
    output.linear = Eigen::Vector3d::Constant(99.0);
    const char *reason = nullptr;
    assert(!adapter.update(input, output, reason));
    assert(std::string(reason) == expected);
    assert(close(output.linear, Eigen::Vector3d::Constant(99.0)));
}

int main()
{
    const double pi = std::acos(-1.0);
    const Eigen::Matrix3d deployed_body_base =
        (Eigen::AngleAxisd(pi, Eigen::Vector3d::UnitZ()) *
         Eigen::AngleAxisd(pi, Eigen::Vector3d::UnitY())).toRotationMatrix();
    NavigationOdometry adapter;
    NavigationOdomState output;
    const char *reason = nullptr;
    auto input = sample();
    input.body_base_rotation = deployed_body_base;
    input.local_body_rotation =
        Eigen::AngleAxisd(pi / 2.0, Eigen::Vector3d::UnitZ()).toRotationMatrix() *
        deployed_body_base.transpose();
    input.local_body_velocity = Eigen::Vector3d(0.0, 0.6, 0.0);
    input.body_gyro = Eigen::Vector3d(-0.02, 0.03, -0.62);
    input.body_gyro_bias = Eigen::Vector3d(-0.02, 0.03, 0.08);
    input.local_body_position = Eigen::Vector3d(3.0, 2.0, 0.1);
    assert(!adapter.update(input, output, reason)); // Must establish continuity.
    advance(input);
    assert(adapter.update(input, output, reason));
    assert(close(output.linear, Eigen::Vector3d(0.6, 0.0, 0.0)));
    assert(close(output.angular, Eigen::Vector3d(0.0, 0.0, 0.7)));
    assert(close(output.position, input.local_body_position));
    assert((output.rotation -
            Eigen::AngleAxisd(pi / 2.0, Eigen::Vector3d::UnitZ()).toRotationMatrix()).norm() < 1e-8);

    // Actual reverse/lateral/vertical measurements remain measurements, not clamped commands.
    adapter.reset();
    input = sample();
    input.local_body_velocity = Eigen::Vector3d(-0.8, 0.3, 0.1);
    assert(!adapter.update(input, output, reason));
    advance(input);
    assert(adapter.update(input, output, reason));
    assert(close(output.linear, input.local_body_velocity));

    // Nonzero translation: a rotating IMU gives tangential base-origin velocity.
    adapter.reset();
    input = sample();
    input.body_base_translation = Eigen::Vector3d(1.0, 0.0, 0.0);
    input.body_gyro = Eigen::Vector3d(0.0, 0.0, 1.0);
    assert(!adapter.update(input, output, reason));
    advance(input);
    input.local_body_rotation = Eigen::AngleAxisd(0.1, Eigen::Vector3d::UnitZ()).toRotationMatrix();
    assert(adapter.update(input, output, reason));
    assert(close(output.linear, Eigen::Vector3d(0.0, 1.0, 0.0)));
    assert(close(output.position, Eigen::Vector3d(std::cos(0.1), std::sin(0.1), 0.0)));

    input = sample();
    input.now = input.stamp + 0.501;
    expectRejected(input, "stale_or_future_measurement");
    input = sample();
    input.now = input.stamp - 0.101;
    expectRejected(input, "stale_or_future_measurement");
    input = sample();
    input.gyro_stamp = input.stamp - 0.051;
    expectRejected(input, "stale_or_future_measurement");
    input = sample();
    input.gyro_stamp = input.stamp + 0.002;
    expectRejected(input, "stale_or_future_measurement");
    input = sample();
    input.local_body_velocity.x() = std::numeric_limits<double>::quiet_NaN();
    expectRejected(input, "invalid_state");
    input = sample();
    input.body_gyro.z() = std::numeric_limits<double>::infinity();
    expectRejected(input, "invalid_state");
    input = sample();
    input.body_base_rotation(0, 0) = 2.0;
    expectRejected(input, "invalid_state");
    input = sample();
    input.body_base_rotation(2, 2) = -1.0; // Reflection is not a rigid rotation.
    expectRejected(input, "invalid_state");
    input = sample();
    input.local_body_velocity.x() = 3.01;
    expectRejected(input, "implausible_measured_velocity");
    input = sample();
    input.body_gyro.z() = 8.01;
    expectRejected(input, "implausible_measured_velocity");

    adapter.reset();
    input = sample();
    assert(!adapter.update(input, output, reason));
    assert(!adapter.update(input, output, reason));
    assert(std::string(reason) == "non_monotonic_measurement");
    advance(input);
    assert(!adapter.update(input, output, reason));
    advance(input);
    assert(adapter.update(input, output, reason));

    // Gaps, pose resets and orientation jumps do not generate fake velocity spikes.
    advance(input, 0.6);
    assert(!adapter.update(input, output, reason));
    assert(std::string(reason) == "measurement_gap");
    advance(input);
    assert(adapter.update(input, output, reason));
    advance(input);
    input.local_body_position.x() += 2.0;
    assert(!adapter.update(input, output, reason));
    assert(std::string(reason) == "pose_discontinuity");
    advance(input);
    assert(adapter.update(input, output, reason));
    advance(input);
    input.local_body_rotation = Eigen::AngleAxisd(pi, Eigen::Vector3d::UnitZ()).toRotationMatrix();
    assert(!adapter.update(input, output, reason));
    assert(std::string(reason) == "pose_discontinuity");

    // Explicit reset always requires two fresh consecutive measured states.
    adapter.reset();
    input = sample();
    assert(!adapter.update(input, output, reason));
    advance(input);
    assert(adapter.update(input, output, reason));
    assert(close(output.linear, Eigen::Vector3d::Zero()));
    assert(close(output.angular, Eigen::Vector3d::Zero()));
    std::cout << "navigation_odometry functional checks passed\n";
}
