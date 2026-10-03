#!/usr/bin/env bash
# 网页点选重定位
# 用法（CLI）: reloc_from_point.sh <x> <y>
# 用法（/run_shfile）: param 必须为单个无空格 token，例如:
#   filename=.../reloc_from_point.sh&param=1.2,-3.4
# yaw 固定 0，由 ICP 自动搜朝向
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
source "$SCRIPT_DIR/env.sh"
source "$SCRIPT_DIR/reloc_config.sh"

# /run_shfile 拼命令: filename + " " + param + " &"
# 前端约定 param="x,y"（一个参数）；也兼容 CLI 传两个参数 x y
if [[ $# -ge 2 ]]; then
  X="$1"
  Y="$2"
elif [[ $# -eq 1 && "$1" == *,* ]]; then
  X="${1%%,*}"
  Y="${1#*,}"
  Y="${Y%%,*}"  # 若误传更多字段，只取第二段
else
  echo "[RelocPoint] usage: $0 <x> <y>  或  $0 <x>,<y>" >&2
  exit 1
fi

Z="${3:-0.0}"
ROLL="${4:-0.0}"
PITCH="${5:-0.0}"
YAW="${6:-0.0}"

# 逗号形式时 z/roll/pitch/yaw 不可从 $3 取；保持默认即可
if [[ $# -eq 1 ]]; then
  Z="0.0"
  ROLL="0.0"
  PITCH="0.0"
  YAW="0.0"
fi

log "[RelocPoint] 点选重定位 x=$X y=$Y (yaw=$YAW，ICP 自动搜朝向)"

# 等待服务（点选时导航应已启动，超时短一些）
WAIT_TO=${RELOC_WAIT_TIMEOUT:-60}
end=$(( $(date +%s) + WAIT_TO ))
while (( $(date +%s) < end )); do
  if bash -lc "source '$ROS_SETUP' && cd '$NAV_WS' && source devel/setup.bash && rosservice list 2>/dev/null | grep -q '/slam_reloc'"; then
    break
  fi
  sleep 0.5
done
if ! bash -lc "source '$ROS_SETUP' && cd '$NAV_WS' && source devel/setup.bash && rosservice list 2>/dev/null | grep -q '/slam_reloc'"; then
  warn "[RelocPoint] /slam_reloc 不可用"
  exit 2
fi

CALL_CMD=$(cat <<EOF
source '$ROS_SETUP' && cd '$NAV_WS' && source devel/setup.bash && rosservice call /slam_reloc "{pcd_path: '$RELOC_PCD_PATH', x: $X, y: $Y, z: $Z, roll: $ROLL, pitch: $PITCH, yaw: $YAW}" 2>&1
EOF
)
RESP=$(bash -lc "$CALL_CMD" || true)
log "[RelocPoint] /slam_reloc 响应:\n$RESP"

end_try=$(( $(date +%s) + ${RELOC_PER_TRY_TIMEOUT:-22} ))
while (( $(date +%s) < end_try )); do
  CHECK_OUT=$(bash -lc "source '$ROS_SETUP' && cd '$NAV_WS' && source devel/setup.bash && rosservice call /slam_reloc_check 'code: true' 2>/dev/null || true")
  if echo "$CHECK_OUT" | grep -q "status: True"; then
    log "[RelocPoint] 重定位成功 ($X, $Y)"
    exit 0
  fi
  sleep "${RELOC_CHECK_INTERVAL:-1}"
done

warn "[RelocPoint] 重定位未在超时内确认成功，可再点一次附近位置"
exit 3

