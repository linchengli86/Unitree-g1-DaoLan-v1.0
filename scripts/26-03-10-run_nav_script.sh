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
WORK_DIR="/home/ztx/robot/DaoLan/PythonProject/point_nav/generated"
VENV_PATH="/home/ztx/robot/DaoLan/PythonProject/py-xiaozhi-main/.venv/bin/activate"
ROS_SETUP="/opt/ros/noetic/setup.bash"

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

# 无条件先重新生成导航脚本
log "正在重新生成导航脚本..."
cd "/home/ztx/robot/DaoLan/PythonProject/py-xiaozhi-main"
/home/ztx/robot/DaoLan/PythonProject/py-xiaozhi-main/.venv/bin/python ./regenerate_navigation_scripts.py
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

# 等待进程结束
wait "$NAV_PID"
log "导航脚本执行完成"

