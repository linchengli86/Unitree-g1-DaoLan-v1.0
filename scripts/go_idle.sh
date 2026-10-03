#!/usr/bin/env bash
# 让小智进入待命状态脚本

set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
source "$SCRIPT_DIR/env.sh"
[[ -f "$SCRIPT_DIR/reloc_config.sh" ]] && source "$SCRIPT_DIR/reloc_config.sh"

log "尝试让小智进入待命状态..."

# 直接通过 Socket 判断服务是否可用
SOCKET_PATH="/tmp/dialogue_trigger.sock"
if [[ ! -S "$SOCKET_PATH" ]]; then
    warn "Socket 文件不存在: $SOCKET_PATH，可能语音服务未运行或 Socket 服务器未启动"
    exit 1
fi

# 切换到语音应用目录
cd "$VOICE_APP_DIR" || {
    warn "无法切换到语音应用目录: $VOICE_APP_DIR"
    exit 1
}

# 激活虚拟环境（如果存在）
if [[ -f "$VOICE_VENV_ACTIVATE" ]]; then
    source "$VOICE_VENV_ACTIVATE"
else
    warn "未找到虚拟环境：$VOICE_VENV_ACTIVATE，使用系统 Python"
fi

# 运行 Python 待命脚本
python3 go_idle.py

exit_code=$?
exit $exit_code
