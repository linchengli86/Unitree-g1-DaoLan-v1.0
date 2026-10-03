#!/usr/bin/env bash

set -euo pipefail

# 获取脚本目录
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
source "$SCRIPT_DIR/env.sh"

# 检查是否提供了脚本参数
if [ $# -eq 0 ]; then
    echo "错误: 必须提供要执行的Python脚本名称"
    echo "用法: $0 <script_name.py>"
    echo "示例: $0 multi_nav_complex_route.py"
    exit 1
fi

# 获取脚本名称参数
SCRIPT_NAME="$1"

# 设置工作目录
WORK_DIR="$BASE_DIR/PythonProject/point_nav/generated"
VENV_PATH="$VOICE_VENV_ACTIVATE"
NAV_PID_FILE="$PID_DIR/nav_script.pid"

is_nav_python_process() {
    local pid="$1"
    local cmdline
    if [ ! -r "/proc/$pid/cmdline" ]; then
        return 1
    fi
    cmdline=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
    [[ "$cmdline" =~ python ]] && [[ "$cmdline" =~ multi_nav_.*\.py ]]
}

stop_nav_process_tree() {
    local pid="$1"
    local timeout="${2:-10}"

    pkill -P "$pid" TERM 2>/dev/null || true
    kill -TERM "$pid" 2>/dev/null || true

    for _ in $(seq "$timeout"); do
        if ! kill -0 "$pid" 2>/dev/null; then
            pkill -P "$pid" KILL 2>/dev/null || true
            return 0
        fi
        sleep 1
    done

    pkill -P "$pid" KILL 2>/dev/null || true
    kill -KILL "$pid" 2>/dev/null || true
    sleep 1
    ! kill -0 "$pid" 2>/dev/null
}

cleanup_existing_nav_pid() {
    if [ ! -f "$NAV_PID_FILE" ]; then
        return 0
    fi

    local existing_pid
    existing_pid=$(cat "$NAV_PID_FILE" 2>/dev/null || true)
    if [ -z "$existing_pid" ]; then
        rm -f "$NAV_PID_FILE"
        return 0
    fi

    if ! kill -0 "$existing_pid" 2>/dev/null; then
        log "发现失效的导航PID文件，已清理: $existing_pid"
        rm -f "$NAV_PID_FILE"
        return 0
    fi

    if ! is_nav_python_process "$existing_pid"; then
        log "PID文件指向的进程不是multi_nav脚本，已清理: $existing_pid"
        rm -f "$NAV_PID_FILE"
        return 0
    fi

    log "检测到已有导航进程运行(PID: $existing_pid)，先停止旧进程并重启"
    if stop_nav_process_tree "$existing_pid" 10; then
        log "旧导航进程已停止: $existing_pid"
        rm -f "$NAV_PID_FILE"
        return 0
    fi

    echo "错误: 无法停止已有导航进程(PID: $existing_pid)" >&2
    return 1
}

# 检查工作目录是否存在
if [ ! -d "$WORK_DIR" ]; then
    echo "错误: 工作目录不存在: $WORK_DIR"
    exit 1
fi

# 检查虚拟环境是否存在
if [ ! -f "$VENV_PATH" ]; then
    echo "错误: 虚拟环境不存在: $VENV_PATH"
    exit 1
fi

# 检查ROS设置文件是否存在
if [ ! -f "$ROS_SETUP" ]; then
    echo "错误: ROS设置文件不存在: $ROS_SETUP"
    exit 1
fi

# 启动前先清理/停止已有导航进程（仅允许单导航实例）
cleanup_existing_nav_pid

# 无条件先重新生成导航脚本
log "正在重新生成导航脚本..."
cd "$VOICE_APP_DIR"
"$VOICE_APP_DIR/.venv/bin/python" ./regenerate_navigation_scripts.py
log "导航脚本重新生成完成"

# 生成后检查Python脚本是否存在
SCRIPT_PATH="$WORK_DIR/$SCRIPT_NAME"
if [ ! -f "$SCRIPT_PATH" ]; then
    echo "错误: Python脚本不存在: $SCRIPT_PATH"
    exit 1
fi

# 切换到工作目录
cd "$WORK_DIR"

# 为远程/后台环境准备音频与会话环境
# 这些环境变量使进程能连接到用户会话的DBus与PulseAudio/ PipeWire
export USER_UID="$(id -u)"
export XDG_RUNTIME_DIR="/run/user/${USER_UID}"
export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"
# PulseAudio/ PipeWire 通常监听此socket
if [ -S "${XDG_RUNTIME_DIR}/pulse/native" ]; then
    export PULSE_SERVER="unix:${XDG_RUNTIME_DIR}/pulse/native"
fi
# 避免某些环境下的AT-SPI DBus错误刷屏
export NO_AT_BRIDGE=1
export ALSA_CARD=0  # 使用第一个音频设备
export ALSA_DEVICE=0
# 设置显示环境变量（修复pynput X连接错误）
export DISPLAY="${DISPLAY:-:0}"
# 激活虚拟环境
source "$VENV_PATH"

# 设置ROS环境
set +u  # 临时关闭未绑定变量检查
source "$ROS_SETUP"
set -u  # 重新启用未绑定变量检查

# 执行Python脚本
log "正在执行: $SCRIPT_NAME"
python "$SCRIPT_NAME" &
NAV_PID=$!

# 保存进程ID到文件
echo "$NAV_PID" > "$PID_DIR/nav_script.pid"
log "导航脚本已启动，PID: $NAV_PID"

# 脚本退出时清理PID文件（避免异常残留）
cleanup_pid_on_exit() {
    if [ -f "$NAV_PID_FILE" ]; then
        local current_pid
        current_pid=$(cat "$NAV_PID_FILE" 2>/dev/null || true)
        if [ "$current_pid" = "$NAV_PID" ] && ! kill -0 "$NAV_PID" 2>/dev/null; then
            rm -f "$NAV_PID_FILE"
        fi
    fi
}
trap cleanup_pid_on_exit EXIT

# 等待进程结束
wait "$NAV_PID"
log "导航脚本执行完成"
