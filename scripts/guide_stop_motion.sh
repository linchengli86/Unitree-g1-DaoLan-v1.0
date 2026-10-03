#!/usr/bin/env bash
# ROS setup reads optional variables such as ROS_DISTRO before assigning them.
# A web-launched process does not inherit an interactive ROS shell.
set +u
source /opt/ros/noetic/setup.bash || exit 1
source "$(dirname "$0")/../G1Nav2D/devel/setup.bash" || exit 1
set -u

# A stop request still cancels the planner if the controller is offline.
controller_ok=0
if timeout 5 rosservice call /unitree_emergency_stop '{}' >/dev/null 2>&1; then
  controller_ok=1
fi
if timeout 5 rosservice call /unitree_motion_enable 'data: false' >/dev/null 2>&1; then
  controller_ok=1
fi
timeout 5 rostopic pub -1 /move_base/cancel actionlib_msgs/GoalID \
  "{stamp: {secs: 0, nsecs: 0}, id: ''}" >/dev/null 2>&1 || true
if [[ "$controller_ok" == "1" ]]; then
  echo '安全控制器已确认停止；导航目标取消指令已发送'
else
  echo '未收到安全控制器的停止确认；请使用遥控器急停并检查 PC2' >&2
  exit 1
fi
