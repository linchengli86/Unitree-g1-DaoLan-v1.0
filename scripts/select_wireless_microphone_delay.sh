#!/usr/bin/env bash

# Select and switch audio input (microphone) on Ubuntu (PulseAudio/PipeWire)
# - Lists all sources with readable descriptions
# - Lets you pick one to set as default
# - Optionally plays a short test sound
#
# Usage:
#   bash select_wireless_microphone_delay.sh             # wait 20s, then auto-select
#   PATTERN="DJI" bash select_wireless_microphone_delay.sh  # auto-pick first match
#   TEST=1 bash select_wireless_microphone_delay.sh      # play test sound after switching
#   SKIP_AEC=1 bash select_wireless_microphone_delay.sh  # switch mic without enabling AEC
#   bash select_wireless_microphone_delay.sh "DJI.*MIC"  # pass pattern as arg
#   DELAY=10 bash select_wireless_microphone_delay.sh    # override wait seconds (default 20)

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

  echo "可用输入设备 (麦克风)："
  print_sources

  local choice
  read -rp "请输入序号或关键字(例如 DJI): " choice
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
  local volume="${2:-15%}"
  # 取消静音后再设音量；失败不中断切换流程
  pactl set-source-mute "$name" 0 || true
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
  local delay_seconds="${DELAY:-30}"
  echo "等待 ${delay_seconds} 秒后再切换无线麦克风..."
  sleep "$delay_seconds"

  local selection_line

  # Default pattern prefers Hollyland Wireless Microphone first, then DJI MIC MINI (stable matching)
  # Note: CSCTEK devices are wired microphones, should NOT be matched here
  local DEFAULTS=(
    "${1:-}"                 # arg1 highest priority (if provided)
    "${PATTERN:-}"           # env PATTERN
    "Shenzhen.*Hollyland.*Wireless.*Microphone.*iec958-stereo"  # Hollyland wireless digital
    "Hollyland.*Wireless.*Microphone.*iec958"                   # Hollyland wireless digital (stable)
    "Hollyland.*Wireless.*Microphone"                           # Hollyland wireless generic
    "Shenzhen.*Hollyland"                                       # Hollyland by manufacturer name
    "DJI.*Technology.*Co.*DJI.*MIC.*MINI.*iec958-stereo"  # prefer DJI MIC MINI digital
    "DJI.*MIC.*MINI.*iec958"  # DJI MIC MINI digital (stable pattern)
    "DJI.*Technology.*Co.*DJI.*MIC"  # DJI Technology Co DJI MIC (stable pattern)
    "DJI.*MIC.*MINI"         # DJI MIC MINI
    "DJI"                    # any DJI device
    "COMICA.*Comica.*VM10.*PRO.*iec958"  # Comica VM10 PRO digital
    "Generic.*USB2.0.*Device.*multichannel"  # USB2.0 Device (but not CSCTEK)
  )

  for pat in "${DEFAULTS[@]}"; do
    [[ -z "$pat" ]] && continue
    if selection_line=$(choose_source_by_pattern "$pat"); then
      # Exclude CSCTEK devices (wired microphones) from wireless microphone selection
      local matched_name matched_desc
      matched_name=$(echo -e "$selection_line" | cut -f1)
      matched_desc=$(echo -e "$selection_line" | cut -f3-)
      if echo "$matched_name $matched_desc" | grep -qiE "CSCTEK|USB.*Audio.*and.*HID.*mono|GeneralPlus|USB.?Audio.?Device|Comica|VM10"; then
        # Skip CSCTEK devices, continue to next pattern
        selection_line=""
        continue
      fi
      break
    fi
  done

  if [[ -z "${selection_line:-}" ]]; then
    echo "未通过默认规则匹配到输入设备。可用设备如下："
    print_sources
    selection_line=$(choose_source_interactive)
  fi

  local name index desc
  name=$(echo -e "$selection_line" | cut -f1)
  index=$(echo -e "$selection_line" | cut -f2)
  desc=$(echo -e "$selection_line" | cut -f3-)

  local volume="15%"
  echo "切换默认输入到: ${desc} [${name}] (index ${index})"
  set_default_source "$name"
  set_source_volume "$name" "$volume"
  echo "已切换，音量已设置为${volume}。"

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
    if bash "$aec_script"; then
      # AEC 会新建 ec_source 并设为默认输入，必须再设一次，否则界面/应用看到的仍是虚拟麦默认音量
      set_source_volume "$name" "$volume"
      set_source_volume "@DEFAULT_SOURCE@" "$volume"
      echo "AEC 开启后，硬件麦与默认输入音量已重新设置为 ${volume}。"
    else
      echo "警告: 开启 AEC 失败，已保持刚切换的麦克风。" >&2
    fi
  fi
}

main "$@"

