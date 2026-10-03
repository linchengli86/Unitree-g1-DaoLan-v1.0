#include <velocity_smoother_ema/velocity_smoother_ema.hpp>
#include <chrono>
#include <cmath>

namespace
{
double steady_seconds()
{
    return std::chrono::duration<double>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
}
}

VelocitySmootherEma::VelocitySmootherEma(ros::NodeHandle* nh):nh_(*nh)
{
    velocity_smoother_ema::FilterConfig config;
    double cmd_rate;
    nh_.param<double>("/alpha_v", config.alpha_v, 0.4);
    nh_.param<double>("/alpha_w", config.alpha_w, 0.4);
    nh_.param<std::string>("/raw_cmd_topic", raw_cmd_topic, "raw_cmd_vel");
    nh_.param<std::string>("/cmd_topic", cmd_topic, "cmd_vel");
    nh_.param<double>("/cmd_rate", cmd_rate, 30.0);
    nh_.param<double>("/raw_timeout", config.raw_timeout, 1.0 / 3.0);
    nh_.param<double>("/acc_lim_x", config.acc_lim_x, 0.3);
    nh_.param<double>("/acc_lim_theta", config.acc_lim_theta, 0.5);
    if (!std::isfinite(cmd_rate) || cmd_rate <= 0.0)
        throw std::invalid_argument("Invalid smoother cmd_rate");
    config.period = 1.0 / cmd_rate;
    filter_ = velocity_smoother_ema::CommandFilter(config);

    velocity_sub_ = nh_.subscribe(raw_cmd_topic, 1, &VelocitySmootherEma::twist_callback, this);
    velocity_pub_ = nh_.advertise<geometry_msgs::Twist>(cmd_topic, 1, false);
    timer = nh_.createSteadyTimer(ros::WallDuration(config.period), &VelocitySmootherEma::update, this);
}

VelocitySmootherEma::~VelocitySmootherEma()
{
    ros::shutdown();
}

void VelocitySmootherEma::twist_callback(const geometry_msgs::Twist::ConstPtr msg)
{
    const double now = steady_seconds();
    if (!std::isfinite(msg->linear.x) || !std::isfinite(msg->linear.y) ||
        !std::isfinite(msg->linear.z) || !std::isfinite(msg->angular.x) ||
        !std::isfinite(msg->angular.y) || !std::isfinite(msg->angular.z))
    {
        filter_.reject(now);
        velocity_pub_.publish(geometry_msgs::Twist());
        ROS_WARN_THROTTLE(1.0, "Invalid velocity command; dropping raw command and publishing zero");
        return;
    }
    velocity_smoother_ema::PlanarCommand raw;
    raw.x = msg->linear.x;
    raw.y = msg->linear.y;
    raw.wz = msg->angular.z;
    if (!filter_.receive(raw, now))
    {
        velocity_pub_.publish(geometry_msgs::Twist());
        ROS_WARN_THROTTLE(1.0, "Velocity clock discontinuity; motion output reset to zero");
    }
    else if (raw.x == 0.0 && raw.y == 0.0 && raw.wz == 0.0)
        velocity_pub_.publish(geometry_msgs::Twist());
}

void VelocitySmootherEma::update(const ros::SteadyTimerEvent&)
{
    const velocity_smoother_ema::PlanarCommand smooth = filter_.update(steady_seconds());
    geometry_msgs::Twist output;
    output.linear.x = smooth.x;
    output.linear.y = smooth.y;
    output.angular.z = smooth.wz;
    velocity_pub_.publish(output);
}

int main(int argc, char** argv)
{
    ros::init(argc, argv, "velocity_smoother_ema");

    ros::NodeHandle nh;

    try
    {
        VelocitySmootherEma vse(&nh);
        ros::spin();
    }
    catch (const std::exception& ex)
    {
        ROS_FATAL("Velocity smoother failed: %s", ex.what());
        return 1;
    }

    return 0;
}
