#include "localizer/icp_localizer.h"
#include <algorithm>
#include <chrono>
#include <pcl/search/kdtree.h>

namespace
{
    using SteadyClock = std::chrono::steady_clock;
    using Deadline = SteadyClock::time_point;
    using XyziCloud = pcl::PointCloud<pcl::PointXYZI>;
    using NormalCloud = fastlio::PointCloudXYZI;
    using NormalTree = pcl::search::KdTree<fastlio::PointType>;

    bool expired(const Deadline &deadline) { return SteadyClock::now() >= deadline; }

    double elapsed(const Deadline &started)
    {
        return std::chrono::duration<double>(SteadyClock::now() - started).count();
    }

    bool validTransform(const Eigen::Matrix4d &pose)
    {
        if (!pose.allFinite() ||
            (pose.row(3) - Eigen::RowVector4d(0.0, 0.0, 0.0, 1.0)).norm() > 1e-4)
            return false;
        const Eigen::Matrix3d rotation = pose.block<3, 3>(0, 0);
        return std::abs(rotation.determinant() - 1.0) < 0.01 &&
               (rotation.transpose() * rotation - Eigen::Matrix3d::Identity()).norm() < 0.01;
    }

    XyziCloud::Ptr finiteCappedSource(const XyziCloud::Ptr &source)
    {
        XyziCloud::Ptr finite(new XyziCloud);
        if (!source) return finite;
        finite->reserve(source->size());
        for (const auto &point : source->points)
            if (std::isfinite(point.x) && std::isfinite(point.y) &&
                std::isfinite(point.z) && std::isfinite(point.intensity))
                finite->push_back(point);
        if (finite->size() <= fastlio::coarse_policy::kSourceCap) return finite;
        XyziCloud::Ptr capped(new XyziCloud);
        capped->reserve(fastlio::coarse_policy::kSourceCap);
        for (std::size_t i = 0; i < fastlio::coarse_policy::kSourceCap; ++i)
            capped->push_back((*finite)[i * finite->size() / fastlio::coarse_policy::kSourceCap]);
        return capped;
    }

    XyziCloud::Ptr voxelSource(const XyziCloud::Ptr &source, float resolution)
    {
        XyziCloud::Ptr out(new XyziCloud);
        pcl::VoxelGrid<pcl::PointXYZI> filter;
        filter.setLeafSize(resolution, resolution, resolution);
        filter.setInputCloud(source);
        filter.filter(*out);
        return out;
    }

    NormalCloud::Ptr finiteNormals(const NormalCloud::Ptr &source)
    {
        NormalCloud::Ptr out(new NormalCloud);
        if (!source) return out;
        out->reserve(source->size());
        for (const auto &point : source->points)
        {
            const double squared_normal = point.normal_x * point.normal_x +
                point.normal_y * point.normal_y + point.normal_z * point.normal_z;
            if (std::isfinite(point.x) && std::isfinite(point.y) && std::isfinite(point.z) &&
                std::isfinite(point.normal_x) && std::isfinite(point.normal_y) &&
                std::isfinite(point.normal_z) && squared_normal > 1e-6)
                out->push_back(point);
        }
        out->is_dense = true;
        return out;
    }

    // The ICP loop checks this at every iteration.  A running nearest-neighbour
    // iteration/map tree build is not preempted, but expiry can never succeed.
    class DeadlineCriteria : public pcl::registration::DefaultConvergenceCriteria<float>
    {
    public:
        DeadlineCriteria(const int &iterations, const Eigen::Matrix4f &transform,
                         const pcl::Correspondences &correspondences,
                         const Deadline &deadline)
            : pcl::registration::DefaultConvergenceCriteria<float>(iterations, transform, correspondences),
              deadline_(deadline) {}
        bool hasConverged() override
        {
            if (expired(deadline_))
            {
                setConvergenceState(CONVERGENCE_CRITERIA_NO_CORRESPONDENCES);
                return false;
            }
            return pcl::registration::DefaultConvergenceCriteria<float>::hasConverged();
        }
    private:
        const Deadline deadline_;
    };

    class DeadlineIcp : public pcl::IterativeClosestPointWithNormals<fastlio::PointType, fastlio::PointType>
    {
    public:
        explicit DeadlineIcp(const Deadline &deadline)
        {
            convergence_criteria_.reset(new DeadlineCriteria(
                nr_iterations_, transformation_, *correspondences_, deadline));
        }
    };

    fastlio::CoarseReport evaluatePose(const XyziCloud::Ptr &source,
                                      const Eigen::Matrix4d &pose,
                                      NormalTree &tree, const Deadline &deadline)
    {
        fastlio::CoarseReport report;
        report.reason = "invalid_pose";
        if (!validTransform(pose)) return report;
        report.point_count = source->size();
        if (source->size() < fastlio::coarse_policy::kMinimumPoints)
        {
            report.reason = "insufficient_points";
            return report;
        }
        double residual_sum = 0.0;
        std::vector<int> indices(1);
        std::vector<float> squared_distances(1);
        const double max_squared_distance =
            fastlio::coarse_policy::kCorrespondenceDistance *
            fastlio::coarse_policy::kCorrespondenceDistance;
        for (std::size_t i = 0; i < source->size(); ++i)
        {
            if (i % 64 == 0 && expired(deadline))
            {
                report.reason = "time_budget_exceeded";
                return report;
            }
            const auto &point = (*source)[i];
            Eigen::Vector3d transformed = pose.block<3, 3>(0, 0) *
                Eigen::Vector3d(point.x, point.y, point.z) + pose.block<3, 1>(0, 3);
            if (!transformed.allFinite())
            {
                report.reason = "nonfinite_result";
                return report;
            }
            fastlio::PointType query;
            query.x = static_cast<float>(transformed.x());
            query.y = static_cast<float>(transformed.y());
            query.z = static_cast<float>(transformed.z());
            if (tree.nearestKSearch(query, 1, indices, squared_distances) == 1 &&
                std::isfinite(squared_distances[0]) && squared_distances[0] <= max_squared_distance)
            {
                ++report.inlier_count;
                residual_sum += squared_distances[0];
            }
        }
        report.inlier_ratio = static_cast<double>(report.inlier_count) / report.point_count;
        if (report.inlier_count > 0) report.score = residual_sum / report.inlier_count;
        if (report.inlier_count < fastlio::coarse_policy::kMinimumPoints)
            report.reason = "insufficient_inliers";
        else if (report.inlier_ratio < fastlio::coarse_policy::kMinimumOverlap)
            report.reason = "insufficient_overlap";
        else if (!std::isfinite(report.score) || report.score < 0.0 ||
                 report.score > fastlio::coarse_policy::kMaximumRmse * fastlio::coarse_policy::kMaximumRmse)
            report.reason = "high_residual";
        else
        {
            report.status = fastlio::coarse_policy::quality(report);
            report.reason = report.status ? "quality_passed" : "invalid_quality_statistics";
        }
        return report;
    }

    bool boundedFromSeed(const Eigen::Matrix4d &pose, const Eigen::Matrix4d &seed)
    {
        if (!validTransform(pose)) return false;
        const Eigen::Vector3d delta = pose.block<3, 1>(0, 3) - seed.block<3, 1>(0, 3);
        Eigen::Matrix3d relative = seed.block<3, 3>(0, 0).transpose() * pose.block<3, 3>(0, 0);
        const Eigen::Vector3d rpy = rotate2rpy(relative);
        return fastlio::coarse_policy::boundedPose(delta.x(), delta.y(), delta.z(),
                                                  rpy.x(), rpy.y(), rpy.z());
    }

    bool independentPoses(const Eigen::Matrix4d &first, const Eigen::Matrix4d &second)
    {
        const Eigen::Vector2d delta = first.block<2, 1>(0, 3) - second.block<2, 1>(0, 3);
        Eigen::Matrix3d first_rotation = first.block<3, 3>(0, 0);
        Eigen::Matrix3d second_rotation = second.block<3, 3>(0, 0);
        return fastlio::coarse_policy::independent(delta.x(), delta.y(),
            rotate2rpy(first_rotation).z() - rotate2rpy(second_rotation).z());
    }
}

namespace fastlio
{
    /**
     * @brief 初始化ICP定位器，加载点云地图并设置粗定位和精定位的目标点云
     * 
     * 该函数实现双层ICP定位策略：
     * 1. 粗定位（rough_map_）：使用粗体素滤波的地图，快速获得初始位姿估计
     * 2. 精定位（refine_map_）：使用精细体素滤波的地图，获得高精度位姿
     * 
     * @param pcd_path 点云地图文件路径（.pcd格式）
     * @param with_norm 是否输入点云已包含法向量信息
     *                  - true: 直接加载包含法向量的点云（PointXYZINormal）
     *                  - false: 加载XYZI点云，通过addNorm()计算法向量
     * 
     * @note 函数执行流程：
     *       1. 检查路径是否已加载，避免重复初始化
     *       2. 根据with_norm参数选择加载方式：
     *          - 不含法向量：加载XYZI → 体素滤波 → 计算法向量 → 精定位地图
     *          - 含法向量：直接加载为精定位地图
     *       3. 从精定位地图生成粗定位地图：复制点云 → 粗体素滤波 → 计算法向量
     *       4. 配置两个ICP算法的目标点云和最大迭代次数
     * 
     * @warning 确保pcd_path文件存在且格式正确，否则会导致读取失败
     */
    void IcpLocalizer::init(const std::string &pcd_path, bool with_norm)
    {
        // 避免重复加载相同的点云地图
        if (!pcd_path_.empty() && pcd_path_ == pcd_path)
            return;
        
        pcl::PCDReader reader;
        pcd_path_ = pcd_path;
        
        if (!with_norm)
        {
            // 输入点云不含法向量，需要加载XYZI点云并计算法向量
            pcl::PointCloud<pcl::PointXYZI>::Ptr cloud(new pcl::PointCloud<pcl::PointXYZI>);
            reader.read(pcd_path, *cloud);
            
            // 对原始点云进行精细体素滤波，用于精定位
            voxel_refine_filter_.setInputCloud(cloud);
            voxel_refine_filter_.filter(*cloud);
            refine_map_ = addNorm(cloud);  // 计算法向量并转换为PointXYZINormal
        }
        else
        {
            // 输入点云已包含法向量，直接加载为精定位地图
            refine_map_.reset(new PointCloudXYZI);
            reader.read(pcd_path, *refine_map_);
        }
        
        // 从精定位地图生成粗定位地图
        pcl::PointCloud<pcl::PointXYZI>::Ptr point_rough(new pcl::PointCloud<pcl::PointXYZI>);
        pcl::PointCloud<pcl::PointXYZI>::Ptr filterd_point_rough(new pcl::PointCloud<pcl::PointXYZI>);
        
        // 复制精定位地图的空间坐标和强度信息（不含法向量）
        pcl::copyPointCloud(*refine_map_, *point_rough);
        
        // 对复制的点云进行粗体素滤波，降低点云密度以提高粗定位速度
        voxel_rough_filter_.setInputCloud(point_rough);
        voxel_rough_filter_.filter(*filterd_point_rough);
        rough_map_ = addNorm(filterd_point_rough);  // 计算法向量并转换为PointXYZINormal
        
        // 配置粗定位ICP算法
        icp_rough_.setMaximumIterations(rough_iter_);
        icp_rough_.setInputTarget(rough_map_);
        
        // 配置精定位ICP算法
        icp_refine_.setMaximumIterations(refine_iter_);
        icp_refine_.setInputTarget(refine_map_);
        
        initialized_ = true;  // 标记初始化完成
    }

    /**
     * @brief 执行双层ICP点云配准，获得精确的位姿变换矩阵
     * 
     * 该函数实现分层配准策略：
     * 1. 粗配准：使用低分辨率点云进行快速初始对齐
     * 2. 精配准：基于粗配准结果进行高精度细化对齐
     * 
     * @param source 输入的源点云（待配准点云）
     * @param init_guess 初始位姿估计（4x4变换矩阵）
     * @return Eigen::Matrix4d 最终配准结果的变换矩阵
     *                         - 成功：返回精配准的最终变换矩阵
     *                         - 失败：返回零矩阵（Eigen::Matrix4d::Zero()）
     * 
     * @note 执行流程：
     *       1. 对源点云进行两种不同分辨率的体素滤波
     *       2. 为滤波后的点云计算法向量
     *       3. 粗配准：使用粗分辨率点云和初始估计进行ICP对齐
     *       4. 收敛性检查：验证粗配准是否收敛
     *       5. 精配准：使用细分辨率点云和粗配准结果进行ICP对齐
     *       6. 质量评估：检查精配准收敛性和适应度得分
     * 
     * @warning 配准可能失败的情况：
     *          - 粗配准未收敛
     *          - 精配准未收敛  
     *          - 最终适应度得分超过阈值（score_ > thresh_）
     */
    Eigen::Matrix4d IcpLocalizer::align(pcl::PointCloud<pcl::PointXYZI>::Ptr source, Eigen::Matrix4d init_guess)
    {
        success_ = false;  // 初始化配准状态为失败
        Eigen::Vector3d xyz = init_guess.block<3, 1>(0, 3);  // 提取初始位置（暂未使用）

        // === 第一步：源点云预处理 ===
        pcl::PointCloud<pcl::PointXYZI>::Ptr rough_source(new pcl::PointCloud<pcl::PointXYZI>);
        pcl::PointCloud<pcl::PointXYZI>::Ptr refine_source(new pcl::PointCloud<pcl::PointXYZI>);

        // 对源点云进行粗体素滤波，用于快速粗配准
        voxel_rough_filter_.setInputCloud(source);
        voxel_rough_filter_.filter(*rough_source);
        
        // 对源点云进行精细体素滤波，用于高精度精配准
        voxel_refine_filter_.setInputCloud(source);
        voxel_refine_filter_.filter(*refine_source);

        // 为滤波后的点云计算法向量，提升配准精度
        PointCloudXYZI::Ptr rough_source_norm = addNorm(rough_source);
        PointCloudXYZI::Ptr refine_source_norm = addNorm(refine_source);
        PointCloudXYZI::Ptr align_point(new PointCloudXYZI);  // 配准结果存储点云
        
        // === 第二步：粗配准阶段 ===
        icp_rough_.setInputSource(rough_source_norm);
        icp_rough_.align(*align_point, init_guess.cast<float>());

        score_ = icp_rough_.getFitnessScore();  // 获取粗配准适应度得分
        if (!icp_rough_.hasConverged())         // 检查粗配准是否收敛
            return Eigen::Matrix4d::Zero();

        // === 第三步：精配准阶段 ===
        // 使用粗配准结果作为精配准的初始估计
        icp_refine_.setInputSource(refine_source_norm);
        icp_refine_.align(*align_point, icp_rough_.getFinalTransformation());
        
        score_ = icp_refine_.getFitnessScore();  // 获取精配准适应度得分
        
        // === 第四步：结果验证 ===
        if (!icp_refine_.hasConverged())  // 检查精配准是否收敛
            return Eigen::Matrix4d::Zero();
        if (score_ > thresh_)             // 检查适应度得分是否满足要求
            return Eigen::Matrix4d::Zero();
            
        success_ = true;  // 标记配准成功
        return icp_refine_.getFinalTransformation().cast<double>();  // 返回最终变换矩阵
    }

    PointCloudXYZI::Ptr IcpLocalizer::addNorm(pcl::PointCloud<pcl::PointXYZI>::Ptr cloud)
    {
        pcl::PointCloud<pcl::Normal>::Ptr normals(new pcl::PointCloud<pcl::Normal>);
        pcl::search::KdTree<pcl::PointXYZI>::Ptr searchTree(new pcl::search::KdTree<pcl::PointXYZI>);
        searchTree->setInputCloud(cloud);

        pcl::NormalEstimation<pcl::PointXYZI, pcl::Normal> normalEstimator;
        normalEstimator.setInputCloud(cloud);
        normalEstimator.setSearchMethod(searchTree);
        normalEstimator.setKSearch(15);
        normalEstimator.compute(*normals);
        PointCloudXYZI::Ptr out(new PointCloudXYZI);
        pcl::concatenateFields(*cloud, *normals, *out);
        return out;
    }

    void IcpLocalizer::writePCDToFile(const std::string &path, bool detail)
    {
        if (!initialized_)
            return;
        pcl::PCDWriter writer;
        writer.writeBinaryCompressed(path, detail ? *refine_map_ : *rough_map_);
    }

    void IcpLocalizer::setParams(double refine_resolution, double rough_resolution, int refine_iter, int rough_iter, double thresh)
    {
        refine_resolution_ = refine_resolution;
        rough_resolution_ = rough_resolution;
        refine_iter_ = refine_iter;
        rough_iter_ = rough_iter;
        thresh_ = thresh;
    }

    void IcpLocalizer::setSearchParams(double xy_offset, int yaw_offset, double yaw_res){
        xy_offset_ = xy_offset;
        yaw_offset_ = yaw_offset;
        yaw_resolution_ = yaw_res;
    }

    /**
     * @brief 多候选位姿同步ICP配准，通过搜索多个初始位姿提高配准成功率
     * 
     * 该函数实现基于多初始候选位姿的鲁棒ICP配准策略：
     * 1. 候选位姿生成：在初始位姿周围生成多个候选位姿
     * 2. 并行粗配准：对所有候选位姿进行粗配准，选择最佳结果
     * 3. 精细配准：基于最佳粗配准结果进行高精度配准
     * 
     * @param source 输入的源点云（待配准点云）
     * @param init_guess 初始位姿估计（4x4变换矩阵）
     * @return Eigen::Matrix4d 最终配准结果的变换矩阵
     *                         - 成功：返回精配准的最终变换矩阵
     *                         - 失败：返回零矩阵（Eigen::Matrix4d::Zero()）
     * 
     * @note 搜索策略：
     *       - X/Y方向：在初始位置±xy_offset_范围内搜索（3×3网格）
     *       - Yaw角度：在初始角度±yaw_offset_×yaw_resolution_范围内搜索
     *       - Roll/Pitch：保持初始值不变
     *       - 总候选数量：3×3×(2×yaw_offset_+1)
     * 
     * @details 执行流程：
     *          1. 解析初始位姿的位置和姿态（RPY）
     *          2. 生成搜索网格内的所有候选位姿
     *          3. 对源点云进行预处理（滤波+法向量计算）
     *          4. 遍历所有候选位姿进行粗配准
     *          5. 选择适应度得分最佳的粗配准结果
     *          6. 基于最佳粗配准结果进行精配准
     * 
     * @warning 配准可能失败的情况：
     *          - 所有候选位姿的粗配准均未收敛
     *          - 所有粗配准适应度得分均超过2×thresh_
     *          - 精配准未收敛
     *          - 精配准适应度得分超过thresh_
     * 
     * @performance 计算复杂度与候选位姿数量成正比，适用于初始位姿不确定性较大的场景
     */
    Eigen::Matrix4d IcpLocalizer::multi_align_coarse(XyziCloud::Ptr source,
                                                   Eigen::Matrix4d init_guess)
    {
        success_ = false;
        score_ = 10.0;
        coarse_report_ = CoarseReport();
        const Deadline started = SteadyClock::now();
        const Deadline deadline = started + std::chrono::seconds(20);
        auto fail = [&](const std::string &reason) -> Eigen::Matrix4d {
            coarse_report_.status = false;
            coarse_report_.reason = reason;
            coarse_report_.elapsed_seconds = elapsed(started);
            return Eigen::Matrix4d::Zero();
        };
        if (!initialized_ || !rough_map_ || !refine_map_)
            return fail("map_not_initialized");
        if (!validTransform(init_guess)) return fail("invalid_seed_transform");
        XyziCloud::Ptr finite = finiteCappedSource(source);
        coarse_report_.point_count = finite->size();
        if (finite->size() < coarse_policy::kMinimumPoints)
            return fail("insufficient_finite_points");
        XyziCloud::Ptr rough_source = voxelSource(finite, 0.5f);
        XyziCloud::Ptr refine_source = voxelSource(finite, 0.2f);
        coarse_report_.point_count = refine_source->size();
        if (rough_source->size() < 15 || refine_source->size() < coarse_policy::kMinimumPoints)
            return fail("insufficient_voxel_points");
        PointCloudXYZI::Ptr rough_source_norm = finiteNormals(addNorm(rough_source));
        PointCloudXYZI::Ptr refine_source_norm = finiteNormals(addNorm(refine_source));
        if (rough_source_norm->size() < 15 || refine_source_norm->size() < coarse_policy::kMinimumPoints)
            return fail("insufficient_normal_points");
        PointCloudXYZI::Ptr rough_target = finiteNormals(rough_map_);
        PointCloudXYZI::Ptr refine_target = finiteNormals(refine_map_);
        if (rough_target->size() < 15 || refine_target->size() < coarse_policy::kMinimumPoints)
            return fail("insufficient_map_points");
        NormalTree::Ptr rough_tree(new NormalTree), refine_tree(new NormalTree);
        rough_tree->setInputCloud(rough_target);
        refine_tree->setInputCloud(refine_target);
        if (expired(deadline)) return fail("time_budget_exceeded");

        // Dedicated registrations leave the legacy ICP search settings and
        // tracking source/targets entirely untouched.
        DeadlineIcp rough_icp(deadline), refine_icp(deadline);
        rough_icp.setMaximumIterations(10);
        rough_icp.setMaxCorrespondenceDistance(0.75);
        rough_icp.setTransformationEpsilon(1e-6);
        rough_icp.setEuclideanFitnessEpsilon(1e-6);
        rough_icp.setInputTarget(rough_target);
        rough_icp.setSearchMethodTarget(rough_tree, true);
        rough_icp.setInputSource(rough_source_norm);
        refine_icp.setMaximumIterations(6);
        refine_icp.setMaxCorrespondenceDistance(0.35);
        refine_icp.setTransformationEpsilon(1e-7);
        refine_icp.setEuclideanFitnessEpsilon(1e-7);
        refine_icp.setInputTarget(refine_target);
        refine_icp.setSearchMethodTarget(refine_tree, true);
        refine_icp.setInputSource(refine_source_norm);
        PointCloudXYZI aligned;
        struct Candidate
        {
            EIGEN_MAKE_ALIGNED_OPERATOR_NEW
            Eigen::Matrix4d pose;
            CoarseReport report;
        };
        using CandidateList = std::vector<Candidate, Eigen::aligned_allocator<Candidate>>;
        CandidateList rough_results;
        for (const auto &offset : coarse_policy::candidates())
        {
            if (expired(deadline)) return fail("time_budget_exceeded");
            Eigen::Matrix4d candidate = init_guess;
            candidate(0, 3) += offset.x;
            candidate(1, 3) += offset.y;
            // World-Z pre-multiplication preserves the supplied body tilt;
            // reconstructing roll*pitch*yaw here would use the wrong convention.
            candidate.block<3, 3>(0, 0) =
                Eigen::AngleAxisd(offset.yaw, Eigen::Vector3d::UnitZ()).toRotationMatrix() *
                init_guess.block<3, 3>(0, 0);
            ++coarse_report_.candidate_count;
            rough_icp.align(aligned, candidate.cast<float>());
            if (expired(deadline)) return fail("time_budget_exceeded");
            if (!rough_icp.hasConverged()) continue;
            const Eigen::Matrix4d transform = rough_icp.getFinalTransformation().cast<double>();
            if (!boundedFromSeed(transform, init_guess)) continue;
            CoarseReport quality = evaluatePose(refine_source, transform, *refine_tree, deadline);
            if (expired(deadline)) return fail("time_budget_exceeded");
            // This only proposes a fine ICP seed, never accepts a pose.  The
            // complete, stricter quality policy is applied after refinement.
            if (quality.inlier_count < coarse_policy::kMinimumPoints ||
                quality.inlier_ratio < 0.35 || !std::isfinite(quality.score) ||
                quality.score < 0.0 || quality.score > 0.0625)
                continue;
            rough_results.push_back({transform, quality});
        }
        if (rough_results.empty()) return fail("no_valid_coarse_alignment");
        auto ranked = [](const Candidate &first, const Candidate &second) {
            if (first.report.score != second.report.score)
                return first.report.score < second.report.score;
            return first.report.inlier_ratio > second.report.inlier_ratio;
        };
        std::sort(rough_results.begin(), rough_results.end(), ranked);
        CandidateList selected;
        for (const auto &candidate : rough_results)
        {
            bool distinct = true;
            for (const auto &retained : selected)
                if (!independentPoses(candidate.pose, retained.pose)) { distinct = false; break; }
            if (distinct) selected.push_back(candidate);
            if (selected.size() == coarse_policy::kMaximumRefinements) break;
        }
        CandidateList fine_results;
        for (const auto &candidate : selected)
        {
            if (expired(deadline)) return fail("time_budget_exceeded");
            ++coarse_report_.refined_count;
            refine_icp.align(aligned, candidate.pose.cast<float>());
            if (expired(deadline)) return fail("time_budget_exceeded");
            if (!refine_icp.hasConverged()) continue;
            const Eigen::Matrix4d transform = refine_icp.getFinalTransformation().cast<double>();
            if (!boundedFromSeed(transform, init_guess) ||
                (transform.block<2, 1>(0, 3) - candidate.pose.block<2, 1>(0, 3)).norm() >
                    coarse_policy::kMaximumRefinementDrift)
                continue;
            CoarseReport quality = evaluatePose(refine_source, transform, *refine_tree, deadline);
            if (expired(deadline)) return fail("time_budget_exceeded");
            if (!quality.status) continue;
            // Different initial guesses often converge to the same answer.  They
            // must not count as an ambiguous second location.
            bool distinct = true;
            for (auto &retained : fine_results)
                if (!independentPoses(transform, retained.pose))
                {
                    distinct = false;
                    if (ranked({transform, quality}, retained)) retained = {transform, quality};
                    break;
                }
            if (distinct) fine_results.push_back({transform, quality});
        }
        coarse_report_.distinct_solution_count = static_cast<int>(fine_results.size());
        if (fine_results.empty()) return fail("no_valid_refined_alignment");
        std::sort(fine_results.begin(), fine_results.end(), ranked);
        const int candidate_count = coarse_report_.candidate_count;
        const int refined_count = coarse_report_.refined_count;
        const int distinct_count = coarse_report_.distinct_solution_count;
        coarse_report_ = fine_results.front().report;
        coarse_report_.candidate_count = candidate_count;
        coarse_report_.refined_count = refined_count;
        coarse_report_.distinct_solution_count = distinct_count;
        if (fine_results.size() > 1)
        {
            const CoarseReport &second = fine_results[1].report;
            coarse_report_.best_second_ratio = second.score > 0.0 ?
                coarse_report_.score / second.score : 1.0;
            if (!coarse_policy::clearlyBetter(coarse_report_, second))
                return fail("ambiguous_distinct_solutions");
        }
        if (expired(deadline)) return fail("time_budget_exceeded");
        coarse_report_.status = true;
        coarse_report_.reason = "accepted";
        coarse_report_.elapsed_seconds = elapsed(started);
        score_ = coarse_report_.score;
        success_ = true;
        return fine_results.front().pose;
    }

    CoarseReport IcpLocalizer::verifyCoarseAlignment(XyziCloud::Ptr source,
                                                   const Eigen::Matrix4d &reference_pose)
    {
        const Deadline started = SteadyClock::now();
        const Deadline deadline = started + std::chrono::seconds(3);
        CoarseReport report;
        if (!initialized_ || !refine_map_)
        {
            report.reason = "map_not_initialized";
            return report;
        }
        XyziCloud::Ptr finite = finiteCappedSource(source);
        if (finite->size() < coarse_policy::kMinimumPoints)
        {
            report.reason = "insufficient_finite_points";
            report.point_count = finite->size();
            return report;
        }
        XyziCloud::Ptr filtered = voxelSource(finite, 0.2f);
        PointCloudXYZI::Ptr target = finiteNormals(refine_map_);
        if (target->size() < coarse_policy::kMinimumPoints)
        {
            report.reason = "insufficient_map_points";
            return report;
        }
        NormalTree tree;
        tree.setInputCloud(target);
        report = evaluatePose(filtered, reference_pose, tree, deadline);
        report.elapsed_seconds = elapsed(started);
        if (expired(deadline))
        {
            report.status = false;
            report.reason = "time_budget_exceeded";
        }
        else if (report.status) report.reason = "fresh_scan_verified";
        return report;
    }

    Eigen::Matrix4d IcpLocalizer::multi_align_sync(pcl::PointCloud<pcl::PointXYZI>::Ptr source, Eigen::Matrix4d init_guess)
    {
        success_ = false;  // 初始化配准状态为失败
        
        // === 第一步：解析初始位姿信息 ===
        Eigen::Vector3d xyz = init_guess.block<3, 1>(0, 3);        // 提取初始位置
        Eigen::Matrix3d rotation = init_guess.block<3, 3>(0, 0);   // 提取初始旋转矩阵
        Eigen::Vector3d rpy = rotate2rpy(rotation);                // 转换为Roll-Pitch-Yaw角度
        std::cout << "[ICP] Initial guess: pos=(" << xyz.transpose() << "), rpy=(" << rpy.transpose() << ")" << std::endl;

        // 预计算Roll和Pitch的角轴表示（保持不变）
        Eigen::AngleAxisf rollAngle(rpy(0), Eigen::Vector3f::UnitX());
        Eigen::AngleAxisf pitchAngle(rpy(1), Eigen::Vector3f::UnitY());
        
        // === 第二步：生成候选位姿集合 ===
        std::vector<Eigen::Matrix4f> candidates;
        Eigen::Matrix4f temp_pose;
        
        // 三重循环生成搜索网格内的所有候选位姿
        for (int i = -1; i <= 1; i++)           // X方向：-xy_offset_, 0, +xy_offset_
        {
            for (int j = -1; j <= 1; j++)       // Y方向：-xy_offset_, 0, +xy_offset_
            {
                for (int k = -yaw_offset_; k <= yaw_offset_; k++)  // Yaw方向：角度步进搜索
                {
                    // 构造候选位置（Z坐标保持不变）
                    Eigen::Vector3f pos(xyz(0) + i * xy_offset_, xyz(1) + j * xy_offset_, xyz(2));
                    
                    // 构造候选Yaw角度
                    Eigen::AngleAxisf yawAngle(rpy(2) + k * yaw_resolution_, Eigen::Vector3f::UnitZ());
                    
                    // 组装4x4变换矩阵
                    temp_pose.setIdentity();
                    temp_pose.block<3, 3>(0, 0) = (rollAngle * pitchAngle * yawAngle).toRotationMatrix();
                    temp_pose.block<3, 1>(0, 3) = pos;
                    candidates.push_back(temp_pose);
                }
            }
        }
        std::cout << "[ICP] Candidate poses: " << candidates.size() << std::endl;
        // === 第三步：源点云预处理 ===
        pcl::PointCloud<pcl::PointXYZI>::Ptr rough_source(new pcl::PointCloud<pcl::PointXYZI>);
        pcl::PointCloud<pcl::PointXYZI>::Ptr refine_source(new pcl::PointCloud<pcl::PointXYZI>);

        // 分别进行粗滤波和精滤波
        voxel_rough_filter_.setInputCloud(source);
        voxel_rough_filter_.filter(*rough_source);
        voxel_refine_filter_.setInputCloud(source);
        voxel_refine_filter_.filter(*refine_source);

        // 为滤波后的点云计算法向量
        PointCloudXYZI::Ptr rough_source_norm = addNorm(rough_source);
        PointCloudXYZI::Ptr refine_source_norm = addNorm(refine_source);
        PointCloudXYZI::Ptr align_point(new PointCloudXYZI);

        // === 第四步：多候选位姿粗配准 ===
        Eigen::Matrix4f best_rough_transform;
        double best_rough_score = 10.0;  // 初始化为较大值
        bool rough_converge = false;
        // ICP may report a low score after drifting into a geometrically similar
        // corridor far away from the supplied seed.  Keep the result inside the
        // candidate search envelope, with a small allowance for refinement.
        const double max_xy_drift = std::sqrt(2.0) * xy_offset_ + 0.25;
        const double max_final_xy_drift = 0.20;
        
        // 遍历所有候选位姿进行粗配准
        for (Eigen::Matrix4f &init_pose : candidates)
        {
            icp_rough_.setInputSource(rough_source_norm);
            icp_rough_.align(*align_point, init_pose);
            
            // 检查粗配准收敛性
            if (!icp_rough_.hasConverged())
                continue;
                
            double rough_score = icp_rough_.getFitnessScore();

            const Eigen::Matrix4f rough_transform = icp_rough_.getFinalTransformation();
            const double rough_xy_drift =
                (rough_transform.block<2, 1>(0, 3).cast<double>() - xyz.head<2>()).norm();
            const double rough_z_drift = std::abs(
                static_cast<double>(rough_transform(2, 3)) - xyz.z());
            Eigen::Matrix3d rough_relative_rotation =
                rotation.transpose() * rough_transform.block<3, 3>(0, 0).cast<double>();
            Eigen::Vector3d rough_relative_rpy = rotate2rpy(rough_relative_rotation);
            if (rough_xy_drift > max_xy_drift || rough_z_drift > 0.5 ||
                std::abs(rough_relative_rpy.x()) > 0.35 ||
                std::abs(rough_relative_rpy.y()) > 0.35)
                continue;
            
            // 过滤适应度得分过高的结果
            if (rough_score > 2 * thresh_)
                continue;
                
            // 更新最佳粗配准结果
            if (rough_score < best_rough_score)
            {
                best_rough_score = rough_score;
                rough_converge = true;
                best_rough_transform = rough_transform;
            }
        }

        // 检查是否找到有效的粗配准结果
        if (!rough_converge) {
            std::cout << "[ICP] No valid rough alignment found." << std::endl;
            return Eigen::Matrix4d::Zero();
        }
            std::cout << "[ICP] Best rough score: " << best_rough_score << std::endl;

        icp_refine_.setInputSource(refine_source_norm);
        icp_refine_.align(*align_point, best_rough_transform);
        score_ = icp_refine_.getFitnessScore();

        std::cout << "[ICP] Refine score: " << score_ << std::endl;

        if (!icp_refine_.hasConverged()) {
            std::cout << "[ICP] Refine alignment failed to converge." << std::endl;
            return Eigen::Matrix4d::Zero();
        }
        if (score_ > thresh_) {
            std::cout << "[ICP] Refine alignment score too high." << std::endl;
            return Eigen::Matrix4d::Zero();
        }

        const Eigen::Matrix4d final_transform =
            icp_refine_.getFinalTransformation().cast<double>();
        const double final_xy_drift =
            (final_transform.block<2, 1>(0, 3) - xyz.head<2>()).norm();
        const double final_z_drift =
            std::abs(final_transform(2, 3) - xyz.z());
        Eigen::Matrix3d final_relative_rotation =
            rotation.transpose() * final_transform.block<3, 3>(0, 0);
        Eigen::Vector3d final_relative_rpy = rotate2rpy(final_relative_rotation);
        Eigen::Matrix3d final_rotation = final_transform.block<3, 3>(0, 0);
        Eigen::Vector3d final_rpy = rotate2rpy(final_rotation);
        std::cout << "[ICP] Final pose: pos=("
                  << final_transform(0, 3) << " "
                  << final_transform(1, 3) << " "
                  << final_transform(2, 3) << "), rpy=("
                  << final_rpy.x() << " " << final_rpy.y() << " "
                  << final_rpy.z() << ")" << std::endl;
        std::cout << "[ICP] Final XY drift from seed: " << final_xy_drift
                  << " m (final limit " << max_final_xy_drift << " m)" << std::endl;
        std::cout << "[ICP] Final Z drift: " << final_z_drift
                  << " m, relative roll/pitch: " << final_relative_rpy.x()
                  << "/" << final_relative_rpy.y() << " rad" << std::endl;
        if (final_xy_drift > max_final_xy_drift || final_z_drift > 0.5 ||
            std::abs(final_relative_rpy.x()) > 0.35 ||
            std::abs(final_relative_rpy.y()) > 0.35) {
            std::cout << "[ICP] Refine result violates the seed pose bounds." << std::endl;
            return Eigen::Matrix4d::Zero();
        }

        std::cout << "[ICP] Alignment SUCCESS." << std::endl;
        success_ = true;
        return final_transform;
    }
}
