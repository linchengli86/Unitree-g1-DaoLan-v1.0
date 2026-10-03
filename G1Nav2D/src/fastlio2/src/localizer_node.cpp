#include <thread>
#include <csignal>
#include <atomic>
#include <chrono>
#include <fstream>
#include <sstream>
#include <ros/ros.h>
#include <ros/package.h>
#include "localizer/icp_localizer.h"
#include "localizer/base_pose_adapter.h"
#include "lio_builder/lio_builder.h"
#include "navigation_odometry.h"
#include <tf2_ros/transform_broadcaster.h>
#include <tf2_ros/transform_listener.h>
#include <tf2_ros/buffer.h>
#include <pcl/io/pcd_io.h>
#include <pcl/filters/voxel_grid.h>

#include <nav_msgs/Odometry.h>
#include <sensor_msgs/PointCloud2.h>

#include "fastlio/SlamReLoc.h"
#include "fastlio/MapConvert.h"
#include "fastlio/SlamHold.h"
#include "fastlio/SlamStart.h"
#include "fastlio/SlamRelocCheck.h"

volatile std::sig_atomic_t terminate_flag = false;

Eigen::Matrix3d rotationMatrixFromVectors(const Eigen::Vector3d& from_vec, const Eigen::Vector3d& to_vec)
{
    Eigen::Vector3d a = from_vec.normalized();
    Eigen::Vector3d b = to_vec.normalized();
    double c = a.dot(b);
    if (c > 0.9999) return Eigen::Matrix3d::Identity();
    if (c < -0.9999) {
        Eigen::Vector3d axis = Eigen::Vector3d::UnitX();
        if (std::abs(a.dot(axis)) > 0.9) axis = Eigen::Vector3d::UnitY();
        axis = axis - axis.dot(a) * a;
        axis.normalize();
        return Eigen::AngleAxisd(M_PI, axis).toRotationMatrix();
    }
    Eigen::Vector3d v = a.cross(b);
    double s = v.norm();
    Eigen::Matrix3d vx;
    vx <<     0, -v.z(),  v.y(),
           v.z(),     0, -v.x(),
          -v.y(),  v.x(),     0;
    return Eigen::Matrix3d::Identity() + vx + vx * vx * ((1 - c) / (s * s));
}

void signalHandler(int signum)
{
    std::cout << "SHUTTING DOWN LOCALIZER NODE!" << std::endl;
    terminate_flag = true;
}

struct SharedData
{
    std::mutex service_mutex;
    std::mutex main_mutex;
    std::atomic<bool> pose_updated{false};
    std::atomic<bool> localizer_activate{false};
    std::atomic<bool> service_called{false};
    std::atomic<bool> service_success{false};
    std::atomic<bool> service_busy{false};

    bool coarse_requested = false;  // Protected by service_mutex.
    bool standard_requested = false;
    std::string request_id;
    double requested_at = 0.0;
    Eigen::Matrix4d body_base = Eigen::Matrix4d::Identity();
    Eigen::Matrix4d base_prior = Eigen::Matrix4d::Identity();

    std::string map_path;
    Eigen::Matrix3d offset_rot = Eigen::Matrix3d::Identity();
    Eigen::Vector3d offset_pos = Eigen::Vector3d::Zero();
    Eigen::Matrix3d local_rot;
    Eigen::Vector3d local_pos;
    Eigen::Matrix4d initial_guess;
    fastlio::PointCloudXYZI::Ptr cloud;
    double cloud_time = 0.0;  // Protected by main_mutex.

    std::atomic<bool> reset_flag{false};
    std::atomic<bool> halt_flag{false};
};

struct CoarseRequest
{
    std::string id, map_path;
    double started_at = 0.0;
    Eigen::Matrix4d body_base, base_prior;
    double accepted_radius = 1.0;
    double accepted_yaw = M_PI / 4.0;
};

static void publishCoarseReport(const CoarseRequest &request, const std::string &state,
                               const fastlio::CoarseReport &fit,
                               const fastlio::CoarseReport &validation,
                               const std::string &reason,
                               const Eigen::Matrix4d *verified_base = nullptr)
{
    XmlRpc::XmlRpcValue value;
    value["request_id"] = request.id;
    value["map_path"] = request.map_path;
    value["started_at"] = request.started_at;
    value["finished_at"] = (state == "succeeded" || state == "failed") ? ros::WallTime::now().toSec() : 0.0;
    value["state"] = state;
    value["reason"] = reason;
    value["score"] = std::isfinite(fit.score) ? fit.score : -1.0;
    value["inlier_ratio"] = fit.inlier_ratio;
    value["count"] = static_cast<int>(fit.point_count);
    value["candidate_count"] = fit.candidate_count;
    value["final_candidates"] = fit.refined_count;
    value["distinct_solutions"] = fit.distinct_solution_count;
    value["best_second_ratio"] = fit.best_second_ratio;
    value["matching_seconds"] = fit.elapsed_seconds;
    value["inlier_count"] = static_cast<int>(fit.inlier_count);
    value["validation_score"] = std::isfinite(validation.score) ? validation.score : -1.0;
    value["validation_inlier_ratio"] = validation.inlier_ratio;
    value["validation_count"] = static_cast<int>(validation.point_count);
    value["ambiguous"] = fit.reason == "ambiguous_distinct_solutions";
    value["pose_verified"] = verified_base != nullptr;
    value["independent_scan_verified"] = verified_base != nullptr && validation.status;
    if (verified_base)
    {
        XmlRpc::XmlRpcValue pose;
        pose["x"] = (*verified_base)(0, 3);
        pose["y"] = (*verified_base)(1, 3);
        pose["z"] = (*verified_base)(2, 3);
        pose["yaw"] = fastlio::planarYaw(*verified_base);
        value["verified_pose"] = pose;
    }
    ros::param::set("/daolan_coarse_relocalization", value);
}

class LocalizerThread
{
public:
    LocalizerThread() {}

    void setSharedDate(std::shared_ptr<SharedData> shared_data)
    {
        shared_data_ = shared_data;
    }

    void setRate(double rate)
    {
        rate_ = std::make_shared<ros::Rate>(rate);
    }
    void setRate(std::shared_ptr<ros::Rate> rate)
    {
        rate_ = rate;
    }
    void setLocalizer(std::shared_ptr<fastlio::IcpLocalizer> localizer)
    {
        icp_localizer_ = localizer;
    }

    void operator()()
    {
        current_cloud_.reset(new pcl::PointCloud<pcl::PointXYZI>);

        while (ros::ok())
        {
            rate_->sleep();
            if (terminate_flag)
                break;
            if (shared_data_->halt_flag)
                continue;
            if (!shared_data_->localizer_activate)
                continue;
            if (!shared_data_->pose_updated)
                continue;
            gloabl_pose_.setIdentity();
            bool rectify = false;
            bool handling_service = false;
            Eigen::Matrix4d init_guess;
            double source_time = 0.0;
            {
                std::lock_guard<std::mutex> lock(shared_data_->main_mutex);
                shared_data_->pose_updated = false;
                init_guess.setIdentity();
                local_rot_ = shared_data_->local_rot;
                local_pos_ = shared_data_->local_pos;
                init_guess.block<3, 3>(0, 0) = shared_data_->offset_rot * local_rot_;
                init_guess.block<3, 1>(0, 3) = shared_data_->offset_rot * local_pos_ + shared_data_->offset_pos;
                source_time = shared_data_->cloud_time;
                if (!shared_data_->cloud) continue;
                pcl::copyPointCloud(*shared_data_->cloud, *current_cloud_);
            }

            if (shared_data_->service_called)
            {
                handling_service = true;
                // Snapshot the request; do not block ROS callbacks while ICP
                // runs.  service_busy rejects a competing/reset request.
                bool coarse = false;
                bool standard = false;
                CoarseRequest request;
                Eigen::Matrix4d requested_guess;
                std::string map_path;
                {
                    std::lock_guard<std::mutex> lock(shared_data_->service_mutex);
                    shared_data_->service_called = false;
                    coarse = shared_data_->coarse_requested;
                    standard = shared_data_->standard_requested;
                    map_path = shared_data_->map_path;
                    requested_guess = shared_data_->initial_guess;
                    request = {shared_data_->request_id, map_path, shared_data_->requested_at,
                               shared_data_->body_base, shared_data_->base_prior};
                    request.accepted_radius = coarse ? 1.0 : 0.20;
                    request.accepted_yaw = coarse ? M_PI / 4.0 : 0.45;
                }
                if (standard)
                {
                    runCoarse(request, requested_guess, source_time);
                    continue;
                }
                try
                {
                    icp_localizer_->init(map_path, false);
                    gloabl_pose_ = icp_localizer_->multi_align_sync(current_cloud_, requested_guess);
                }
                catch (const std::exception &error)
                {
                    ROS_ERROR("Relocalization failed: %s", error.what());
                    shared_data_->localizer_activate = false;
                    shared_data_->service_success = false;
                    shared_data_->service_busy = false;
                    continue;
                }
                if (icp_localizer_->isSuccess())
                {
                    rectify = true;
                    shared_data_->localizer_activate = true;
                }

                else
                {
                    rectify = false;
                    shared_data_->localizer_activate = false;
                    shared_data_->service_success = false;
                    shared_data_->service_busy = false;
                }
            }
            else
            {
                gloabl_pose_ = icp_localizer_->align(current_cloud_, init_guess);
                if (icp_localizer_->isSuccess())
                    rectify = true;
                else
                    rectify = false;
            }

            if (rectify)
            {
                std::lock_guard<std::mutex> lock(shared_data_->main_mutex);
                shared_data_->offset_rot = gloabl_pose_.block<3, 3>(0, 0) * local_rot_.transpose();
                shared_data_->offset_pos = -gloabl_pose_.block<3, 3>(0, 0) * local_rot_.transpose() * local_pos_ + gloabl_pose_.block<3, 1>(0, 3);
                if (handling_service)
                {
                    shared_data_->service_success = true;
                    shared_data_->service_busy = false;
                }
            }
        }
    }

private:
    void runCoarse(const CoarseRequest &request, const Eigen::Matrix4d &guess,
                   double source_time)
    {
        fastlio::CoarseReport fit, validation;
        auto fail = [&](const std::string &reason)
        {
            shared_data_->localizer_activate = false;
            shared_data_->service_success = false;
            publishCoarseReport(request, "failed", fit, validation, reason);
            shared_data_->service_busy = false;
            ROS_WARN("Coarse relocalization rejected: %s", reason.c_str());
        };
        try
        {
            const double age = ros::Time::now().toSec() - source_time;
            if (!std::isfinite(age) || age < -0.1 || age > 1.5 || current_cloud_->empty())
            {
                fail("source_scan_stale_or_empty");
                return;
            }
            publishCoarseReport(request, "matching", fit, validation, "bounded_search");
            icp_localizer_->init(request.map_path, false);
            if (ros::WallTime::now().toSec() - request.started_at > 27.0)
            {
                fail("request_time_budget_exceeded");
                return;
            }
            Eigen::Matrix4d candidate = icp_localizer_->multi_align_coarse(current_cloud_, guess);
            fit = icp_localizer_->getCoarseReport();
            if (!fit.status || !candidate.allFinite())
            {
                fail(fit.reason);
                return;
            }

            Eigen::Matrix4d local_before = Eigen::Matrix4d::Identity();
            local_before.block<3, 3>(0, 0) = local_rot_;
            local_before.block<3, 1>(0, 3) = local_pos_;
            const double completed_at = ros::Time::now().toSec();
            const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(3);
            auto fresh = pcl::PointCloud<pcl::PointXYZI>::Ptr(new pcl::PointCloud<pcl::PointXYZI>);
            Eigen::Matrix4d local_after = Eigen::Matrix4d::Identity();
            bool acquired = false;
            publishCoarseReport(request, "validating", fit, validation, "waiting_independent_scan");
            while (ros::ok() && !terminate_flag && std::chrono::steady_clock::now() < deadline)
            {
                {
                    std::lock_guard<std::mutex> lock(shared_data_->main_mutex);
                    const double scan_age = ros::Time::now().toSec() - shared_data_->cloud_time;
                    if (shared_data_->cloud && shared_data_->cloud_time > completed_at &&
                        shared_data_->cloud_time > source_time &&
                        scan_age >= -0.1 && scan_age <= 1.5)
                    {
                        pcl::copyPointCloud(*shared_data_->cloud, *fresh);
                        local_after.block<3, 3>(0, 0) = shared_data_->local_rot;
                        local_after.block<3, 1>(0, 3) = shared_data_->local_pos;
                        acquired = true;
                    }
                }
                if (acquired) break;
                std::this_thread::sleep_for(std::chrono::milliseconds(30));
            }
            if (!acquired || fresh->empty())
            {
                fail("independent_scan_timeout");
                return;
            }
            if ((local_after.block<3, 1>(0, 3) - local_before.block<3, 1>(0, 3)).norm() > 0.03 ||
                Eigen::AngleAxisd(local_before.block<3, 3>(0, 0).transpose() *
                                 local_after.block<3, 3>(0, 0)).angle() > 0.05)
            {
                fail("robot_moved_during_matching");
                return;
            }
            // Compensate the two scans' odometry, without re-fitting validation.
            candidate = candidate * local_before.inverse() * local_after;
            validation = icp_localizer_->verifyCoarseAlignment(fresh, candidate);
            if (!validation.status)
            {
                fail("independent_scan_" + validation.reason);
                return;
            }
            const Eigen::Matrix4d verified_base = candidate * request.body_base;
            if (!verified_base.allFinite() ||
                std::hypot(verified_base(0, 3) - request.base_prior(0, 3),
                           verified_base(1, 3) - request.base_prior(1, 3)) > request.accepted_radius + 1e-6 ||
                fastlio::wrappedYawError(fastlio::planarYaw(verified_base),
                                        fastlio::planarYaw(request.base_prior)) > request.accepted_yaw + 1e-6)
            {
                fail("refined_base_pose_outside_rough_prior");
                return;
            }
            if (!ros::ok() || terminate_flag)
            {
                fail("node_shutdown");
                return;
            }
            if (ros::WallTime::now().toSec() - request.started_at > 27.0)
            {
                fail("request_time_budget_exceeded");
                return;
            }
            // The offset is committed only after every acceptance check.
            bool still_stationary = false;
            {
                std::lock_guard<std::mutex> lock(shared_data_->main_mutex);
                const double age = ros::Time::now().toSec() - shared_data_->cloud_time;
                still_stationary = age >= -0.1 && age <= 1.5 &&
                    (shared_data_->local_pos - local_after.block<3, 1>(0, 3)).norm() <= 0.03 &&
                    Eigen::AngleAxisd(local_after.block<3, 3>(0, 0).transpose() *
                                     shared_data_->local_rot).angle() <= 0.05;
                if (still_stationary)
                {
                    shared_data_->offset_rot = candidate.block<3, 3>(0, 0) *
                                              local_after.block<3, 3>(0, 0).transpose();
                    shared_data_->offset_pos = candidate.block<3, 1>(0, 3) -
                                              shared_data_->offset_rot * local_after.block<3, 1>(0, 3);
                }
            }
            if (!still_stationary)
            {
                fail("robot_moved_or_scan_stale_during_validation");
                return;
            }
            publishCoarseReport(request, "succeeded", fit, validation, "verified", &verified_base);
            shared_data_->service_success = true;
            shared_data_->localizer_activate = true;
            shared_data_->service_busy = false;
            ROS_INFO("Coarse relocalization verified: (%.3f, %.3f), yaw=%.3f, overlap=%.3f",
                     verified_base(0, 3), verified_base(1, 3), fastlio::planarYaw(verified_base),
                     validation.inlier_ratio);
        }
        catch (const std::exception &error)
        {
            fail(std::string("exception: ") + error.what());
        }
    }

    std::shared_ptr<SharedData> shared_data_;
    std::shared_ptr<fastlio::IcpLocalizer> icp_localizer_;
    std::shared_ptr<ros::Rate> rate_;
    pcl::PointCloud<pcl::PointXYZI>::Ptr current_cloud_;
    Eigen::Matrix4d gloabl_pose_;
    Eigen::Matrix3d local_rot_;
    Eigen::Vector3d local_pos_;
};

class LocalizerROS
{
public:
    LocalizerROS(tf2_ros::TransformBroadcaster &br, std::shared_ptr<SharedData> shared_data)
        : shared_date_(shared_data), tf_listener_(tf_buffer_), br_(br)
    {
        initParams();
        initSubscribers();
        initPublishers();
        initServices();
        lio_builder_ = std::make_shared<fastlio::LIOBuilder>(lio_params_);
        icp_localizer_ = std::make_shared<fastlio::IcpLocalizer>(localizer_params_.refine_resolution,
                                                                 localizer_params_.rough_resolution,
                                                                 localizer_params_.refine_iter,
                                                                 localizer_params_.rough_iter,
                                                                 localizer_params_.thresh);
        icp_localizer_->setSearchParams(localizer_params_.xy_offset, localizer_params_.yaw_offset, localizer_params_.yaw_resolution);
        localizer_loop_.setRate(loop_rate_);
        localizer_loop_.setSharedDate(shared_data);
        localizer_loop_.setLocalizer(icp_localizer_);
        localizer_thread_ = std::make_shared<std::thread>(std::ref(localizer_loop_));
    }

    void initParams()
    {
        nh_.param<std::string>("map_frame", global_frame_, "map");
        nh_.param<std::string>("local_frame", local_frame_, "local");
        nh_.param<std::string>("body_frame", body_frame_, "body");
        nh_.param<std::string>("imu_topic", imu_data_.topic, "/livox/imu");
        nh_.param<std::string>("livox_topic", livox_data_.topic, "/livox/lidar");
        nh_.param<bool>("publish_map_cloud", publish_map_cloud_, false);
        double local_rate, loop_rate;
        nh_.param<double>("local_rate", local_rate, 20.0);
        nh_.param<double>("loop_rate", loop_rate, 1.0);
        local_rate_ = std::make_shared<ros::Rate>(local_rate);
        loop_rate_ = std::make_shared<ros::Rate>(loop_rate);

        nh_.param<double>("lio_builder/det_range", lio_params_.det_range, 100.0);
        nh_.param<double>("lio_builder/cube_len", lio_params_.cube_len, 500.0);
        nh_.param<double>("lio_builder/resolution", lio_params_.resolution, 0.1);
        nh_.param<double>("lio_builder/move_thresh", lio_params_.move_thresh, 1.5);
        nh_.param<bool>("lio_builder/align_gravity", lio_params_.align_gravity, true);
        nh_.param<std::vector<double>>("lio_builder/imu_ext_rot", lio_params_.imu_ext_rot, std::vector<double>());
        nh_.param<std::vector<double>>("lio_builder/imu_ext_pos", lio_params_.imu_ext_pos, std::vector<double>());
        
        nh_.param<double>("localizer/refine_resolution", localizer_params_.refine_resolution, 0.2);
        nh_.param<double>("localizer/rough_resolution", localizer_params_.rough_resolution, 0.5);
        nh_.param<double>("localizer/refine_iter", localizer_params_.refine_iter, 5);
        nh_.param<double>("localizer/rough_iter", localizer_params_.rough_iter, 10);
        nh_.param<double>("localizer/thresh", localizer_params_.thresh, 0.15);

        nh_.param<double>("localizer/xy_offset", localizer_params_.xy_offset, 2.0);
        nh_.param<double>("localizer/yaw_resolution", localizer_params_.yaw_resolution, 0.5);
        nh_.param<int>("localizer/yaw_offset", localizer_params_.yaw_offset, 1);
    }

    void initSubscribers()
    {
        imu_sub_ = nh_.subscribe(imu_data_.topic, 1000, &ImuData::callback, &imu_data_);
        livox_sub_ = nh_.subscribe(livox_data_.topic, 1000, &LivoxData::callback, &livox_data_);
    }

    void initPublishers()
    {
        local_cloud_pub_ = nh_.advertise<sensor_msgs::PointCloud2>("local_cloud", 1000);
        body_cloud_pub_ = nh_.advertise<sensor_msgs::PointCloud2>("body_cloud", 1000);
        odom_pub_ = nh_.advertise<nav_msgs::Odometry>("slam_odom", 1000);
        navigation_odom_pub_ = nh_.advertise<nav_msgs::Odometry>("navigation_odom", 1);
        map_cloud_pub_ = nh_.advertise<sensor_msgs::PointCloud2>("global_cloud", 1000);
        body_cloud_org_pub_ = nh_.advertise<sensor_msgs::PointCloud2>("velodyne_points", 1);
    }

    // bool relocCallback(fastlio::SlamReLoc::Request &req, fastlio::SlamReLoc::Response &res)
    // {
    //     std::string map_path = req.pcd_path;
    //     float x = req.x;
    //     float y = req.y;
    //     float z = req.z;
    //     float roll = req.roll;
    //     float pitch = req.pitch;
    //     float yaw = req.yaw;
    //     Eigen::AngleAxisf rollAngle(roll, Eigen::Vector3f::UnitX());
    //     Eigen::AngleAxisf pitchAngle(pitch, Eigen::Vector3f::UnitY());
    //     Eigen::AngleAxisf yawAngle(yaw, Eigen::Vector3f::UnitZ());
    //     Eigen::Quaternionf q = rollAngle * pitchAngle * yawAngle;
    //     {
    //         std::lock_guard<std::mutex> lock(shared_date_->service_mutex);
    //         shared_date_->halt_flag = false;
    //         shared_date_->service_called = true;
    //         shared_date_->localizer_activate = true;
    //         shared_date_->map_path = map_path;
    //         shared_date_->initial_guess.block<3, 3>(0, 0) = q.toRotationMatrix().cast<double>();
    //         shared_date_->initial_guess.block<3, 1>(0, 3) = Eigen::Vector3d(x, y, z);
    //     }
    //     res.status = 1;
    //     res.message = "RELOCALIZE CALLED!";

    //     return true;
    // }

    bool relocCallback(fastlio::SlamReLoc::Request &req, fastlio::SlamReLoc::Response &res)
    {
        if (shared_date_->service_busy)
        {
            res.status = 0;
            res.message = "RELOCALIZATION BUSY";
            return true;
        }
        std::string map_path = req.pcd_path;
        float x = req.x;
        float y = req.y;
        float z = req.z;
        float roll = req.roll;
        float pitch = req.pitch;
        float yaw = req.yaw;
        
        // === 新增：获取IMU初始化时的重力向量 ===
        Eigen::Vector3d mean_acc = Eigen::Vector3d::Zero();
        if (lio_builder_ && lio_builder_->getIMUProcessor()) {
            auto imu_processor = lio_builder_->getIMUProcessor();
            if (imu_processor->isInitialized()) {
                mean_acc = imu_processor->getMeanAcc();
                ROS_INFO("Retrieved mean_acc from IMU processor: [%.3f, %.3f, %.3f]", 
                        mean_acc[0], mean_acc[1], mean_acc[2]);
            } else {
                ROS_WARN("IMU processor not initialized yet, using default gravity vector");
                mean_acc = Eigen::Vector3d(0.0, 0.0, 9.8);  // 默认值
            }
        } else {
            ROS_WARN("LIO builder or IMU processor not available, using default gravity vector");
            mean_acc = Eigen::Vector3d(0.0, 0.0, 9.8);  // 默认值
        }
        
        // === 重力对齐逆变换 ===
        Eigen::Matrix3d final_rot;
        if (lio_params_.align_gravity) {
            // 1. 构造重力对齐后的旋转矩阵
            Eigen::AngleAxisf rollAngle(roll, Eigen::Vector3f::UnitX());
            Eigen::AngleAxisf pitchAngle(pitch, Eigen::Vector3f::UnitY());
            Eigen::AngleAxisf yawAngle(yaw, Eigen::Vector3f::UnitZ());
            Eigen::Matrix3d aligned_rot_matrix = (rollAngle * pitchAngle * yawAngle).toRotationMatrix().cast<double>();
            
            // 2. 计算重力对齐变换矩阵
            Eigen::Vector3d from_vec = (-mean_acc).normalized();  // 保持与 LIO 内部点云坐标约定一致
            Eigen::Vector3d to_vec(0.0, 0.0, -1.0);
            
            Eigen::Matrix3d gravity_align_rot = rotationMatrixFromVectors(from_vec, to_vec);
            
            // 3. 逆向变换：original_rot = gravity_align_rot.T * aligned_rot
            final_rot = gravity_align_rot.transpose() * aligned_rot_matrix;
            
            ROS_INFO("Gravity alignment applied using mean_acc: [%.3f, %.3f, %.3f]", 
                    mean_acc[0], mean_acc[1], mean_acc[2]);
            ROS_INFO("Aligned RPY: [%.3f, %.3f, %.3f]", roll, pitch, yaw);
            
            // 转换回欧拉角用于日志显示
            Eigen::Vector3d original_rpy = final_rot.eulerAngles(0, 1, 2);
            ROS_INFO("Original RPY: [%.3f, %.3f, %.3f]", original_rpy[0], original_rpy[1], original_rpy[2]);
        } else {
            // 不需要重力对齐逆变换
            Eigen::AngleAxisf rollAngle(roll, Eigen::Vector3f::UnitX());
            Eigen::AngleAxisf pitchAngle(pitch, Eigen::Vector3f::UnitY());
            Eigen::AngleAxisf yawAngle(yaw, Eigen::Vector3f::UnitZ());
            final_rot = (rollAngle * pitchAngle * yawAngle).toRotationMatrix().cast<double>();
        }
        
        {
            std::lock_guard<std::mutex> lock(shared_date_->service_mutex);
            shared_date_->halt_flag = false;
            // Never expose the previous request's success while a new ICP
            // attempt is still running.
            shared_date_->service_success = false;
            shared_date_->coarse_requested = false;
            shared_date_->standard_requested = false;
            shared_date_->service_busy = true;
            shared_date_->service_called = true;
            shared_date_->localizer_activate = true;
            shared_date_->map_path = map_path;
            shared_date_->initial_guess.block<3, 3>(0, 0) = final_rot;
            shared_date_->initial_guess.block<3, 1>(0, 3) = Eigen::Vector3d(x, y, z);
        }
        
        res.status = 1;
        res.message = "RELOCALIZE CALLED!";
        return true;
    }

    bool standardBaseRelocCallback(fastlio::SlamReLoc::Request &req,
                                  fastlio::SlamReLoc::Response &res)
    {
        return enqueueBaseRelocation(req, res, false);
    }

    bool coarseRelocCallback(fastlio::SlamReLoc::Request &req,
                             fastlio::SlamReLoc::Response &res)
    {
        return enqueueBaseRelocation(req, res, true);
    }

    bool enqueueBaseRelocation(fastlio::SlamReLoc::Request &req,
                               fastlio::SlamReLoc::Response &res, bool coarse)
    {
        auto reject = [&](const std::string &reason)
        {
            res.status = 0;
            res.message = reason;
            return true;
        };
        if (shared_date_->service_busy) return reject("RELOCALIZATION BUSY");
        if (ros::Time::isSimTime()) return reject("Real sensor time is required");
        const std::string expected_map = ros::package::getPath("fastlio") + "/PCD/map.pcd";
        if (req.pcd_path != expected_map || !std::ifstream(expected_map).good())
            return reject("Expected the deployed fastlio/PCD/map.pcd");
        if (!lio_builder_ || !lio_builder_->getIMUProcessor() ||
            !lio_builder_->getIMUProcessor()->isInitialized())
            return reject("IMU is not initialized");
        {
            std::lock_guard<std::mutex> lock(shared_date_->main_mutex);
            const double age = ros::Time::now().toSec() - shared_date_->cloud_time;
            if (!shared_date_->cloud || shared_date_->cloud->empty() ||
                !std::isfinite(age) || age < -0.1 || age > 1.5)
                return reject("Fresh body point cloud is required");
        }
        try
        {
            const auto tf = tf_buffer_.lookupTransform(body_frame_, "base_link", ros::Time(0), ros::Duration(0.15));
            const double age = ros::Time::now().toSec() - tf.header.stamp.toSec();
            if (!tf.header.stamp.isZero() && (age < -0.1 || age > 1.5))
                return reject("body->base_link TF is stale");
            const auto &qmsg = tf.transform.rotation;
            Eigen::Quaterniond q(qmsg.w, qmsg.x, qmsg.y, qmsg.z);
            if (!std::isfinite(q.norm()) || std::abs(q.norm() - 1.0) > 1e-3)
                return reject("body->base_link TF rotation is invalid");
            Eigen::Matrix4d body_base = Eigen::Matrix4d::Identity();
            body_base.block<3, 3>(0, 0) = q.normalized().toRotationMatrix();
            body_base.block<3, 1>(0, 3) = Eigen::Vector3d(tf.transform.translation.x,
                                                       tf.transform.translation.y,
                                                       tf.transform.translation.z);
            const Eigen::Matrix4d body_guess = fastlio::mapBodyFromBasePose(
                req.x, req.y, req.z, req.roll, req.pitch, req.yaw, body_base);
            const Eigen::Matrix4d base_prior = body_guess * body_base;
            std::ostringstream id;
            id << ros::WallTime::now().toNSec() << "-" << ++request_sequence_;
            CoarseRequest request{id.str(), expected_map, ros::WallTime::now().toSec(), body_base, base_prior};
            {
                std::lock_guard<std::mutex> lock(shared_date_->service_mutex);
                if (shared_date_->service_busy) return reject("RELOCALIZATION BUSY");
                shared_date_->halt_flag = false;
                shared_date_->service_success = false;
                shared_date_->coarse_requested = coarse;
                // Both new base-frame modes preserve the exact body rotation.
                // Legacy multi_align_sync rebuilds Rx*Ry*Rz candidates, which
                // flips heading for this robot's upside-down body extrinsic.
                // Confirmed-cache mode uses the verified matcher too, but its
                // final acceptance remains within 20 cm / 0.45 rad.
                shared_date_->standard_requested = true;
                shared_date_->service_busy = true;
                shared_date_->map_path = expected_map;
                shared_date_->initial_guess = body_guess;
                shared_date_->body_base = body_base;
                shared_date_->base_prior = base_prior;
                shared_date_->request_id = request.id;
                shared_date_->requested_at = request.started_at;
                shared_date_->localizer_activate = true;
                shared_date_->service_called = true;
            }
            // Only the worker writes reports, so a late queued report cannot
            // overwrite a more advanced state from a fast registration.
            res.status = 1;
            res.message = coarse ? "{\"request_id\":\"" + request.id + "\"}" : "RELOCALIZE BASE CALLED!";
            ROS_INFO("Standard base pose relocalization queued (%s), radius=%s",
                     request.id.c_str(), coarse ? "1m" : "precise");
            return true;
        }
        catch (const std::exception &error)
        {
            return reject(std::string("Standard base pose rejected: ") + error.what());
        }
    }

    bool mapConvertCallback(fastlio::MapConvert::Request &req, fastlio::MapConvert::Response &res)
    {
        pcl::PCDReader reader;
        pcl::PointCloud<pcl::PointXYZI>::Ptr cloud(new pcl::PointCloud<pcl::PointXYZI>);
        reader.read(req.map_path, *cloud);
        pcl::VoxelGrid<pcl::PointXYZI> down_sample_filter;
        down_sample_filter.setLeafSize(req.resolution, req.resolution, req.resolution);
        down_sample_filter.setInputCloud(cloud);
        down_sample_filter.filter(*cloud);

        fastlio::PointCloudXYZI::Ptr cloud_with_norm = fastlio::IcpLocalizer::addNorm(cloud);
        pcl::PCDWriter writer;
        writer.writeBinaryCompressed(req.save_path, *cloud_with_norm);
        res.message = "CONVERT SUCCESS!";
        res.status = 1;

        return true;
    }

    bool slamHoldCallback(fastlio::SlamHold::Request &req, fastlio::SlamHold::Response &res)
    {
        if (shared_date_->service_busy)
        {
            res.status = 0;
            res.message = "RELOCALIZATION BUSY; HOLD NOT APPLIED";
            return true;
        }
        shared_date_->service_mutex.lock();
        shared_date_->halt_flag = true;
        shared_date_->reset_flag = true;
        shared_date_->service_mutex.unlock();
        res.message = "SLAM HALT!";
        res.status = 1;
        return true;
    }

    bool slamStartCallback(fastlio::SlamStart::Request &req, fastlio::SlamStart::Response &res)
    {
        if (shared_date_->service_busy)
        {
            res.status = 0;
            res.message = "RELOCALIZATION BUSY; START NOT APPLIED";
            return true;
        }
        shared_date_->service_mutex.lock();
        shared_date_->halt_flag = false;
        shared_date_->service_mutex.unlock();
        res.message = "SLAM START!";
        res.status = 1;
        return true;
    }

    bool slamRelocCheckCallback(fastlio::SlamRelocCheck::Request &req, fastlio::SlamRelocCheck::Response &res)
    {
        res.status = shared_date_->service_success;
        return true;
    }

    void initServices()
    {
        reloc_server_ = nh_.advertiseService("slam_reloc", &LocalizerROS::relocCallback, this);
        base_reloc_server_ = nh_.advertiseService("slam_reloc_base", &LocalizerROS::standardBaseRelocCallback, this);
        coarse_reloc_server_ = nh_.advertiseService("slam_reloc_coarse", &LocalizerROS::coarseRelocCallback, this);
        map_convert_server_ = nh_.advertiseService("map_convert", &LocalizerROS::mapConvertCallback, this);
        hold_server_ = nh_.advertiseService("slam_hold", &LocalizerROS::slamHoldCallback, this);
        start_server_ = nh_.advertiseService("slam_start", &LocalizerROS::slamStartCallback, this);
        reloc_check_server_ = nh_.advertiseService("slam_reloc_check", &LocalizerROS::slamRelocCheckCallback, this);
    }

    void publishCloud(ros::Publisher &publisher, const sensor_msgs::PointCloud2 &cloud_to_pub)
    {
        if (publisher.getNumSubscribers() == 0)
            return;
        publisher.publish(cloud_to_pub);
    }

    void publishOdom(const nav_msgs::Odometry &odom_to_pub)
    {
        if (odom_pub_.getNumSubscribers() == 0)
            return;
        odom_pub_.publish(odom_to_pub);
    }

    void publishNavigationOdom()
    {
        // Do not block the LIO loop, invent an extrinsic, or cache a dynamic TF.
        if (!navigation_extrinsic_ready_)
        {
            try
            {
                const auto transform = tf_buffer_.lookupTransform(body_frame_, "base_link", ros::Time(0));
                const auto &q = transform.transform.rotation;
                const auto &t = transform.transform.translation;
                Eigen::Quaterniond rotation(q.w, q.x, q.y, q.z);
                navigation_body_base_translation_ = Eigen::Vector3d(t.x, t.y, t.z);
                if (!transform.header.stamp.isZero() || !rotation.coeffs().allFinite() ||
                    std::abs(rotation.norm() - 1.0) > 1e-5 ||
                    !navigation_body_base_translation_.allFinite())
                {
                    ROS_WARN_THROTTLE(1.0, "Navigation odometry requires a valid static body-to-base_link extrinsic");
                    return;
                }
                navigation_body_base_rotation_ = rotation.toRotationMatrix();
                navigation_extrinsic_ready_ = true;
            }
            catch (const tf2::TransformException &error)
            {
                ROS_WARN_THROTTLE(1.0, "Navigation odometry waiting for static extrinsic: %s", error.what());
                return;
            }
        }
        if (measure_group_.imus.empty())
        {
            navigation_odometry_.reset();
            ROS_WARN_THROTTLE(1.0, "Navigation odometry rejected: missing synchronized gyro");
            return;
        }
        // LIO's state.rot is the IMU/body pose; cloudUndistortedBody() maps
        // lidar points into that same IMU frame using offset_R_L_I. Raw gyro
        // therefore needs only the estimated IMU bias, then body->base rotation.
        const auto &imu = measure_group_.imus.back();
        fastlio::NavigationOdomInput input;
        input.stamp = current_time_;
        input.now = ros::Time::now().toSec();
        input.gyro_stamp = imu.timestamp;
        input.local_body_rotation = current_state_.rot.toRotationMatrix();
        input.local_body_position = current_state_.pos;
        input.local_body_velocity = current_state_.vel;
        input.body_gyro = imu.gyro;
        input.body_gyro_bias = current_state_.bg;
        input.body_base_rotation = navigation_body_base_rotation_;
        input.body_base_translation = navigation_body_base_translation_;
        fastlio::NavigationOdomState state;
        const char *reason = nullptr;
        if (!navigation_odometry_.update(input, state, reason))
        {
            ROS_WARN_THROTTLE(1.0, "Navigation odometry rejected: %s", reason);
            return;
        }
        auto odom = eigen2Odometry(state.rotation, state.position,
                                  local_frame_, "base_link", current_time_);
        odom.twist.twist.linear.x = state.linear.x();
        odom.twist.twist.linear.y = state.linear.y();
        odom.twist.twist.linear.z = state.linear.z();
        odom.twist.twist.angular.x = state.angular.x();
        odom.twist.twist.angular.y = state.angular.y();
        odom.twist.twist.angular.z = state.angular.z();
        navigation_odom_pub_.publish(odom);
    }

    void systemReset()
    {
        navigation_odometry_.reset();
        offset_rot_ = Eigen::Matrix3d::Identity();
        offset_pos_ = Eigen::Vector3d::Zero();
        {
            std::lock_guard<std::mutex> lock(shared_date_->main_mutex);
            shared_date_->offset_rot = Eigen::Matrix3d::Identity();
            shared_date_->offset_pos = Eigen::Vector3d::Zero();
            shared_date_->service_success = false;
        }
        lio_builder_->reset();
    }

    void run()
    {
        while (ros::ok())
        {
            local_rate_->sleep();
            ros::spinOnce();
            if (terminate_flag)
                break;
            if (!measure_group_.syncPackage(imu_data_, livox_data_))
                continue;
            if (shared_date_->halt_flag)
                continue;

            if (shared_date_->reset_flag)
            {
                // ROS_INFO("SLAM RESET!");
                systemReset();
                shared_date_->service_mutex.lock();
                shared_date_->reset_flag = false;
                shared_date_->service_mutex.unlock();
            }

            lio_builder_->mapping(measure_group_);
            if (lio_builder_->currentStatus() == fastlio::Status::INITIALIZE)
                continue;
            current_time_ = measure_group_.lidar_time_end;
            current_state_ = lio_builder_->currentState();
            current_cloud_body_ = lio_builder_->cloudUndistortedBody();
            {
                std::lock_guard<std::mutex> lock(shared_date_->main_mutex);
                shared_date_->local_rot = current_state_.rot.toRotationMatrix();
                shared_date_->local_pos = current_state_.pos;
                // Publish an immutable snapshot for the background ICP thread.
                shared_date_->cloud.reset(new fastlio::PointCloudXYZI(*current_cloud_body_));
                shared_date_->cloud_time = current_time_;
                offset_rot_ = shared_date_->offset_rot;
                offset_pos_ = shared_date_->offset_pos;
                shared_date_->pose_updated = true;
            }
            br_.sendTransform(eigen2Transform(
                current_state_.rot.toRotationMatrix(),
                current_state_.pos,
                local_frame_,
                body_frame_,
                current_time_));
            br_.sendTransform(eigen2Transform(
                offset_rot_,
                offset_pos_,
                global_frame_,
                local_frame_,
                current_time_));
            publishOdom(eigen2Odometry(current_state_.rot.toRotationMatrix(),
                                       current_state_.pos,
                                       local_frame_,
                                       body_frame_,
                                       current_time_));
            publishNavigationOdom();
            publishCloud(body_cloud_pub_,
                         pcl2msg(current_cloud_body_,
                                 body_frame_,
                                 current_time_));
            publishCloud(local_cloud_pub_,
                         pcl2msg(lio_builder_->cloudWorld(),
                                 local_frame_,
                                 current_time_));
            publishCloud(body_cloud_org_pub_,
                            pcl2msg(measure_group_.lidar_org,
                                    body_frame_,
                                    current_time_));
            if (publish_map_cloud_)
            {
                if (icp_localizer_->isInitialized())
                {
                    publishCloud(map_cloud_pub_,
                                 pcl2msg(icp_localizer_->getRoughMap(),
                                         global_frame_,
                                         current_time_));
                }
            }
        }

        localizer_thread_->join();
        std::cout << "LOCALIZER NODE IS DOWN!" << std::endl;
    }

private:
    ros::NodeHandle nh_;
    std::string body_frame_;
    std::string local_frame_;
    std::string global_frame_;

    double current_time_;
    bool publish_map_cloud_;
    fastlio::state_ikfom current_state_;

    ImuData imu_data_;
    LivoxData livox_data_;
    MeasureGroup measure_group_;
    std::shared_ptr<SharedData> shared_date_;
    std::shared_ptr<ros::Rate> local_rate_;
    std::shared_ptr<ros::Rate> loop_rate_;
    tf2_ros::Buffer tf_buffer_;
    tf2_ros::TransformListener tf_listener_;
    tf2_ros::TransformBroadcaster &br_;
    fastlio::LioParams lio_params_;
    fastlio::LocalizerParams localizer_params_;
    std::shared_ptr<fastlio::LIOBuilder> lio_builder_;
    std::shared_ptr<fastlio::IcpLocalizer> icp_localizer_;
    LocalizerThread localizer_loop_;
    std::shared_ptr<std::thread> localizer_thread_;

    ros::Subscriber imu_sub_;

    ros::Subscriber livox_sub_;

    ros::Publisher odom_pub_;
    ros::Publisher navigation_odom_pub_;
    fastlio::NavigationOdometry navigation_odometry_;
    bool navigation_extrinsic_ready_ = false;
    Eigen::Matrix3d navigation_body_base_rotation_ = Eigen::Matrix3d::Identity();
    Eigen::Vector3d navigation_body_base_translation_ = Eigen::Vector3d::Zero();

    ros::Publisher body_cloud_pub_;

    ros::Publisher local_cloud_pub_;

    ros::Publisher map_cloud_pub_;
    
    ros::Publisher body_cloud_org_pub_;

    ros::ServiceServer reloc_server_;
    ros::ServiceServer base_reloc_server_;
    ros::ServiceServer coarse_reloc_server_;
    unsigned long request_sequence_ = 0;

    ros::ServiceServer map_convert_server_;

    ros::ServiceServer reloc_check_server_;

    ros::ServiceServer hold_server_;

    ros::ServiceServer start_server_;

    Eigen::Matrix3d offset_rot_ = Eigen::Matrix3d::Identity();

    Eigen::Vector3d offset_pos_ = Eigen::Vector3d::Zero();

    fastlio::PointCloudXYZI::Ptr current_cloud_body_;
};

int main(int argc, char **argv)
{
    ros::init(argc, argv, "localizer_node");
    tf2_ros::TransformBroadcaster br;
    signal(SIGINT, signalHandler);
    std::shared_ptr<SharedData> shared_date = std::make_shared<SharedData>();
    LocalizerROS localizer_ros(br, shared_date);
    localizer_ros.run();
    return 0;
}
