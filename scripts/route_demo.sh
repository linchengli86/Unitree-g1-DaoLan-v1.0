#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
DESTINATION="${1:-auto}"
CHECK_ONLY=0
if [[ "${2:-}" == "--check-only" && $# == 2 ]]; then
  CHECK_ONLY=1
elif [[ $# -gt 1 ]]; then
  echo "用法: bash scripts/route_demo.sh [auto|start|end] [--check-only]" >&2
  exit 2
fi

case "$DESTINATION" in
  auto|start|end) ;;
  *)
    echo "用法: bash scripts/route_demo.sh [auto|start|end]" >&2
    exit 2
    ;;
esac

set +u
source /opt/ros/noetic/setup.bash
source "$PROJECT_DIR/G1Nav2D/devel/setup.bash"
set -u

safe_stop() {
  timeout 5 rosservice call /unitree_motion_enable "data: false" >/dev/null 2>&1
}
trap safe_stop EXIT INT TERM

echo "[1/4] 安全预检并禁用运动"
if ! safe_stop; then
  echo "未确认运动禁用，拒绝启动路线；请检查控制器" >&2
  exit 7
fi

required_nodes=(/localizer_node /move_base /unitree_safe_controller)
nodes=$(rosnode list 2>/dev/null || true)
for node in "${required_nodes[@]}"; do
  grep -qx "$node" <<<"$nodes" || {
    echo "缺少节点: $node；请先启动导航系统" >&2
    exit 3
  }
done

echo "[2/4] 检查定位与传感器"
reloc_status=$(rosservice call /slam_reloc_check "code: true" 2>/dev/null || true)
grep -q "status: True" <<<"$reloc_status" || {
  echo "重定位状态无效，拒绝启动路线" >&2
  exit 4
}
# tf_echo runs forever even when TF is valid: timeout would always reject it.
# This finite read-only lookup exits successfully after receiving the transform.
timeout 6 /usr/bin/python3 - <<'PY' >/dev/null 2>&1 || {
import math
import rospy
import tf
rospy.init_node("guide_route_tf_check", anonymous=True, disable_signals=True)
listener = tf.TransformListener()
listener.waitForTransform("map", "base_link", rospy.Time(0), rospy.Duration(4))
translation, rotation = listener.lookupTransform("map", "base_link", rospy.Time(0))
if not all(math.isfinite(value) for value in translation + rotation):
    raise ValueError("TF contains non-finite values")
PY
  echo "缺少 map -> base_link 变换" >&2
  exit 5
}

timeout 4 rostopic echo -n 1 /scan >/dev/null 2>&1 || {
  echo "没有收到 /scan 数据" >&2
  exit 6
}

if [[ "$CHECK_ONLY" == "1" ]]; then
  echo "预检通过；未发送目标，未使能运动"
  exit 0
fi

echo "[3/4] 路线方向: $DESTINATION"
echo "[4/4] 规划通过后自动使能；结束或异常时自动禁用"
/usr/bin/python3 "$SCRIPT_DIR/replay_teaching_route_safe.py" \
  --destination "$DESTINATION" \
  --spacing 0.5 \
  --goal-timeout 120 \
  --hard-timeout 180
