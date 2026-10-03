#!/usr/bin/env bash
# 通用环境配置与工具函数（Ubuntu 服务器）
set -euo pipefail

# ----- 可按需修改的参数（也可通过环境变量覆盖）-----
# 默认从本文件位置推导项目根目录，避免绑定具体用户名与安装路径。
_ENV_SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
_DEFAULT_BASE_DIR=$(cd "$_ENV_SCRIPT_DIR/.." && pwd)
export BASE_DIR=${BASE_DIR:-$_DEFAULT_BASE_DIR}
export ROS_SETUP=${ROS_SETUP:-/opt/ros/noetic/setup.bash}
export NAV_WS=${NAV_WS:-$BASE_DIR/G1Nav2D}
export CONTROL_DIR=${CONTROL_DIR:-$BASE_DIR/unitree_sdk2_python/example/g1/high_level}
# 动作执行目录（与导航脚本 execution_path 对齐）
export ACTION_EXECUTION_PATH=${ACTION_EXECUTION_PATH:-$HOME/robot_program/unitree_sdk2/build2/bin}
export VOICE_APP_DIR=${VOICE_APP_DIR:-$BASE_DIR/PythonProject/py-xiaozhi-main}
export VOICE_VENV_ACTIVATE=${VOICE_VENV_ACTIVATE:-$VOICE_APP_DIR/.venv/bin/activate}

# PC2 通常通过机器人网段连接 PC1；优先自动寻找该路由对应的网卡。
_AUTO_CONTROL_IFACE=$(ip route get 192.168.123.161 2>/dev/null | sed -n 's/.* dev \([^ ]*\).*/\1/p' | head -n1 || true)
export CONTROL_IFACE=${CONTROL_IFACE:-${_AUTO_CONTROL_IFACE:-eth0}}

# PC2 为 aarch64；仅在对应架构的 AEC 库实际存在时加入搜索路径。
case "$(uname -m)" in
  aarch64|arm64) _WEBRTC_ARCH=arm64 ;;
  x86_64|amd64) _WEBRTC_ARCH=x64 ;;
  *) _WEBRTC_ARCH=$(uname -m) ;;
esac
export WEBRTC_APM_LIB_DIR=${WEBRTC_APM_LIB_DIR:-$VOICE_APP_DIR/libs/webrtc_apm/linux/$_WEBRTC_ARCH}

# PC2 官方环境通常已带含 cyclonedds/unitree_sdk2py 的 unitree-core venv。
if [[ -x "$HOME/robot_dev/envs/unitree-core/bin/python" ]]; then
  _DEFAULT_CONTROL_PYTHON="$HOME/robot_dev/envs/unitree-core/bin/python"
else
  _DEFAULT_CONTROL_PYTHON=python3
fi
export CONTROL_PYTHON=${CONTROL_PYTHON:-$_DEFAULT_CONTROL_PYTHON}
export NAV_RVIZ=${NAV_RVIZ:-false}

# AEC动态库搜索路径（防止 libwebrtc_apm.so 依赖找不到）
if [[ -d "$WEBRTC_APM_LIB_DIR" ]]; then
  export LD_LIBRARY_PATH="$WEBRTC_APM_LIB_DIR:${LD_LIBRARY_PATH:-}"
fi

# 禁用ROS 1 End-of-Life警告弹窗
export DISABLE_ROS1_EOL_WARNINGS=1

# 运行期目录
export RUNTIME_DIR=${RUNTIME_DIR:-$BASE_DIR/run}
export LOG_DIR=${LOG_DIR:-$RUNTIME_DIR/logs}
export PID_DIR=${PID_DIR:-$RUNTIME_DIR/pids}

mkdir -p "$LOG_DIR" "$PID_DIR"

log() { echo "[$(date +'%F %T')] $*"; }
warn() { echo "[$(date +'%F %T')] [WARN] $*" >&2; }

write_pid() { # name pid
  echo "$2" > "$PID_DIR/$1.pid"
}

read_pid() { # name -> pid or empty
  cat "$PID_DIR/$1.pid" 2>/dev/null || true
}

is_running() { # pid -> 0/1
  local pid="${1:-}"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

stop_pid() { # name [signal] [timeout]
  local name="$1" sig="${2:-TERM}" timeout="${3:-10}"
  local pid
  pid=$(read_pid "$name")
  if [[ -z "$pid" ]]; then
    return 1
  fi
  if ! is_running "$pid"; then
    rm -f "$PID_DIR/$name.pid"
    return 0
  fi
  kill -"$sig" "$pid" 2>/dev/null || true
  for _ in $(seq "$timeout"); do
    if ! is_running "$pid"; then
      rm -f "$PID_DIR/$name.pid"
      return 0
    fi
    sleep 1
  done
  kill -KILL "$pid" 2>/dev/null || true
  rm -f "$PID_DIR/$name.pid"
}
