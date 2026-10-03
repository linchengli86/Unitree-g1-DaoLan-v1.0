#!/usr/bin/env bash

# Select and switch to Lenovo microphone on Ubuntu (PulseAudio/PipeWire)
# - Lists all sources with readable descriptions
# - Prefers Lenovo microphone devices
# - Lets you pick one to set as default
# - Optionally plays a short test sound
#
# Usage:
#   bash select_lenovo_microphone.sh             # interactive selection
#   PATTERN="Lenovo" bash select_lenovo_microphone.sh  # auto-pick first match
#   TEST=1 bash select_lenovo_microphone.sh      # play test sound after switching
#   SKIP_AEC=1 bash select_lenovo_microphone.sh  # switch mic without enabling AEC
#   bash select_lenovo_microphone.sh "510"       # pass pattern as arg

set -euo pipefail

# 为远程/后台环境准备音频与会话环境
# 这些环境变量使进程能连接到用户会话的DBus与PulseAudio/PipeWire
export USER_UID="$(id -u)"
export XDG_RUNTIME_DIR="/run/user/${USER_UID}"
export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"
# PulseAudio/PipeWire 通常监听此socket
if [ -S "${XDG_RUNTIME_DIR}/pulse/native" ]; then
    export PULSE_SERVER="unix:${XDG_RUNTIME_DIR}/pulse/native"
fi
# 避免某些环境下的AT-SPI DBus错误刷屏
export NO_AT_BRIDGE=1
export ALSA_CARD=0  # 使用第一个音频设备
export ALSA_DEVICE=0
# 设置显示环境变量（修复pynput X连接错误）
export DISPLAY="${DISPLAY:-:0}"

if ! command -v pactl >/dev/null 2>&1; then
  echo "Error: pactl not found. Install PulseAudio/PipeWire tools (sudo apt install pulseaudio-utils)." >&2
  exit 1
fi

# Return an array: each element is "name\tindex\tdescription"
list_sources() {
  local IFS=$'\n'
  local sources_short sources_full
  sources_short=$(pactl list short sources || true)
  sources_full=$(pactl list sources || true)

  # Parse descriptions per source name
  while read -r line; do
    [[ -z "$line" ]] && continue
    local index name
    index=$(echo "$line" | awk '{print $1}')
    name=$(echo "$line" | awk '{print $2}')
    
    # Skip monitor devices (output monitoring)
    if echo "$name" | grep -q "\.monitor$"; then
      continue
    fi
    # Skip AEC virtual sources so switching always targets real hardware
    if echo "$name" | grep -qiE 'ec_sink|ec_source|echoCancel|echo-cancel|source_ec'; then
      continue
    fi
    
    # Grab Description: line from the full listing block for this source
    local desc
    desc=$(echo "$sources_full" | awk -v n="Name: ${name}" 'found{print; if ($0 ~ /^\s*$/) exit} $0==n{found=1}' | grep -m1 "Description:" | sed 's/^[ \t]*Description:[ \t]*//')
    if [[ -z "$desc" ]]; then
      desc="$name"
    fi
    echo -e "${name}\t${index}\t${desc}"
  done <<< "$sources_short"
}

print_sources() {
  local IFS=$'\n'
  local items=($(list_sources))
  local i=1
  for item in "${items[@]}"; do
    local name index desc
    name=$(echo -e "$item" | cut -f1)
    index=$(echo -e "$item" | cut -f2)
    desc=$(echo -e "$item" | cut -f3-)
    echo "  ${i}) ${desc}  [${name}]"
    i=$((i+1))
  done
}

choose_source_interactive() {
  local IFS=$'\n'
  local items=($(list_sources))
  if [[ ${#items[@]} -eq 0 ]]; then
    echo "Error: No sources found." >&2
    return 1
  fi

  echo "可用话筒设备："
  print_sources

  local choice
  read -rp "请输入序号或关键字(例如 Lenovo): " choice
  # Normalize: allow formats like "2) xxx" or pasted whole line
  local number_only
  number_only=$(echo "$choice" | sed -E 's/^\s*([0-9]+)\).*/\1/; t; s/.*//')
  # If number, select by index
  if [[ -n "$number_only" ]]; then
    choice="$number_only"
  fi
  if [[ "$choice" =~ ^[0-9]+$ ]]; then
    if (( choice >= 1 && choice <= ${#items[@]} )); then
      echo -e "${items[$((choice-1))]}"
      return 0
    else
      echo "序号超出范围。" >&2
      return 1
    fi
  fi

  # Otherwise treat as pattern and find first match
  local matched
  if matched=$(choose_source_by_pattern "$choice"); then
    echo -e "$matched"
    return 0
  fi

  echo "未匹配到含有 \"$choice\" 的设备，请重试。" >&2
  return 1
}

# Auto-select first source whose description or name matches PATTERN
choose_source_by_pattern() {
  local pattern="$1"
  local IFS=$'\n'
  for item in $(list_sources); do
    local name desc
    name=$(echo -e "$item" | cut -f1)
    desc=$(echo -e "$item" | cut -f3-)
    # Exclude wireless microphones (Hollyland, DJI, Wireless Microphone)
    if echo "$name $desc" | grep -qiE "(Hollyland|DJI.*MIC|Wireless.*Microphone)"; then
      continue
    fi
    if echo "$name $desc" | grep -iE -- "$pattern" >/dev/null; then
      echo -e "$item"
      return 0
    fi
  done
  return 1
}

set_default_source() {
  local name="$1"
  pactl set-default-source "$name"
}

set_source_volume() {
  local name="$1"
  local volume="${2:-29%}"
  pactl set-source-volume "$name" "$volume" || true
}

play_test_sound() {
  # Try several common tools; ignore failures
  if command -v canberra-gtk-play >/dev/null 2>&1; then
    canberra-gtk-play --id=audio-volume-change --description="Audio test" || true
  elif command -v paplay >/dev/null 2>&1 && [[ -f /usr/share/sounds/freedesktop/stereo/audio-volume-change.oga ]]; then
    paplay /usr/share/sounds/freedesktop/stereo/audio-volume-change.oga || true
  elif command -v speaker-test >/dev/null 2>&1; then
    speaker-test -t wav -c 2 -l 1 >/dev/null 2>&1 || true
  else
    echo "提示: 未找到测试音工具(canberra-gtk-play/paplay/speaker-test)。" >&2
  fi
}

main() {
  local selection_line

  # Default pattern prefers Lenovo microphone devices
  # Match by description first, then by device name
  local DEFAULTS=(
    "${1:-}"                 # arg1 highest priority (if provided)
    "${PATTERN:-}"           # env PATTERN
    "Lenovo.*510.*Camera.*iec958-stereo"  # prefer Lenovo 510 Camera digital (iec958-stereo)
    "Lenovo.*510.*Camera"    # Lenovo 510 Camera
    "Lenovo.*510"            # Lenovo 510
    "Lenovo.*Camera.*iec958" # Lenovo Camera digital (iec958)
    "Lenovo.*Camera"         # Lenovo Camera
    "Lenovo"                 # any Lenovo device
    "510.*Camera"            # 510 Camera keyword
  )

  for pat in "${DEFAULTS[@]}"; do
    [[ -z "$pat" ]] && continue
    if selection_line=$(choose_source_by_pattern "$pat"); then
      break
    fi
  done

  if [[ -z "${selection_line:-}" ]]; then
    echo "未通过默认规则匹配到联想话筒设备。可用设备如下："
    print_sources
    selection_line=$(choose_source_interactive)
  fi

  local name index desc
  name=$(echo -e "$selection_line" | cut -f1)
  index=$(echo -e "$selection_line" | cut -f2)
  desc=$(echo -e "$selection_line" | cut -f3-)

  echo "切换默认话筒到: ${desc} [${name}] (index ${index})"
  set_default_source "$name"
  set_source_volume "$name" "29%"
  echo "已切换，音量已设置为29%。"

  if [[ "${TEST:-0}" == "1" ]]; then
    echo "播放测试音..."
    play_test_sound
  fi

  if [[ "${SKIP_AEC:-0}" == "1" ]]; then
    echo "已跳过 AEC（SKIP_AEC=1）"
  else
    local aec_script
    aec_script="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/enable_aec.sh"
    echo "正在根据当前默认喇叭/麦克风开启 AEC..."
    if ! bash "$aec_script"; then
      echo "警告: 开启 AEC 失败，已保持刚切换的麦克风。" >&2
    fi
  fi
}

main "$@"

