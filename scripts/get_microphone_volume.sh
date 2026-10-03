#!/usr/bin/env bash

# Get microphone volume for current, wired, and wireless microphones
# - Shows current default microphone volume
# - Shows wired microphone volume (CSCTEK devices)
# - Shows wireless microphone volume (Hollyland, DJI devices)
#
# Usage:
#   bash get_microphone_volume.sh           # normal mode
#   bash get_microphone_volume.sh --debug   # debug mode (shows raw data)
#   DEBUG=1 bash get_microphone_volume.sh   # debug mode (env var)

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
  sources_short=$(pactl list short sources 2>/dev/null || true)
  sources_full=$(pactl list sources 2>/dev/null || true)

  if [[ -z "$sources_short" ]]; then
    return 0
  fi

  # Parse descriptions per source name
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" ]] && continue
    local index name
    index=$(echo "$line" | awk '{print $1}')
    name=$(echo "$line" | awk '{print $2}')
    
    # Skip if name is empty
    [[ -z "$name" ]] && continue
    
    # Skip monitor devices (output monitoring)
    if echo "$name" | grep -q "\.monitor$"; then
      continue
    fi
    
    # Grab Description: line from the full listing block for this source
    # Support both "Description:" (English) and "描述：" (Chinese)
    local desc
    if [[ -n "$sources_full" ]]; then
      desc=$(echo "$sources_full" | awk -v n="${name}" '
        BEGIN { found=0 }
        /^[[:space:]]*(名称|Name):[[:space:]]*/ {
          if ($0 ~ n) { found=1 }
          else { found=0 }
          next
        }
        found && /^[[:space:]]*(描述|Description):/ { 
          gsub(/^[[:space:]]*(描述|Description):[[:space:]]*/, ""); 
          print; 
          exit 
        }
        found && /^[[:space:]]*$/ && found>0 { exit }
      ' | head -1)
    fi
    
    if [[ -z "$desc" ]]; then
      desc="$name"
    fi
    echo -e "${name}\t${index}\t${desc}"
  done <<< "$sources_short"
}

# Get volume percentage for a source by name
get_source_volume() {
  local source_name="$1"
  [[ -z "$source_name" ]] && echo "N/A" && return 1
  
  # Use the verified working method:
  # pactl list sources | awk '/名称：source_name/,/^$/' | grep "^[[:space:]]*音量：" | grep -v "基础音量" | grep -oE '/[[:space:]]*[0-9]{1,3}%' | head -1 | sed 's|/[[:space:]]*||; s|%||'
  # Note: Use grep -v "基础音量" to exclude "基础音量：" line
  
  local volume_percent
  
  # Try Chinese format first (名称：)
  # Match only "音量：" line, exclude "基础音量：" line
  volume_percent=$(pactl list sources 2>&1 | awk "/名称：${source_name}/,/^$/" | grep "音量：" | grep -v "基础音量" | grep -oE '/[[:space:]]*[0-9]{1,3}%' | head -1 | sed 's|/[[:space:]]*||; s|%||' || true)
  
  # Fallback to English format if Chinese didn't work
  if [[ -z "$volume_percent" ]]; then
    volume_percent=$(pactl list sources 2>&1 | awk "/Name: ${source_name}/,/^$/" | grep "Volume:" | grep -v "Base Volume" | grep -oE '/[[:space:]]*[0-9]{1,3}%' | head -1 | sed 's|/[[:space:]]*||; s|%||' || true)
  fi
  
  # Validate the result
  if [[ -n "$volume_percent" ]] && [[ "$volume_percent" =~ ^[0-9]+$ ]] && [[ "$volume_percent" -ge 0 ]] && [[ "$volume_percent" -le 200 ]]; then
    echo "$volume_percent"
    return 0
  fi
  
  echo "N/A"
  return 1
}

# Get mute status for a source by name
get_source_mute() {
  local source_name="$1"
  [[ -z "$source_name" ]] && echo "unknown" && return 1
  
  # Parse from pactl list sources (works for both English and Chinese PulseAudio)
  local sources_full
  sources_full=$(pactl list sources 2>&1 || true)
  
  if [[ -z "$sources_full" ]]; then
    echo "unknown"
    return 1
  fi
  
  # Extract the entire source block first
  # Support both "Name: " (English) and "名称：" (Chinese)
  local source_block
  source_block=$(echo "$sources_full" | awk -v n="${source_name}" '
    BEGIN { found=0; block="" }
    /^[[:space:]]*(名称|Name):[[:space:]]*/ {
      if ($0 ~ n) { found=1 }
      else { found=0; block="" }
    }
    found { 
      block=block $0 "\n"
      if (/^[[:space:]]*$/) { exit }
    }
    END { if (found) print block }
  ')
  
  if [[ -z "$source_block" ]]; then
    echo "unknown"
    return 1
  fi
  
  # Extract mute line from the block
  # Support both "Mute:" (English) and "静音：" (Chinese)
  local mute_line
  mute_line=$(echo "$source_block" | grep -E '^[[:space:]]*(静音|Mute):' | head -1)
  
  if [[ -z "$mute_line" ]]; then
    echo "unknown"
    return 1
  fi
  
  # Extract mute status
  # Format examples:
  #   "静音：否" (Chinese: no)
  #   "静音：是" (Chinese: yes)
  #   "Mute: no" (English)
  #   "Mute: yes" (English)
  local mute_status
  
  # Check for Chinese format first
  if echo "$mute_line" | grep -qE '(静音|Mute):[[:space:]]*(是|否)'; then
    if echo "$mute_line" | grep -qE '(静音|Mute):[[:space:]]*是'; then
      echo "yes"
      return 0
    else
      echo "no"
      return 0
    fi
  fi
  
  # Check for English format
  mute_status=$(echo "$mute_line" | grep -oE '(yes|no)' | head -1 | tr -d '\n\r\t ' || true)
  
  if [[ -n "$mute_status" ]] && [[ "$mute_status" =~ ^(yes|no)$ ]]; then
    echo "$mute_status"
    return 0
  fi
  
  echo "unknown"
  return 1
}

# Find wired microphone (CSCTEK devices - USB Audio and HID)
find_wired_microphone() {
  local IFS=$'\n'
  local patterns=(
    "USB.*Audio.*and.*HID"   # USB Audio and HID in description (most reliable)
    "USB.*Audio.*and.*HID.*单声道"
    "USB.*Audio.*and.*HID.*mono"
    "USB.*Audio.*and.*HID.*multichannel"
    "CSCTEK.*USB.*Audio.*and.*HID.*mono-fallback"
    "CSCTEK.*USB.*Audio.*and.*HID.*multichannel-input"
    "CSCTEK.*USB.*Audio.*and.*HID"
  )
  
  local sources_list
  sources_list=$(list_sources 2>/dev/null || true)
  
  if [[ -z "$sources_list" ]]; then
    return 1
  fi
  
  for pat in "${patterns[@]}"; do
    while IFS= read -r item || [[ -n "$item" ]]; do
      [[ -z "$item" ]] && continue
      local name desc
      name=$(echo -e "$item" | cut -f1)
      desc=$(echo -e "$item" | cut -f3-)
      # Exclude wireless microphones (Hollyland, DJI)
      if echo "$name $desc" | grep -qiE "(Hollyland|DJI.*MIC)"; then
        continue
      fi
      if echo "$name $desc" | grep -iE -- "$pat" >/dev/null 2>&1; then
        echo -e "$item"
        return 0
      fi
    done <<< "$sources_list"
  done
  return 1
}

# Find wireless microphone (Hollyland, DJI devices, excluding CSCTEK)
find_wireless_microphone() {
  local IFS=$'\n'
  local patterns=(
    "Shenzhen.*Hollyland.*Wireless.*Microphone.*iec958-stereo"
    "Hollyland.*Wireless.*Microphone.*iec958"
    "Hollyland.*Wireless.*Microphone"
    "Shenzhen.*Hollyland"
    "DJI.*Technology.*Co.*DJI.*MIC.*MINI.*iec958-stereo"
    "DJI.*MIC.*MINI.*iec958"
    "DJI.*Technology.*Co.*DJI.*MIC"
    "DJI.*MIC.*MINI"
    "DJI"
  )
  
  local sources_list
  sources_list=$(list_sources 2>/dev/null || true)
  
  if [[ -z "$sources_list" ]]; then
    return 1
  fi
  
  for pat in "${patterns[@]}"; do
    while IFS= read -r item || [[ -n "$item" ]]; do
      [[ -z "$item" ]] && continue
      local name desc
      name=$(echo -e "$item" | cut -f1)
      desc=$(echo -e "$item" | cut -f3-)
      # Exclude CSCTEK devices (wired microphones)
      if echo "$name $desc" | grep -qiE "CSCTEK|USB.*Audio.*and.*HID.*mono" 2>/dev/null; then
        continue
      fi
      if echo "$name $desc" | grep -iE -- "$pat" >/dev/null 2>&1; then
        echo -e "$item"
        return 0
      fi
    done <<< "$sources_list"
  done
  return 1
}

# Format volume display with mute status
format_volume_display() {
  local volume="$1"
  local mute="$2"
  local volume_str
  
  if [[ "$volume" == "N/A" ]]; then
    volume_str="N/A"
  else
    volume_str="${volume}%"
  fi
  
  if [[ "$mute" == "yes" ]]; then
    echo "${volume_str} (静音)"
  else
    echo "${volume_str}"
  fi
}

get_default_source_name() {
  local src
  # Try pactl get-default-source (may not be available on some systems)
  if src=$(pactl get-default-source 2>/dev/null || true); then
    # Some locales output "未指定有效的命令。" when unsupported – treat as empty
    if [[ -n "$src" ]] && ! echo "$src" | grep -qiE "未指定|invalid|error"; then
      echo "$src"
      return 0
    fi
  fi

  # Try pactl info (certain locale may not include Default Source)
  if src=$(pactl info 2>/dev/null | grep -i "Default Source" | awk -F': ' '{print $2}' | head -1); then
    src=$(echo "$src" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
    if [[ -n "$src" ]]; then
      echo "$src"
      return 0
    fi
  fi

  # Fallback: use pacmd info
  if command -v pacmd >/dev/null 2>&1; then
    src=$(pacmd info 2>/dev/null | grep -i "Default source name" | awk -F': ' '{print $2}' | head -1 | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
    if [[ -n "$src" ]]; then
      echo "$src"
      return 0
    fi
  fi

  echo ""
  return 1
}

main() {
  # Check if DEBUG mode is enabled
  local DEBUG="${DEBUG:-0}"
  
  if [[ "$DEBUG" == "1" ]] || [[ "${1:-}" == "--debug" ]]; then
    echo "=== 调试信息：测试 pactl 命令 ==="
    echo "测试: pactl get-default-source"
    pactl get-default-source 2>&1 | sed 's/^/  /' || echo "  (未设置)"
    echo ""
    
    echo "测试: pactl list short sources"
    pactl list short sources 2>&1 | head -5 | sed 's/^/  /'
    echo ""
  fi
  
  echo "=== 话筒音量信息 ==="
  echo ""
  
  # Get all sources first
  local IFS=$'\n'
  local all_items=($(list_sources))
  
  if [[ "$DEBUG" == "1" ]] || [[ "${1:-}" == "--debug" ]]; then
    echo "找到 ${#all_items[@]} 个音频输入设备"
    if [[ ${#all_items[@]} -gt 0 ]]; then
      local test_name
      test_name=$(echo -e "${all_items[0]}" | cut -f1)
      echo "测试第一个设备: $test_name"
      echo "原始 pactl get-source-volume 输出:"
      local test_output
      test_output=$(pactl get-source-volume "$test_name" 2>&1 || echo "ERROR")
      echo "$test_output" | sed 's/^/  /'
      echo ""
      
      echo "原始 pactl list sources 中该设备的 Volume 行:"
      pactl list sources 2>&1 | awk -v n="${test_name}" '
        BEGIN { found=0 }
        /^[[:space:]]*(名称|Name):[[:space:]]*/ {
          if ($0 ~ n) { found=1 }
          else { found=0 }
          next
        }
        found && /^[[:space:]]*(音量|Volume):/ { 
          print
          exit 
        }
        found && /^[[:space:]]*$/ && found>0 { exit }
      ' | sed 's/^/  /'
      echo ""
      echo "---"
      echo ""
    fi
  fi
  
  if [[ ${#all_items[@]} -eq 0 ]]; then
    echo "错误: 未找到任何音频输入设备。"
    echo "请检查:"
    echo "  1. 音频设备是否已连接"
    echo "  2. PulseAudio/PipeWire 是否正在运行"
    echo "  3. 运行 'pactl list sources' 查看详细信息"
    exit 1
  fi
  
  # Identify wired and wireless microphones once (reuse later)
  local wired_item wired_name wired_desc
  if wired_item=$(find_wired_microphone 2>/dev/null); then
    wired_name=$(echo -e "$wired_item" | cut -f1)
    wired_desc=$(echo -e "$wired_item" | cut -f3-)
  fi

  local wireless_item wireless_name wireless_desc
  if wireless_item=$(find_wireless_microphone 2>/dev/null); then
    wireless_name=$(echo -e "$wireless_item" | cut -f1)
    wireless_desc=$(echo -e "$wireless_item" | cut -f3-)
  fi

  # Get current default microphone
  local current_default
  current_default=$(get_default_source_name || echo "")
  
  if [[ -n "$current_default" ]]; then
    local current_volume current_mute current_desc
    current_volume=$(get_source_volume "$current_default" 2>/dev/null || echo "N/A")
    current_mute=$(get_source_mute "$current_default" 2>/dev/null || echo "unknown")
    
    # Get description for current default
    for item in "${all_items[@]}"; do
      local name
      name=$(echo -e "$item" | cut -f1)
      if [[ "$name" == "$current_default" ]]; then
        current_desc=$(echo -e "$item" | cut -f3-)
        break
      fi
    done
    
    if [[ -z "${current_desc:-}" ]]; then
      current_desc="$current_default"
    fi
    
    local current_type="未知"
    if [[ -n "${wired_name:-}" && "$current_default" == "$wired_name" ]]; then
      current_type="有线"
    elif [[ -n "${wireless_name:-}" && "$current_default" == "$wireless_name" ]]; then
      current_type="无线"
    fi

    echo "当前默认话筒:"
    echo "  设备: ${current_desc} [${current_default}] (${current_type})"
    echo "  音量: $(format_volume_display "$current_volume" "$current_mute")"
    echo ""
  else
    echo "当前默认话筒: 未设置"
    echo ""
  fi
  
  # Get wired microphone
  if [[ -n "${wired_name:-}" ]]; then
    local wired_volume wired_mute
    wired_volume=$(get_source_volume "$wired_name" 2>/dev/null || echo "N/A")
    wired_mute=$(get_source_mute "$wired_name" 2>/dev/null || echo "unknown")
    
    echo "有线话筒:"
    echo "  设备: ${wired_desc} [${wired_name}]"
    echo "  音量: $(format_volume_display "$wired_volume" "$wired_mute")"
    
    # If volume is N/A, show debug info
    if [[ "$wired_volume" == "N/A" ]]; then
      echo "  [调试] 尝试获取原始数据:" >&2
      echo "  [调试] pactl get-source-volume $wired_name:" >&2
      pactl get-source-volume "$wired_name" 2>&1 | sed 's/^/    /' >&2 || echo "    (命令失败)" >&2
    fi
    echo ""
  else
    echo "有线话筒: 未找到"
    echo ""
  fi
  
  # Get wireless microphone
  if [[ -n "${wireless_name:-}" ]]; then
    local wireless_volume wireless_mute
    wireless_volume=$(get_source_volume "$wireless_name" 2>/dev/null || echo "N/A")
    wireless_mute=$(get_source_mute "$wireless_name" 2>/dev/null || echo "unknown")
    
    echo "无线话筒:"
    echo "  设备: ${wireless_desc} [${wireless_name}]"
    echo "  音量: $(format_volume_display "$wireless_volume" "$wireless_mute")"
    echo ""
  else
    echo "无线话筒: 未找到"
    echo ""
  fi
  
  echo "=== 所有可用话筒设备 ==="
  if [[ ${#all_items[@]} -eq 0 ]]; then
    echo "  无可用设备"
  else
    local i=1
    for item in "${all_items[@]}"; do
      local name index desc
      name=$(echo -e "$item" | cut -f1)
      index=$(echo -e "$item" | cut -f2)
      desc=$(echo -e "$item" | cut -f3-)
      local vol mute
      vol=$(get_source_volume "$name" 2>/dev/null || echo "N/A")
      mute=$(get_source_mute "$name" 2>/dev/null || echo "unknown")
      local status_marker=""
      if [[ "$name" == "$current_default" ]]; then
        status_marker=" [当前默认]"
      fi
      echo "  ${i}) ${desc} [${name}] - $(format_volume_display "$vol" "$mute")${status_marker}"
      i=$((i+1))
    done
  fi
}

main "$@"

