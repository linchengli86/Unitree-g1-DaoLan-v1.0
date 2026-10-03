#!/usr/bin/env bash
# 按顺序后台启动导航、ROS Core、运控、语音（Ubuntu 服务器）

set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
source "$SCRIPT_DIR/env.sh"
[[ -f "$SCRIPT_DIR/reloc_config.sh" ]] && source "$SCRIPT_DIR/reloc_config.sh"

# 参数解析：支持 --no-auto-reloc 关闭自动重定位（默认启用）
for arg in "$@"; do
  case "$arg" in
    --auto-reloc)
      export AUTO_RELOC=1 ;; # 兼容旧用法
    --no-auto-reloc)
      export AUTO_RELOC=0 ;;
  esac
done

log "启动流程开始"

# 0) 基础检查
[[ -f "$ROS_SETUP" ]] || { echo "未找到 ROS Noetic: $ROS_SETUP" >&2; exit 1; }
[[ -d "$NAV_WS" ]] || { echo "未找到导航目录: $NAV_WS" >&2; exit 1; }
[[ -d "$CONTROL_DIR" ]] || { echo "未找到运控目录: $CONTROL_DIR" >&2; exit 1; }
[[ -d "$VOICE_APP_DIR" ]] || { echo "未找到语音目录: $VOICE_APP_DIR" >&2; exit 1; }

# 1) 启动导航（后台）：cd G1Nav2D && source devel/setup.bash && roslaunch fastlio navigation.launch
log "[1/4] 启动导航：cd '$NAV_WS' && source devel/setup.bash && roslaunch fastlio navigation.launch"
NAV_LOG="$LOG_DIR/navigation.log"
if [[ -f "$NAV_WS/devel/setup.bash" ]]; then
  nohup bash -lc "cd '$NAV_WS' && source devel/setup.bash && roslaunch fastlio navigation.launch rviz:='$NAV_RVIZ'" >"$NAV_LOG" 2>&1 &
  NAV_PID=$!
  write_pid navigation "$NAV_PID"
  log "navigation PID=$NAV_PID，日志：$NAV_LOG"
else
  warn "未找到 $NAV_WS/devel/setup.bash，导航无法启动"
fi

# 2) 等待导航中的 ROS Master 就绪（不另启roscore）
log "[2/4] 等待导航启动的 ROS Master 就绪..."
log "等待 ROS Master 就绪..."
for _ in $(seq 60); do
  if bash -lc "source '$ROS_SETUP' && rosparam get /rosdistro >/dev/null 2>&1"; then
    log "检测到 ROS Master 正在运行"
    break
  fi
  sleep 1
done
if ! bash -lc "source '$ROS_SETUP' && rosparam get /rosdistro >/dev/null 2>&1"; then
  warn "ROS Master 未检测到，继续尝试，但后续可能失败"
fi

# 2.5) 等待关键导航服务就绪，然后启动自动重定位
log "[2.5/4] 等待导航服务就绪..."
for _ in $(seq 30); do
  if bash -lc "source '$ROS_SETUP' && cd '$NAV_WS' && source devel/setup.bash && rosservice list 2>/dev/null | grep -q '/slam_reloc'" && \
     bash -lc "source '$ROS_SETUP' && cd '$NAV_WS' && source devel/setup.bash && rosservice list 2>/dev/null | grep -q '/move_base'"; then
    log "检测到关键导航服务已就绪"
    break
  fi
  sleep 2
done

# 启动自动重定位（后台）：可用 --no-auto-reloc 关闭；日志输出至 $LOG_DIR/auto_reloc.log
if [[ "${AUTO_RELOC:-1}" == "1" ]]; then
  AUTO_RELOC_LOG="$LOG_DIR/auto_reloc.log"
  log "[2.6/4] 启动自动重定位：$SCRIPT_DIR/auto_reloc.sh"
  nohup bash -lc "'$SCRIPT_DIR/auto_reloc.sh'" >"$AUTO_RELOC_LOG" 2>&1 &
  AUTO_RELOC_PID=$!
  write_pid auto_reloc "$AUTO_RELOC_PID"
  log "auto_reloc PID=$AUTO_RELOC_PID，日志：$AUTO_RELOC_LOG"
else
  log "[2.6/4] 跳过自动重定位（AUTO_RELOC=0）"
fi

# 3) 启动运控（后台）
log "[3/4] 启动运控 g1_control.py $CONTROL_IFACE（$CONTROL_PYTHON）"
CONTROL_LOG="$LOG_DIR/control.log"
nohup bash -lc "source '$ROS_SETUP' && cd '$CONTROL_DIR' && '$CONTROL_PYTHON' g1_control.py '$CONTROL_IFACE'" >"$CONTROL_LOG" 2>&1 &
CONTROL_PID=$!
write_pid control "$CONTROL_PID"
log "control PID=$CONTROL_PID，日志：$CONTROL_LOG"

# 可选：等待运控输出关键字（优化：若日志中出现错误自动停止）
sleep 2
if grep -qiE "(Traceback|Error|Exception)" "$CONTROL_LOG" 2>/dev/null; then
  warn "运控日志检测到错误（$CONTROL_LOG），请检查"
fi

# 4) 启动语音（后台）
log "[4/4] 启动语音 main.py"
VOICE_LOG="$LOG_DIR/voice.log"
if [[ -f "$VOICE_VENV_ACTIVATE" ]]; then
  ACTIVATE_CMD="source '$VOICE_VENV_ACTIVATE' && "
else
  ACTIVATE_CMD=""
  warn "未找到虚拟环境：$VOICE_VENV_ACTIVATE，直接使用系统 python"
fi
nohup bash -lc "cd '$VOICE_APP_DIR' && ${ACTIVATE_CMD} source '$ROS_SETUP' && python main.py" >"$VOICE_LOG" 2>&1 &
VOICE_PID=$!
write_pid voice "$VOICE_PID"
log "voice PID=$VOICE_PID，日志：$VOICE_LOG"

log "全部启动完成。你可以查看日志：$LOG_DIR"
log "停止请运行：scripts/stop_all.sh"
