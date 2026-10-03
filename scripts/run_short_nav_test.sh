#!/usr/bin/env bash
set -euo pipefail

source /opt/ros/noetic/setup.bash
source /home/unitree/robot/DaoLan/G1Nav2D/devel/setup.bash

CUTOFF_PID=""

cancel_goal() {
  rostopic pub -1 /move_base/cancel actionlib_msgs/GoalID \
    "{stamp: {secs: 0, nsecs: 0}, id: ''}" >/dev/null 2>&1 || true
}

safe_stop() {
  rosservice call /unitree_motion_enable "data: false" >/dev/null 2>&1 || true
  cancel_goal
  if [[ -n "${CUTOFF_PID}" ]]; then
    kill "${CUTOFF_PID}" >/dev/null 2>&1 || true
  fi
}

emergency_stop() {
  rosservice call /unitree_emergency_stop "{}" >/dev/null 2>&1 || true
  cancel_goal
}

trap safe_stop EXIT
trap 'emergency_stop; exit 130' INT TERM

echo "[1/4] 禁用运动并清除旧目标"
rosservice call /unitree_motion_enable "data: false"
cancel_goal

echo "[2/4] 在机器人前方设置 0.6 m 相对目标（此时仍禁用运动）"
rostopic pub -1 /move_base_simple/goal geometry_msgs/PoseStamped \
  "{header: {frame_id: 'base_link'}, pose: {position: {x: 0.60, y: 0.0, z: 0.0}, orientation: {x: 0.0, y: 0.0, z: 0.0, w: 1.0}}}" >/dev/null

echo "[3/4] 等待规划器给出非零速度"
/usr/bin/python3 - <<'PY'
import sys
import time

import rospy
from geometry_msgs.msg import Twist

rospy.init_node("short_nav_preflight", anonymous=True, disable_signals=True)
deadline = time.monotonic() + 6.0
while time.monotonic() < deadline and not rospy.is_shutdown():
    try:
        msg = rospy.wait_for_message("/cmd_vel", Twist, timeout=1.0)
    except rospy.ROSException:
        continue
    if max(abs(msg.linear.x), abs(msg.linear.y), abs(msg.angular.z)) > 0.005:
        print(
            "规划有效: vx={:.3f}, vy={:.3f}, wz={:.3f}".format(
                msg.linear.x, msg.linear.y, msg.angular.z
            )
        )
        sys.exit(0)
print("未检测到有效规划速度，拒绝解锁", file=sys.stderr)
sys.exit(1)
PY

echo "[4/4] 解锁并开始短距离测试（10 秒正常停止，12 秒硬截止）"
nohup bash -lc '
  sleep 12
  source /opt/ros/noetic/setup.bash
  source /home/unitree/robot/DaoLan/G1Nav2D/devel/setup.bash
  rosservice call /unitree_emergency_stop "{}" >/dev/null 2>&1 || true
  rostopic pub -1 /move_base/cancel actionlib_msgs/GoalID \
    "{stamp: {secs: 0, nsecs: 0}, id: \"\"}" >/dev/null 2>&1 || true
' >/tmp/daolan_nav_cutoff.log 2>&1 </dev/null &
CUTOFF_PID=$!

rosservice call /unitree_motion_enable "data: true"
sleep 10
echo "短距离测试结束，正在禁用运动"
