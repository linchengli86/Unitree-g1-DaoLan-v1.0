#!/usr/bin/env bash
# 启动语音（Ubuntu 服务器）

set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
source "$SCRIPT_DIR/env.sh"
[[ -f "$SCRIPT_DIR/reloc_config.sh" ]] && source "$SCRIPT_DIR/reloc_config.sh"

# 参数解析：支持 --no-auto-reloc 关闭自动重定位（默认启用）
RUN_FOREGROUND=0
for arg in "$@"; do
  case "$arg" in
    --auto-reloc)
      export AUTO_RELOC=1 ;; # 兼容旧用法
    --no-auto-reloc)
      export AUTO_RELOC=0 ;;
    --fg|--foreground)
      RUN_FOREGROUND=1 ;;
  esac
done

log "启动流程开始"

# 0) 基础检查
[[ -d "$VOICE_APP_DIR" ]] || { echo "未找到语音目录: $VOICE_APP_DIR" >&2; exit 1; }

# 检测是否已启动语音，已启动则提示并退出
VOICE_PID=$(read_pid voice)
if [[ -n "$VOICE_PID" ]] && is_running "$VOICE_PID"; then
  log "语音服务已在运行 (PID: $VOICE_PID)"
  log "如需重启，请先运行：scripts/stop_all.sh"
  exit 0
fi

# 额外检测：通过进程名和路径检测语音服务是否在运行
if pgrep -f "python.*main\.py" >/dev/null 2>&1; then
  # 检查是否是我们的语音服务进程
  VOICE_PROCESS=$(pgrep -f "python.*main\.py" | head -1)
  if [[ -n "$VOICE_PROCESS" ]]; then
    # 检查进程的工作目录是否匹配
    PROCESS_CWD=$(readlink -f "/proc/$VOICE_PROCESS/cwd" 2>/dev/null || true)
    if [[ "$PROCESS_CWD" == "$VOICE_APP_DIR" ]]; then
      log "检测到语音服务进程已在运行 (PID: $VOICE_PROCESS, 目录: $PROCESS_CWD)"
      log "如需重启，请先运行：scripts/stop_all.sh"
      exit 0
    fi
  fi
fi


# 4) 启动语音（后台）
log "[4/4] 启动语音 main.py"
VOICE_LOG="$LOG_DIR/voice.log"
> "$VOICE_LOG"  # 每次启动清空日志

# 设置音频环境变量（修复PortAudio ALSA错误）
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
export PULSE_SERVER="unix:$XDG_RUNTIME_DIR/pulse/native"
export ALSA_CARD=0  # 使用第一个音频设备
export ALSA_DEVICE=0

# 设置显示环境变量（修复pynput X连接错误）
export DISPLAY="${DISPLAY:-:0}"
export NO_AT_BRIDGE=1  # 避免AT-SPI错误

# 禁用 Python 输出缓冲，确保日志实时写入文件（便于 tail -f 实时查看）
export PYTHONUNBUFFERED=1

# 默认以纯对话安全模式启动。需要身体动作时必须显式设置为 1。
export ROBOT_ACTIONS_ENABLED=${ROBOT_ACTIONS_ENABLED:-0}
export UNITREE_TTS_OUTPUT=${UNITREE_TTS_OUTPUT:-1}
export TEXT_ONLY_MODE=${TEXT_ONLY_MODE:-1}

# aarch64 上 pygame wheel 自带的 libgomp 如果在 OpenCV 之后加载，会出现
# "cannot allocate memory in static TLS block"。启动 Python 前先预加载同一文件。
PYGAME_LIBGOMP=$(find "$VOICE_APP_DIR/.venv/lib" \
  -path '*/pygame.libs/libgomp-*.so*' -print -quit 2>/dev/null || true)
if [[ -n "$PYGAME_LIBGOMP" ]]; then
  PYGAME_LIBGOMP=$(readlink -f "$PYGAME_LIBGOMP")
  export LD_PRELOAD="$PYGAME_LIBGOMP${LD_PRELOAD:+:$LD_PRELOAD}"
  log "已预加载 Pygame libgomp: $PYGAME_LIBGOMP"
fi

# 将系统输出音量调整为最大（优先 pactl，其次 pamixer，最后 amixer）
set_output_volume_max() {
  if command -v pactl >/dev/null 2>&1; then
    # 取消静音并设置为90%
    if pactl get-default-sink >/dev/null 2>&1; then
      local sink
      sink=$(pactl get-default-sink 2>/dev/null || echo @DEFAULT_SINK@)
      pactl set-sink-mute "$sink" 0 || true
      pactl set-sink-volume "$sink" 90% || true
      log "音量已通过 pactl 设置到最大 (sink=$sink)"
      return 0
    else
      # 某些系统不支持 get-default-sink，使用 @DEFAULT_SINK@
      pactl set-sink-mute @DEFAULT_SINK@ 0 || true
      pactl set-sink-volume @DEFAULT_SINK@ 90% || true
      log "音量已通过 pactl(@DEFAULT_SINK@) 设置到最大"
      return 0
    fi
  fi

  if command -v pamixer >/dev/null 2>&1; then
    pamixer --unmute || true
    pamixer --set-volume 90 || true
    log "音量已通过 pamixer 设置到最大"
    return 0
  fi

  if command -v amixer >/dev/null 2>&1; then
    # 优先通过 PulseAudio 控制；若失败则退回默认设备
    amixer -D pulse sset Master 90% unmute || amixer sset Master 90% unmute || true
    # 尝试常见控件名
    amixer -D pulse sset Speaker 90% unmute >/dev/null 2>&1 || true
    amixer -D pulse sset PCM 90% unmute >/dev/null 2>&1 || true
    log "音量已通过 amixer 设置到最大"
    return 0
  fi

  warn "未找到可用的音量控制工具 (pactl/pamixer/amixer)"
  return 1
}

# 调整系统音量
set_output_volume_max || true

# 3) 启动语音前暂停当前导览（若有）
log "暂停当前导览导航"
if [[ -f "$SCRIPT_DIR/pause_nav.sh" ]]; then
  if bash "$SCRIPT_DIR/pause_nav.sh"; then
    log "导览已暂停"
  else
    warn "暂停导览失败或无导航在运行，继续启动语音"
  fi
else
  warn "未找到 pause_nav.sh，跳过暂停导览"
fi

if [[ -f "$VOICE_VENV_ACTIVATE" ]]; then
  ACTIVATE_CMD="source '$VOICE_VENV_ACTIVATE' && "
else
  ACTIVATE_CMD=""
  warn "未找到虚拟环境：$VOICE_VENV_ACTIVATE，直接使用系统 python"
fi

if [[ "$RUN_FOREGROUND" -eq 1 ]]; then
  log "以前台模式启动（实时输出并写入日志：$VOICE_LOG）"
  # 后台运行主进程，记录 PID；前台用 tail 跟随日志并在进程结束后退出
  nohup bash -lc "cd '$VOICE_APP_DIR' && ${ACTIVATE_CMD} source '$ROS_SETUP' && python main.py" >>"$VOICE_LOG" 2>&1 &
  VOICE_PID=$!
  write_pid voice "$VOICE_PID"
  log "voice PID=$VOICE_PID，日志：$VOICE_LOG"

  # 跟随日志，进程结束后自动退出，从而关闭终端窗口
  if command -v tail >/dev/null 2>&1; then
    tail -f "$VOICE_LOG" --pid="$VOICE_PID" || true
  else
    log "系统缺少 tail 命令，改为简单等待进程结束"
    wait "$VOICE_PID" || true
  fi

  # 获取退出码并退出
  wait "$VOICE_PID" 2>/dev/null || true
  exit_code=$?
  log "语音进程退出，退出码：$exit_code，日志：$VOICE_LOG"
  exit $exit_code
else
  nohup bash -lc "cd '$VOICE_APP_DIR' && ${ACTIVATE_CMD} source '$ROS_SETUP' && python main.py" >"$VOICE_LOG" 2>&1 &
  VOICE_PID=$!
  write_pid voice "$VOICE_PID"
  log "voice PID=$VOICE_PID，日志：$VOICE_LOG"

  log "全部启动完成。你可以查看日志：$LOG_DIR"
  log "停止请运行：scripts/stop_all.sh"
fi
