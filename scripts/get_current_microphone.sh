#!/usr/bin/env bash
# 获取当前麦克风（有线话筒1、有线话筒2、无线话筒）
# 用法: ./get_current_microphone.sh [--format json|shell|env]

set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
source "$SCRIPT_DIR/env.sh"

# 默认输出格式
OUTPUT_FORMAT="${1:-json}"

# 设备名称特征（简短子串，便于识别）
# 有线话筒1
WIRED_MIC_1_PATTERN="USB_Audio_and_HID"
# 有线话筒2
WIRED_MIC_2_PATTERN="Lenovo_Lenovo_510_Camera"
# 无线话筒
WIRELESS_MIC_PATTERN="Wireless_Microphone"

# 日志函数
log() { echo "[$(date +'%F %T')] $*"; }
warn() { echo "[$(date +'%F %T')] [WARN] $*" >&2; }
error() { echo "[$(date +'%F %T')] [ERROR] $*" >&2; }

# 检查 pactl
if ! command -v pactl >/dev/null 2>&1; then
    error "未找到 pactl，请确保 PulseAudio 已安装"
    exit 1
fi

# 从 pactl info 解析默认信源（兼容中文「默认信源」与英文 Default Source）
get_default_source() {
    pactl info 2>/dev/null | grep -E "默认信源|Default Source" | sed -E 's/.*[：:][[:space:]]*//' | tr -d '\r' | xargs
}

# 判断源名称对应的麦克风类型
get_mic_type() {
    local name="$1"
    if [[ "$name" == *"$WIRED_MIC_1_PATTERN"* ]]; then
        echo "有线话筒1"
    elif [[ "$name" == *"$WIRED_MIC_2_PATTERN"* ]]; then
        echo "有线话筒2"
    elif [[ "$name" == *"$WIRELESS_MIC_PATTERN"* ]]; then
        echo "无线话筒"
    else
        echo "unknown"
    fi
}

# 主逻辑：以 pactl info 中的「默认信源」为准
DEFAULT_SOURCE=$(get_default_source)
WIRED_1_ACTIVE=false
WIRED_2_ACTIVE=false
WIRELESS_ACTIVE=false
CURRENT_SOURCE_ID=""
CURRENT_SOURCE_NAME="$DEFAULT_SOURCE"
CURRENT_MIC_DISPLAY="unknown"

# 仅当默认源是麦克风输入（alsa_input.*）时才识别类型
if [[ -n "$DEFAULT_SOURCE" && "$DEFAULT_SOURCE" == alsa_input.* ]]; then
    CURRENT_MIC_DISPLAY=$(get_mic_type "$DEFAULT_SOURCE")
    case "$CURRENT_MIC_DISPLAY" in
        有线话筒1) WIRED_1_ACTIVE=true ;;
        有线话筒2) WIRED_2_ACTIVE=true ;;
        无线话筒)   WIRELESS_ACTIVE=true ;;
        *) ;;
    esac
    # 从 pactl list sources short 中解析该默认源的 index
    while IFS= read -r line; do
        idx=$(echo "$line" | awk '{print $1}')
        name=$(echo "$line" | awk '{print $2}')
        if [[ "$name" == "$DEFAULT_SOURCE" ]]; then
            CURRENT_SOURCE_ID="$idx"
            break
        fi
    done < <(pactl list sources short 2>/dev/null)
fi

# 根据输出格式返回结果（与 get_robot_position.sh 一致）
case "$OUTPUT_FORMAT" in
    json)
        if command -v jq >/dev/null 2>&1; then
            jq -n \
                --arg cur "$CURRENT_MIC_DISPLAY" \
                --arg id "$CURRENT_SOURCE_ID" \
                --arg name "$CURRENT_SOURCE_NAME" \
                --arg w1 "$WIRED_1_ACTIVE" \
                --arg w2 "$WIRED_2_ACTIVE" \
                --arg wl "$WIRELESS_ACTIVE" \
                '{ success: true,
                   current_microphone: $cur,
                   wired_mic_1_active: ($w1 == "true"),
                   wired_mic_2_active: ($w2 == "true"),
                   wireless_mic_active: ($wl == "true"),
                   source_id: $id,
                   source_name: $name }'
        else
            # 无 jq 时手写 JSON（布尔用 true/false）
            W1_J=$( [[ "$WIRED_1_ACTIVE" == true ]] && echo "true" || echo "false" )
            W2_J=$( [[ "$WIRED_2_ACTIVE" == true ]] && echo "true" || echo "false" )
            WL_J=$( [[ "$WIRELESS_ACTIVE" == true ]] && echo "true" || echo "false" )
            echo "{\"success\": true, \"current_microphone\": \"$CURRENT_MIC_DISPLAY\", \"wired_mic_1_active\": $W1_J, \"wired_mic_2_active\": $W2_J, \"wireless_mic_active\": $WL_J, \"source_id\": \"$CURRENT_SOURCE_ID\", \"source_name\": \"$CURRENT_SOURCE_NAME\"}"
        fi
        ;;
    shell)
        echo "MIC_CURRENT=$CURRENT_MIC_DISPLAY"
        echo "MIC_WIRED_1_ACTIVE=$WIRED_1_ACTIVE"
        echo "MIC_WIRED_2_ACTIVE=$WIRED_2_ACTIVE"
        echo "MIC_WIRELESS_ACTIVE=$WIRELESS_ACTIVE"
        echo "MIC_SOURCE_ID=$CURRENT_SOURCE_ID"
        echo "MIC_SOURCE_NAME=$CURRENT_SOURCE_NAME"
        echo "MIC_SUCCESS=true"
        ;;
    env)
        export MIC_CURRENT="$CURRENT_MIC_DISPLAY"
        export MIC_WIRED_1_ACTIVE="$WIRED_1_ACTIVE"
        export MIC_WIRED_2_ACTIVE="$WIRED_2_ACTIVE"
        export MIC_WIRELESS_ACTIVE="$WIRELESS_ACTIVE"
        export MIC_SOURCE_ID="$CURRENT_SOURCE_ID"
        export MIC_SOURCE_NAME="$CURRENT_SOURCE_NAME"
        export MIC_SUCCESS="true"
        echo "export MIC_CURRENT='$CURRENT_MIC_DISPLAY'"
        echo "export MIC_WIRED_1_ACTIVE='$WIRED_1_ACTIVE'"
        echo "export MIC_WIRED_2_ACTIVE='$WIRED_2_ACTIVE'"
        echo "export MIC_WIRELESS_ACTIVE='$WIRELESS_ACTIVE'"
        echo "export MIC_SOURCE_ID='$CURRENT_SOURCE_ID'"
        echo "export MIC_SOURCE_NAME='$CURRENT_SOURCE_NAME'"
        echo "export MIC_SUCCESS='true'"
        ;;
    *)
        echo "=== 当前麦克风 ==="
        echo "当前麦克风: $CURRENT_MIC_DISPLAY"
        echo "有线话筒1: $WIRED_1_ACTIVE"
        echo "有线话筒2: $WIRED_2_ACTIVE"
        echo "无线话筒: $WIRELESS_ACTIVE"
        echo "源 ID: $CURRENT_SOURCE_ID"
        echo "源名称: $CURRENT_SOURCE_NAME"
        echo "================"
        ;;
esac
exit 0

