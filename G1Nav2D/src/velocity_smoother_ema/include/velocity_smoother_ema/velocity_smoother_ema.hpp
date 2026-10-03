#ifndef VELOCITY_SMOOTHER_EMA_H
#define VELOCITY_SMOOTHER_EMA_H


#include <ros/ros.h>
#include <geometry_msgs/Twist.h>
#include <velocity_smoother_ema/command_filter.hpp>

class VelocitySmootherEma
{
    public:
        VelocitySmootherEma(ros::NodeHandle* nh);
        ~VelocitySmootherEma();
        void twist_callback(const geometry_msgs::Twist::ConstPtr msg);
        void update(const ros::SteadyTimerEvent&);

    private:
        ros::NodeHandle nh_;
        ros::Subscriber velocity_sub_;
        ros::Publisher velocity_pub_;
        ros::SteadyTimer timer;
        std::string raw_cmd_topic = "raw_cmd_topic";
        std::string cmd_topic = "cmd_topic";
        velocity_smoother_ema::CommandFilter filter_;
};

#endif
