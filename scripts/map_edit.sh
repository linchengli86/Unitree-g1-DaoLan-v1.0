#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
source "$SCRIPT_DIR/env.sh"

WS_DIR="$NAV_WS"

# 设置显示环境变量（修复GUI相关错误）
export DISPLAY="${DISPLAY:-:0}"
export NO_AT_BRIDGE=1  # 避免AT-SPI错误

# 设置用户运行时环境（修复桌面环境缺失问题）
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"

# 设置ROS环境变量
export ROS_MASTER_URI="${ROS_MASTER_URI:-http://localhost:11311}"
export ROS_IP="${ROS_IP:-127.0.0.1}"
export ROS_HOSTNAME="${ROS_HOSTNAME:-localhost}"

# 禁用ROS 1 End-of-Life警告弹窗
export DISABLE_ROS1_EOL_WARNINGS=1

# 设置工作目录环境
export HOME="${HOME:?HOME 未设置}"
export USER="${USER:-$(id -un)}"

cd "$WS_DIR"
set +u
source "$WS_DIR/devel/setup.bash"
set -u

# 修复地图文件中的 -nan 值
MAP_FILE="$BASE_DIR/map/map.yaml"
if [[ -f "$MAP_FILE" ]]; then
  echo "检查并修复地图文件中的 -nan 值..."
  # 备份原文件
  cp "$MAP_FILE" "${MAP_FILE}.backup"
  # 替换 -nan 为 0.0
  sed -i 's/-nan/0.0/g' "$MAP_FILE"
  echo "地图文件已修复"
fi

# 启动地图编辑，保持前台运行以显示GUI界面
# 使用默认配置，让RViz自动处理地图加载
roslaunch ros_map_edit map_edit.launch


