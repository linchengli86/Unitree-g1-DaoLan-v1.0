#!/usr/bin/env bash
# 自动调用 /slam_reloc：从 (0,0) 起逐步扩大半径，超过 RELOC_MAX_RADIUS(默认5m) 停止
# 依赖: env.sh + reloc_config.sh
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
source "$SCRIPT_DIR/env.sh"
source "$SCRIPT_DIR/reloc_config.sh"

if [[ "${AUTO_RELOC:-0}" != "1" ]]; then
  log "AUTO_RELOC 未启用，直接退出"
  exit 0
fi

log "[AutoReloc] 开始自动重定位（中心=($RELOC_X,$RELOC_Y)，max_radius=${RELOC_MAX_RADIUS}m）"

log "[AutoReloc] 等待 /slam_reloc (timeout=${RELOC_WAIT_TIMEOUT}s)"
end=$(( $(date +%s) + RELOC_WAIT_TIMEOUT ))
while (( $(date +%s) < end )); do
  if bash -lc "source '$ROS_SETUP' && cd '$NAV_WS' && source devel/setup.bash && rosservice list 2>/dev/null | grep -q '/slam_reloc'"; then
    log "[AutoReloc] 检测到 /slam_reloc 服务"
    break
  fi
  sleep 1
done
if ! bash -lc "source '$ROS_SETUP' && cd '$NAV_WS' && source devel/setup.bash && rosservice list 2>/dev/null | grep -q '/slam_reloc'"; then
  warn "[AutoReloc] 超时未检测到 /slam_reloc"
  [[ "$RELOC_FAIL_STRICT" == "1" ]] && exit 2 || exit 0
fi

status_ok=0

try_reloc_once() {
  local x="$1" y="$2" z="$3" roll="$4" pitch="$5" yaw="$6"
  log "[AutoReloc] 调用 /slam_reloc: pcd=$RELOC_PCD_PATH pose=($x,$y,$z,$roll,$pitch,$yaw)"
  local CALL_CMD
  CALL_CMD=$(cat <<EOF
source '$ROS_SETUP' && cd '$NAV_WS' && source devel/setup.bash && rosservice call /slam_reloc "{pcd_path: '$RELOC_PCD_PATH', x: $x, y: $y, z: $z, roll: $roll, pitch: $pitch, yaw: $yaw}" 2>&1
EOF
)
  local RESP
  RESP=$(bash -lc "$CALL_CMD" || true)
  log "[AutoReloc] /slam_reloc 响应:\n$RESP"

  local end_try=$(( $(date +%s) + ${RELOC_PER_TRY_TIMEOUT:-12} ))
  while (( $(date +%s) < end_try )); do
    local CHECK_OUT
    CHECK_OUT=$(bash -lc "source '$ROS_SETUP' && cd '$NAV_WS' && source devel/setup.bash && rosservice call /slam_reloc_check 'code: true' 2>/dev/null || true")
    if echo "$CHECK_OUT" | grep -q "status: True"; then
      status_ok=1
      log "[AutoReloc] 重定位成功 (pose=($x,$y,$z,$roll,$pitch,$yaw))"
      return 0
    fi
    sleep "${RELOC_CHECK_INTERVAL:-1}"
  done
  return 1
}

RADII=$(python3 - <<PY
max_r = float("${RELOC_MAX_RADIUS}")
raw = "${RELOC_RADIUS_CANDIDATES}"
out = []
for tok in raw.split():
    try:
        r = float(tok)
    except ValueError:
        continue
    if 0.0 <= r <= max_r + 1e-9:
        out.append(str(r))
print(" ".join(out))
PY
)
log "[AutoReloc] 半径阶梯: [$RADII]（上限 ${RELOC_MAX_RADIUS}m）"

if [[ "${RELOC_SWEEP_ENABLE:-1}" != "1" ]]; then
  try_reloc_once "$RELOC_X" "$RELOC_Y" "$RELOC_Z" "$RELOC_ROLL" "$RELOC_PITCH" "$RELOC_YAW" || true
else
  for r in $RADII; do
    if python3 -c "import sys; sys.exit(0 if abs(float('$r')) < 1e-9 else 1)"; then
      if try_reloc_once "$RELOC_X" "$RELOC_Y" "$RELOC_Z" "$RELOC_ROLL" "$RELOC_PITCH" "$RELOC_YAW"; then
        break
      fi
    else
      log "[AutoReloc] 尝试半径 r=$r m（四方向）"
      for dx_dy in "$r 0" "-$r 0" "0 $r" "0 -$r"; do
        set -- $dx_dy
        dx="$1"
        dy="$2"
        x=$(python3 -c "print(float('$RELOC_X') + float('$dx'))")
        y=$(python3 -c "print(float('$RELOC_Y') + float('$dy'))")
        if try_reloc_once "$x" "$y" "$RELOC_Z" "$RELOC_ROLL" "$RELOC_PITCH" "$RELOC_YAW"; then
          break 2
        fi
        sleep "${RELOC_TRY_INTERVAL:-2}"
      done
    fi
    sleep "${RELOC_TRY_INTERVAL:-2}"
  done
fi

if [[ "$status_ok" != "1" ]]; then
  warn "[AutoReloc] 在 ${RELOC_MAX_RADIUS}m 内未成功，停止自动定位（可网页点选重试）"
  [[ "$RELOC_FAIL_STRICT" == "1" ]] && exit 3 || exit 0
fi

log "[AutoReloc] 完成"

