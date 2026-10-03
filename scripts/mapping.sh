#!/usr/bin/env bash
# 建图：启动 roslaunch + H5 桥接（mapping_h5_bridge.py / projected_map_ws_bridge.py）；
# 退出或停止建图时一并清理桥接进程

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

export DISABLE_ROS1_EOL_WARNINGS=1

MAPPING_WS="${NAV_WS:-$BASE_DIR/G1Nav2D}"
BRIDGE_SCRIPT="$MAPPING_WS/src/tool/scripts/mapping_h5_bridge.py"
BRIDGE_LOG="$LOG_DIR/mapping_h5_bridge.log"
PROJECTED_MAP_BRIDGE_SCRIPT="$MAPPING_WS/src/tool/scripts/projected_map_ws_bridge.py"
PROJECTED_MAP_BRIDGE_LOG="$LOG_DIR/projected_map_ws_bridge.log"

ROSLAUNCH_PID=""
cleanup() {
  log "建图会话结束，停止桥接与 roslaunch…"
  stop_pid mapping_h5_bridge TERM 8 || true
  stop_pid projected_map_ws_bridge TERM 8 || true
  pkill -f 'mapping_h5_bridge\.py' 2>/dev/null || true
  pkill -f 'projected_map_ws_bridge\.py' 2>/dev/null || true
  if [[ -n "${ROSLAUNCH_PID:-}" ]] && is_running "$ROSLAUNCH_PID"; then
    kill -TERM "$ROSLAUNCH_PID" 2>/dev/null || true
    for _ in $(seq 1 12); do
      is_running "$ROSLAUNCH_PID" || break
      sleep 0.5
    done
    if is_running "$ROSLAUNCH_PID"; then
      kill -KILL "$ROSLAUNCH_PID" 2>/dev/null || true
    fi
  fi
  rm -f "$PID_DIR/mapping_h5_bridge.pid"
  rm -f "$PID_DIR/projected_map_ws_bridge.pid"
}
trap cleanup EXIT INT TERM HUP

# 避免重复实例
stop_pid mapping_h5_bridge TERM 5 || true
stop_pid projected_map_ws_bridge TERM 5 || true
pkill -f 'mapping_h5_bridge\.py' 2>/dev/null || true
pkill -f 'projected_map_ws_bridge\.py' 2>/dev/null || true
rm -f "$PID_DIR/mapping_h5_bridge.pid"
rm -f "$PID_DIR/projected_map_ws_bridge.pid"

log "[1/4] 工作空间：$MAPPING_WS"
cd "$MAPPING_WS"

if [[ ! -f "$MAPPING_WS/devel/setup.bash" ]]; then
  echo "未找到 $MAPPING_WS/devel/setup.bash" >&2
  exit 1
fi

set +u
source "$MAPPING_WS/devel/setup.bash"
set -u

if [[ ! -f "$BRIDGE_SCRIPT" ]]; then
  warn "未找到 $BRIDGE_SCRIPT，H5 建图界面将无法显示点云/位姿"
fi
if [[ ! -f "$PROJECTED_MAP_BRIDGE_SCRIPT" ]]; then
  warn "未找到 $PROJECTED_MAP_BRIDGE_SCRIPT，H5 将无法显示 RViz 同源墙体栅格"
fi

log "[2/5] 启动 roslaunch fastlio mapping.launch（rviz=$NAV_RVIZ）"
roslaunch fastlio mapping.launch rviz:="$NAV_RVIZ" &
ROSLAUNCH_PID=$!

log "[3/5] 等待 ROS Master…"
if ! bash -lc "source '$ROS_SETUP' && rosparam get /rosdistro >/dev/null 2>&1"; then
  for _ in $(seq 1 60); do
    if bash -lc "source '$ROS_SETUP' && rosparam get /rosdistro >/dev/null 2>&1"; then
      break
    fi
    sleep 1
  done
fi

log "[4/5] 启动 H5 点云桥接（python3）：$BRIDGE_SCRIPT"
if [[ -f "$BRIDGE_SCRIPT" ]]; then
  mkdir -p "$LOG_DIR"
  nohup bash -c "source '$ROS_SETUP' && source '$MAPPING_WS/devel/setup.bash' && exec python3 '$BRIDGE_SCRIPT'" \
    >>"$BRIDGE_LOG" 2>&1 &
  write_pid mapping_h5_bridge "$!"
  log "mapping_h5_bridge 已后台运行，PID=$(read_pid mapping_h5_bridge)，日志：$BRIDGE_LOG"
else
  warn "跳过 H5 桥接"
fi

log "[5/5] 启动 projected_map 桥接（python3）：$PROJECTED_MAP_BRIDGE_SCRIPT"
if [[ -f "$PROJECTED_MAP_BRIDGE_SCRIPT" ]]; then
  mkdir -p "$LOG_DIR"
  nohup bash -c "source '$ROS_SETUP' && source '$MAPPING_WS/devel/setup.bash' && exec python3 '$PROJECTED_MAP_BRIDGE_SCRIPT'" \
    >>"$PROJECTED_MAP_BRIDGE_LOG" 2>&1 &
  write_pid projected_map_ws_bridge "$!"
  log "projected_map_ws_bridge 已后台运行，PID=$(read_pid projected_map_ws_bridge)，日志：$PROJECTED_MAP_BRIDGE_LOG"
else
  warn "跳过 projected_map 桥接"
fi

set +e
wait "$ROSLAUNCH_PID"
set -e
