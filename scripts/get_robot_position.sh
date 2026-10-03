#!/usr/bin/env bash
# 获取机器人在世界坐标系中的位置和角度
# 用法: ./get_robot_position.sh [--format json|shell|env]

set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
source "$SCRIPT_DIR/env.sh"

# 默认输出格式
OUTPUT_FORMAT="${1:-json}"

# Python脚本路径
PYTHON_SCRIPT="$NAV_WS/src/tool/scripts/get_robot_pose.py"

# 日志函数
log() { echo "[$(date +'%F %T')] $*"; }
warn() { echo "[$(date +'%F %T')] [WARN] $*" >&2; }
error() { echo "[$(date +'%F %T')] [ERROR] $*" >&2; }

# 检查Python脚本是否存在
if [[ ! -f "$PYTHON_SCRIPT" ]]; then
    error "Python脚本不存在: $PYTHON_SCRIPT"
    exit 1
fi

# 检查ROS环境
if [[ ! -f "$ROS_SETUP" ]]; then
    error "ROS环境未找到: $ROS_SETUP"
    exit 1
fi

# 检查导航工作空间
if [[ ! -d "$NAV_WS" ]]; then
    error "导航工作空间未找到: $NAV_WS"
    exit 1
fi

# 检查ROS Master是否运行
if ! bash -lc "source '$ROS_SETUP' && rosparam get /rosdistro >/dev/null 2>&1"; then
    error "ROS Master未运行，请先启动导航系统"
    exit 1
fi

# 调用Python脚本获取位置信息
log "正在获取机器人位姿..."
JSON_OUTPUT=$(bash -lc "source '$ROS_SETUP' && cd '$NAV_WS' && source devel/setup.bash && python3 '$PYTHON_SCRIPT' --json" 2>&1)

# 解析JSON结果（使用Python或jq，优先使用jq）
if command -v jq >/dev/null 2>&1; then
    # 使用jq解析
    SUCCESS=$(echo "$JSON_OUTPUT" | jq -r '.success // false' 2>/dev/null || echo "false")
    if [[ "$SUCCESS" == "true" ]]; then
        X=$(echo "$JSON_OUTPUT" | jq -r '.x // 0')
        Y=$(echo "$JSON_OUTPUT" | jq -r '.y // 0')
        Z=$(echo "$JSON_OUTPUT" | jq -r '.z // 0')
        ROLL=$(echo "$JSON_OUTPUT" | jq -r '.roll // 0')
        PITCH=$(echo "$JSON_OUTPUT" | jq -r '.pitch // 0')
        YAW=$(echo "$JSON_OUTPUT" | jq -r '.yaw // 0')
        ERROR_MSG=""
    else
        ERROR_MSG=$(echo "$JSON_OUTPUT" | jq -r '.error // "获取位姿失败"')
    fi
else
    # 使用Python解析（如果没有jq）
    PYTHON_PARSE_SCRIPT='import sys, json
try:
    data = json.load(sys.stdin)
    success = data.get("success", False)
    print("SUCCESS=" + str(success))
    if success:
        print("X=" + str(data.get("x", 0.0)))
        print("Y=" + str(data.get("y", 0.0)))
        print("Z=" + str(data.get("z", 0.0)))
        print("ROLL=" + str(data.get("roll", 0.0)))
        print("PITCH=" + str(data.get("pitch", 0.0)))
        print("YAW=" + str(data.get("yaw", 0.0)))
        print("ERROR=")
    else:
        print("X=0")
        print("Y=0")
        print("Z=0")
        print("ROLL=0")
        print("PITCH=0")
        print("YAW=0")
        print("ERROR=" + str(data.get("error", "获取位姿失败")))
except Exception as e:
    print("SUCCESS=False")
    print("X=0")
    print("Y=0")
    print("Z=0")
    print("ROLL=0")
    print("PITCH=0")
    print("YAW=0")
    print("ERROR=" + str(e))'
    
    PARSED=$(echo "$JSON_OUTPUT" | python3 -c "$PYTHON_PARSE_SCRIPT" 2>/dev/null)
    if [[ -n "$PARSED" ]]; then
        eval "$PARSED"
    else
        SUCCESS="false"
        ERROR_MSG="JSON解析失败"
    fi
fi

# 统一保留三位小数
fmt3() { printf "%.3f" "$1"; }
X3=$(fmt3 "${X:-0}")
Y3=$(fmt3 "${Y:-0}")
Z3=$(fmt3 "${Z:-0}")
ROLL3=$(fmt3 "${ROLL:-0}")
PITCH3=$(fmt3 "${PITCH:-0}")
YAW3=$(fmt3 "${YAW:-0}")

# 检查是否成功（兼容 true / True）
if [[ "$SUCCESS" == "true" || "$SUCCESS" == "True" ]]; then
    # 根据输出格式返回结果
    case "$OUTPUT_FORMAT" in
        json)
            if command -v jq >/dev/null 2>&1; then
                # 使用jq对关键字段保留三位小数，保持其他字段不变
                echo "$JSON_OUTPUT" | jq '(.x,.y,.z,.roll,.pitch,.yaw) |= ((. * 1000 | round) / 1000)'
            else
                # 无jq时返回简化JSON（仅包含关键字段，已保留三位）
                echo "{\"success\": true, \"x\": $X3, \"y\": $Y3, \"z\": $Z3, \"roll\": $ROLL3, \"pitch\": $PITCH3, \"yaw\": $YAW3}"
            fi
            ;;
        shell)
            # Shell变量格式输出
            echo "ROBOT_X=$X3"
            echo "ROBOT_Y=$Y3"
            echo "ROBOT_Z=$Z3"
            echo "ROBOT_ROLL=$ROLL3"
            echo "ROBOT_PITCH=$PITCH3"
            echo "ROBOT_YAW=$YAW3"
            echo "ROBOT_SUCCESS=true"
            ;;
        env)
            # 导出为环境变量（使用source调用）
            export ROBOT_X="$X3"
            export ROBOT_Y="$Y3"
            export ROBOT_Z="$Z3"
            export ROBOT_ROLL="$ROLL3"
            export ROBOT_PITCH="$PITCH3"
            export ROBOT_YAW="$YAW3"
            export ROBOT_SUCCESS="true"
            # 输出变量定义（供source使用）
            echo "export ROBOT_X='$X3'"
            echo "export ROBOT_Y='$Y3'"
            echo "export ROBOT_Z='$Z3'"
            echo "export ROBOT_ROLL='$ROLL3'"
            echo "export ROBOT_PITCH='$PITCH3'"
            echo "export ROBOT_YAW='$YAW3'"
            echo "export ROBOT_SUCCESS='true'"
            ;;
        simple)
            # 简单格式：只输出x, y, yaw
            echo "$X3 $Y3 $YAW3"
            ;;
        *)
            # 默认：人类可读格式
            echo "=== 机器人位姿 ==="
            echo "位置: x=$X3, y=$Y3, z=$Z3"
            echo "角度: roll=${ROLL3}°, pitch=${PITCH3}°, yaw=${YAW3}°"
            echo "================"
            ;;
    esac
    exit 0
else
    # 获取失败，输出错误信息
    error "获取位姿失败: $ERROR_MSG"
    if [[ "$OUTPUT_FORMAT" == "json" ]]; then
        # 如果Python已输出JSON，直接透传；否则构造一个简单的错误JSON
        if echo "$JSON_OUTPUT" | grep -q '"success"'; then
            echo "$JSON_OUTPUT"
        else
            echo "{\"success\": false, \"error\": \"${ERROR_MSG:-获取位姿失败}\"}"
        fi
    else
        echo "ROBOT_SUCCESS=false"
        echo "ROBOT_ERROR='${ERROR_MSG:-获取位姿失败}'"
    fi
    exit 1
fi


