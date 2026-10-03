#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
source "$SCRIPT_DIR/env.sh"

# 巡逻脚本：按顺序执行生成导航脚本与运行多点巡航

# 路径配置（根据需要修改）
PY_APP_DIR="$VOICE_APP_DIR"
GEN_DIR="$BASE_DIR/PythonProject/point_nav/generated"
VENV_ACTIVATE="$VOICE_VENV_ACTIVATE"

echo "[1/4] 执行脚本生成：regenerate_navigation_scripts.py"
cd "$PY_APP_DIR"
"$PY_APP_DIR/.venv/bin/python" ./regenerate_navigation_scripts.py

echo "[2/4] 进入生成目录：$GEN_DIR"
cd "$GEN_DIR"

echo "[3/4] 激活虚拟环境并加载 ROS 环境"
source "$VENV_ACTIVATE"
# 临时关闭 nounset，避免 ROS 的 profile 脚本引用未绑定变量时报错
set +u
source "$ROS_SETUP"
set -u
echo "Python 解释器：$(command -v python)"

echo "[4/4] 启动多点巡航：multi_nav_complex_route.py"
python multi_nav_complex_route.py

echo "完成：巡逻流程已结束。"

