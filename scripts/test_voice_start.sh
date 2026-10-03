#!/usr/bin/env bash
# 测试语音启动脚本（模拟未登录环境）

set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
source "$SCRIPT_DIR/env.sh"

log() { echo "[$(date +'%F %T')] $*"; }
warn() { echo "[$(date +'%F %T')] [WARN] $*" >&2; }

log "测试语音启动脚本环境检测"

# 检查用户会话状态
log "=== 用户会话检查 ==="
if [[ -d "/run/user/$(id -u)" ]]; then
  log "✓ 用户会话目录存在: /run/user/$(id -u)"
else
  log "✗ 用户会话目录不存在: /run/user/$(id -u)"
fi

if [[ -S "/run/user/$(id -u)/bus" ]]; then
  log "✓ D-Bus 会话总线可用: /run/user/$(id -u)/bus"
else
  log "✗ D-Bus 会话总线不可用: /run/user/$(id -u)/bus"
fi

# 检查音频系统
log "=== 音频系统检查 ==="
if command -v pactl >/dev/null 2>&1; then
  if pactl info >/dev/null 2>&1; then
    log "✓ PulseAudio 可用"
    pactl info | head -5
  else
    log "✗ PulseAudio 不可用"
  fi
else
  log "✗ pactl 命令不存在"
fi

if command -v amixer >/dev/null 2>&1; then
  log "✓ amixer 可用"
  amixer sget Master 2>/dev/null | head -3 || log "✗ amixer 无法访问 Master 控件"
else
  log "✗ amixer 命令不存在"
fi

# 检查显示系统
log "=== 显示系统检查 ==="
if [[ -n "${DISPLAY:-}" ]]; then
  log "✓ DISPLAY 环境变量已设置: $DISPLAY"
  if xset q >/dev/null 2>&1; then
    log "✓ X11 显示服务器可用"
  else
    log "✗ X11 显示服务器不可用"
  fi
else
  log "✗ DISPLAY 环境变量未设置"
fi

if command -v Xvfb >/dev/null 2>&1; then
  log "✓ Xvfb 可用（虚拟显示服务器）"
else
  log "✗ Xvfb 不可用"
fi

# 检查 Python 环境
log "=== Python 环境检查 ==="
if [[ -f "$VOICE_VENV_ACTIVATE" ]]; then
  log "✓ 虚拟环境存在: $VOICE_VENV_ACTIVATE"
else
  log "✗ 虚拟环境不存在: $VOICE_VENV_ACTIVATE"
fi

if command -v python >/dev/null 2>&1; then
  log "✓ Python 可用: $(python --version 2>&1)"
else
  log "✗ Python 不可用"
fi

# 检查 ROS 环境
log "=== ROS 环境检查 ==="
if [[ -f "$ROS_SETUP" ]]; then
  log "✓ ROS 设置文件存在: $ROS_SETUP"
else
  log "✗ ROS 设置文件不存在: $ROS_SETUP"
fi

# 检查语音应用目录
log "=== 语音应用检查 ==="
if [[ -d "$VOICE_APP_DIR" ]]; then
  log "✓ 语音应用目录存在: $VOICE_APP_DIR"
  if [[ -f "$VOICE_APP_DIR/main.py" ]]; then
    log "✓ main.py 文件存在"
  else
    log "✗ main.py 文件不存在"
  fi
else
  log "✗ 语音应用目录不存在: $VOICE_APP_DIR"
fi

log "=== 测试完成 ==="
log "如果所有检查都通过，语音服务应该能够正常启动"
