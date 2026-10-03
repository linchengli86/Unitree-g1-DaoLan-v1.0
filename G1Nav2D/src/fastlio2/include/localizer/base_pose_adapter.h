#pragma once

#include <Eigen/Geometry>
#include <cmath>
#include <stdexcept>

namespace fastlio {

// T_body_base is read from TF; the service input is standard map->base_link.
// Raw ICP source points are in body, so no historical gravity compensation is
// applied here. This leaves the existing /slam_reloc contract untouched.
inline Eigen::Matrix4d mapBodyFromBasePose(
    double x, double y, double z, double roll, double pitch, double yaw,
    const Eigen::Matrix4d &body_base)
{
    if (!std::isfinite(x) || !std::isfinite(y) || !std::isfinite(z) ||
        !std::isfinite(roll) || !std::isfinite(pitch) || !std::isfinite(yaw) ||
        !body_base.allFinite())
        throw std::invalid_argument("non-finite base pose or extrinsic");
    const Eigen::Matrix3d rotation = body_base.block<3, 3>(0, 0);
    if ((rotation.transpose() * rotation - Eigen::Matrix3d::Identity()).norm() > 1e-6 ||
        std::abs(rotation.determinant() - 1.0) > 1e-6 ||
        (body_base.row(3) - Eigen::RowVector4d(0, 0, 0, 1)).norm() > 1e-9)
        throw std::invalid_argument("body/base extrinsic is not rigid");
    Eigen::Matrix4d map_base = Eigen::Matrix4d::Identity();
    map_base.block<3, 3>(0, 0) =
        (Eigen::AngleAxisd(yaw, Eigen::Vector3d::UnitZ()) *
         Eigen::AngleAxisd(pitch, Eigen::Vector3d::UnitY()) *
         Eigen::AngleAxisd(roll, Eigen::Vector3d::UnitX())).toRotationMatrix();
    map_base.block<3, 1>(0, 3) = Eigen::Vector3d(x, y, z);
    return map_base * body_base.inverse();
}

inline double planarYaw(const Eigen::Matrix4d &pose)
{
    return std::atan2(pose(1, 0), pose(0, 0));
}

inline double wrappedYawError(double first, double second)
{
    return std::abs(std::atan2(std::sin(first - second), std::cos(first - second)));
}

}  // namespace fastlio
