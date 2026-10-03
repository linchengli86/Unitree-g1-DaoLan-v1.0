#!/usr/bin/env bash
# 系统静音切换（Ubuntu / PulseAudio / PipeWire）
#
# 用法:
#   ./toggle_mute.sh              # 切换扬声器静音（默认）
#   ./toggle_mute.sh mute         # 仅静音
#   ./toggle_mute.sh unmute       # 仅取消静音
#   ./toggle_mute.sh --mic        # 切换麦克风
#   ./toggle_mute.sh --all        # 扬声器+麦克风一起切换
#   未知参数会被忽略，按默认 toggle 扬声器处理
#
# 核心: pactl set-sink-mute @DEFAULT_SINK@ toggle

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

ACTION="toggle"
DO_SINK=1
DO_SOURCE=0

for arg in "$@"; do
  case "$arg" in
    mute|unmute|toggle) ACTION="$arg" ;;
    --mic)  DO_SINK=0; DO_SOURCE=1 ;;
    --all)  DO_SINK=1; DO_SOURCE=1 ;;
    -h|--help)
      echo "用法: $0 [mute|unmute|toggle] [--mic|--all]"
      exit 0
      ;;
    *)
      # 未知参数忽略，走默认 toggle 扬声器
      ;;
  esac
done

# pactl 的 mute 参数: 0=取消静音 1=静音 toggle=切换
case "$ACTION" in
  mute)   PACTL_MUTE=1 ;;
  unmute) PACTL_MUTE=0 ;;
  toggle) PACTL_MUTE=toggle ;;
esac

export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
[[ -S "$XDG_RUNTIME_DIR/pulse/native" ]] && export PULSE_SERVER="unix:$XDG_RUNTIME_DIR/pulse/native"
export NO_AT_BRIDGE=1
export DISPLAY="${DISPLAY:-:0}"

if ! command -v pactl >/dev/null 2>&1; then
  warn "未找到 pactl，请安装 pulseaudio-utils"
  exit 1
fi

if [[ "$DO_SINK" -eq 1 ]]; then
  pactl set-sink-mute @DEFAULT_SINK@ "$PACTL_MUTE"
  log "扬声器: $ACTION (@DEFAULT_SINK@)"
fi

if [[ "$DO_SOURCE" -eq 1 ]]; then
  pactl set-source-mute @DEFAULT_SOURCE@ "$PACTL_MUTE"
  log "麦克风: $ACTION (@DEFAULT_SOURCE@)"
fi

