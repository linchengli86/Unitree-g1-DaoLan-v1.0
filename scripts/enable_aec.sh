#!/usr/bin/env bash

# 按当前默认喇叭和麦克风开启系统级 AEC（回声消除）
# - 不写死设备名，跟随 pactl 当前默认输入/输出
# - 若已加载过 echo-cancel，先取出真正的硬件设备再重载，避免套娃
# - 使用 WebRTC + channels=1，不指定 rate（指定 32k 会导致 48k USB 设备初始化失败）
# - 关闭 WebRTC analog/digital AGC，避免说话时麦音量被自动拉高
# - WebRTC 失败时回退 Speex（同样关闭 agc）
#
# Usage:
#   bash enable_aec.sh
#   SKIP_AEC=1 bash select_wired_microphone.sh  # 切换设备时跳过 AEC

set -euo pipefail

export USER_UID="$(id -u)"
export XDG_RUNTIME_DIR="/run/user/${USER_UID}"
export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"
if [ -S "${XDG_RUNTIME_DIR}/pulse/native" ]; then
    export PULSE_SERVER="unix:${XDG_RUNTIME_DIR}/pulse/native"
fi
export NO_AT_BRIDGE=1
export DISPLAY="${DISPLAY:-:0}"
export LC_ALL=C

if ! command -v pactl >/dev/null 2>&1; then
  echo "Error: pactl not found. Install PulseAudio/PipeWire tools (sudo apt install pulseaudio-utils)." >&2
  exit 1
fi

is_echo_cancel_name() {
  local name="$1"
  [[ "$name" == *ec_sink* || "$name" == *ec_source* || "$name" == *echoCancel* || "$name" == *echo-cancel* || "$name" == *source_ec* ]]
}

get_default_sink() {
  pactl info 2>/dev/null | awk -F': ' '/Default Sink:/{print $2; exit}'
}

get_default_source() {
  pactl info 2>/dev/null | awk -F': ' '/Default Source:/{print $2; exit}'
}

get_echo_cancel_args() {
  pactl list short modules 2>/dev/null | awk -F'\t' '$2=="module-echo-cancel"{print $3; exit}'
}

get_modarg() {
  local args="$1" key="$2"
  # shellcheck disable=SC2086
  printf '%s\n' $args | awk -F= -v k="$key" '$1==k{print $2; exit}'
}

unload_echo_cancel() {
  # 按 ID 卸掉全部 echo-cancel，避免连续切换时残留多套
  local id
  while read -r id; do
    [[ -z "$id" ]] && continue
    pactl unload-module "$id" >/dev/null 2>&1 || true
  done < <(pactl list short modules 2>/dev/null | awk -F'\t' '$2=="module-echo-cancel"{print $1}')
}

move_streams() {
  local sink_name="$1" source_name="$2"
  local id
  while read -r id; do
    [[ -z "$id" ]] && continue
    pactl move-sink-input "$id" "$sink_name" >/dev/null 2>&1 || true
  done < <(pactl list short sink-inputs 2>/dev/null | awk '{print $1}')

  while read -r id; do
    [[ -z "$id" ]] && continue
    pactl move-source-output "$id" "$source_name" >/dev/null 2>&1 || true
  done < <(pactl list short source-outputs 2>/dev/null | awk '{print $1}')
}

aec_args_for() {
  local method="$1"
  if [[ "$method" == "webrtc" ]]; then
    # WebRTC 默认 analog_gain_control=1，会按语音电平改硬件采集音量
    printf '%s' "analog_gain_control=0 digital_gain_control=0"
  else
    printf '%s' "agc=0"
  fi
}

load_aec() {
  local method="$1" source="$2" sink="$3"
  local aec_args
  aec_args="$(aec_args_for "$method")"
  pactl load-module module-echo-cancel \
    aec_method="$method" \
    aec_args="${aec_args}" \
    source_master="$source" \
    sink_master="$sink" \
    source_name=ec_source \
    sink_name=ec_sink \
    channels=1
}

main() {
  local sink source ec_args master_sink master_source mod_id

  sink=$(get_default_sink)
  source=$(get_default_source)
  ec_args=$(get_echo_cancel_args || true)

  if [[ -n "${ec_args:-}" ]]; then
    if is_echo_cancel_name "$sink"; then
      master_sink=$(get_modarg "$ec_args" sink_master)
      [[ -n "$master_sink" ]] && sink="$master_sink"
    fi
    if is_echo_cancel_name "$source"; then
      master_source=$(get_modarg "$ec_args" source_master)
      [[ -n "$master_source" ]] && source="$master_source"
    fi
  fi

  if [[ -z "$sink" || -z "$source" ]]; then
    echo "Error: 无法读取默认喇叭或麦克风。" >&2
    exit 1
  fi

  if [[ "$source" == *.monitor ]]; then
    echo "Error: 当前默认输入是喇叭 monitor，不是麦克风。请先切换麦克风。" >&2
    exit 1
  fi

  if is_echo_cancel_name "$sink" || is_echo_cancel_name "$source"; then
    echo "Error: 仍指向 AEC 虚拟设备（sink=$sink source=$source），请先切换真实喇叭/麦克风。" >&2
    exit 1
  fi

  echo "开启 AEC："
  echo "  喇叭=$sink"
  echo "  麦克风=$source"
  echo "  AGC=关闭（避免说话时音量自动变大）"

  unload_echo_cancel

  if mod_id=$(load_aec webrtc "$source" "$sink"); then
    echo "WebRTC AEC 加载成功 (module $mod_id, AGC 已关闭)"
  else
    echo "WebRTC AEC 失败，改用 Speex..."
    if mod_id=$(load_aec speex "$source" "$sink"); then
      echo "Speex AEC 加载成功 (module $mod_id, AGC 已关闭)"
    else
      echo "Error: AEC 模块初始化失败。" >&2
      exit 1
    fi
  fi

  pactl set-default-sink ec_sink
  pactl set-default-source ec_source
  move_streams ec_sink ec_source

  echo "已将默认输出/输入切换为 ec_sink / ec_source"
}

main "$@"

