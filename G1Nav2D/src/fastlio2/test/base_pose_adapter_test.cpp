#include "localizer/base_pose_adapter.h"
#include <cassert>
#include <limits>
#include <string>

int main(int argc, char **argv)
{
    assert(argc == 2);
    const std::string name(argv[1]);
    Eigen::Matrix4d extrinsic = Eigen::Matrix4d::Identity();
    if (name == "identity")
    {
        const auto pose = fastlio::mapBodyFromBasePose(4.65, -1.25, .02, 0, 0, .71, extrinsic);
        assert(std::abs(pose(0, 3) - 4.65) < 1e-10);
        assert(fastlio::wrappedYawError(fastlio::planarYaw(pose), .71) < 1e-10);
    }
    else if (name == "upside_down_body")
    {
        // This project's actual static TF is Rz(pi)*Ry(pi), approximately Rx(pi).
        extrinsic.block<3, 3>(0, 0) =
            (Eigen::AngleAxisd(3.1416, Eigen::Vector3d::UnitZ()) *
             Eigen::AngleAxisd(3.1416, Eigen::Vector3d::UnitY())).toRotationMatrix();
        const auto body = fastlio::mapBodyFromBasePose(4.65, -1.25, 0, 0, 0, .71, extrinsic);
        const Eigen::Matrix4d recovered_base = body * extrinsic;
        assert(fastlio::wrappedYawError(fastlio::planarYaw(recovered_base), .71) < 1e-10);
        assert((recovered_base.block<3, 3>(0, 0).col(2) - Eigen::Vector3d::UnitZ()).norm() < 1e-10);
    }
    else if (name == "translated_extrinsic")
    {
        extrinsic.block<3, 1>(0, 3) = Eigen::Vector3d(.3, -.2, .1);
        extrinsic.block<3, 3>(0, 0) = Eigen::AngleAxisd(.5, Eigen::Vector3d::UnitZ()).toRotationMatrix();
        const auto body = fastlio::mapBodyFromBasePose(2, 3, 1, 0, 0, .8, extrinsic);
        const Eigen::Matrix4d recovered = body * extrinsic;
        assert((recovered.block<3, 1>(0, 3) - Eigen::Vector3d(2, 3, 1)).norm() < 1e-10);
        assert(fastlio::wrappedYawError(fastlio::planarYaw(recovered), .8) < 1e-10);
    }
    else if (name == "standard_rpy")
    {
        const auto actual = fastlio::mapBodyFromBasePose(0, 0, 0, .2, -.3, .4, extrinsic);
        const Eigen::Matrix3d expected =
            (Eigen::AngleAxisd(.4, Eigen::Vector3d::UnitZ()) *
             Eigen::AngleAxisd(-.3, Eigen::Vector3d::UnitY()) *
             Eigen::AngleAxisd(.2, Eigen::Vector3d::UnitX())).toRotationMatrix();
        assert((actual.block<3, 3>(0, 0) - expected).norm() < 1e-10);
    }
    else if (name == "wrap")
        assert(fastlio::wrappedYawError(M_PI - .01, -M_PI + .01) < .02000001);
    else if (name == "nonfinite" || name == "nonrigid")
    {
        if (name == "nonfinite") extrinsic(0, 3) = std::numeric_limits<double>::quiet_NaN();
        else extrinsic(0, 0) = 2;
        bool rejected = false;
        try { fastlio::mapBodyFromBasePose(0, 0, 0, 0, 0, 0, extrinsic); }
        catch (const std::invalid_argument &) { rejected = true; }
        assert(rejected);
    }
    else return 2;
    return 0;
}
