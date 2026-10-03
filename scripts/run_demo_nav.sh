#!/usr/bin/env bash
set -euo pipefail

source /opt/ros/noetic/setup.bash
source /home/unitree/robot/DaoLan/G1Nav2D/devel/setup.bash

DISTANCE="${1:-2.0}"
if [[ ! "${DISTANCE}" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  echo "距离必须是数字（单位：米）" >&2
  exit 2
fi

/usr/bin/python3 - "${DISTANCE}" <<'PY'
import sys
d = float(sys.argv[1])
if not 0.4 <= d <= 3.0:
    raise SystemExit("演示距离必须在 0.4–3.0 m 之间")
PY

NORMAL_TIMEOUT=$(/usr/bin/python3 - "${DISTANCE}" <<'PY'
import math
import sys
print(max(15, int(math.ceil(float(sys.argv[1]) / 0.12)) + 5))
PY
)
HARD_TIMEOUT=$((NORMAL_TIMEOUT + 3))
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

echo "[2/4] 设置机器人正前方 ${DISTANCE} m 演示目标"
rostopic pub -1 /move_base_simple/goal geometry_msgs/PoseStamped \
  "{header: {frame_id: 'base_link'}, pose: {position: {x: ${DISTANCE}, y: 0.0, z: 0.0}, orientation: {x: 0.0, y: 0.0, z: 0.0, w: 1.0}}}" >/dev/null

echo "[3/4] 确认规划器已给出有效速度"
/usr/bin/python3 - <<'PY'
import sys
import time
import rospy
from geometry_msgs.msg import Twist

rospy.init_node("demo_nav_preflight", anonymous=True, disable_signals=True)
deadline = time.monotonic() + 6.0
while time.monotonic() < deadline and not rospy.is_shutdown():
    try:
        msg = rospy.wait_for_message("/cmd_vel", Twist, timeout=1.0)
    except rospy.ROSException:
        continue
    if max(abs(msg.linear.x), abs(msg.linear.y), abs(msg.angular.z)) > 0.005:
        print("规划有效: vx={:.3f}, wz={:.3f}".format(msg.linear.x, msg.angular.z))
        sys.exit(0)
raise SystemExit("未检测到有效规划速度，拒绝解锁")
PY

echo "[4/4] 开始演示；到点自动停用，最迟 ${HARD_TIMEOUT} 秒硬截止"
nohup bash -lc "
  sleep ${HARD_TIMEOUT}
  source /opt/ros/noetic/setup.bash
  source /home/unitree/robot/DaoLan/G1Nav2D/devel/setup.bash
  rosservice call /unitree_emergency_stop '{}' >/dev/null 2>&1 || true
  rostopic pub -1 /move_base/cancel actionlib_msgs/GoalID \
    '{stamp: {secs: 0, nsecs: 0}, id: \"\"}' >/dev/null 2>&1 || true
" >/tmp/daolan_demo_cutoff.log 2>&1 </dev/null &
CUTOFF_PID=$!

rosservice call /unitree_motion_enable "data: true"

/usr/bin/python3 - "${NORMAL_TIMEOUT}" <<'PY'
import sys
import time
import rospy
from geometry_msgs.msg import Twist

timeout = float(sys.argv[1])
deadline = time.monotonic() + timeout
started = False
quiet_since = None
while time.monotonic() < deadline and not rospy.is_shutdown():
    try:
        msg = rospy.wait_for_message("/cmd_vel_smooth", Twist, timeout=0.5)
    except rospy.ROSException:
        continue
    active = max(abs(msg.linear.x), abs(msg.linear.y), abs(msg.angular.z)) > 0.005
    if active:
        started = True
        quiet_since = None
    elif started:
        if quiet_since is None:
            quiet_since = time.monotonic()
        elif time.monotonic() - quiet_since >= 1.5:
            print("检测到到点零速")
            sys.exit(0)
raise SystemExit("演示达到正常超时，执行安全停止")
PY

echo "演示完成，正在禁用运动"
