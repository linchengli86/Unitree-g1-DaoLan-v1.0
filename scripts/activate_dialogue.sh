#!/usr/bin/env bash
# 主动触发对话脚本
# 用于从外部触发小智对话，支持唤醒和发送文本

set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
source "$SCRIPT_DIR/env.sh"
[[ -f "$SCRIPT_DIR/reloc_config.sh" ]] && source "$SCRIPT_DIR/reloc_config.sh"

log() { echo "[$(date +'%F %T')] $*"; }
warn() { echo "[$(date +'%F %T')] [WARN] $*" >&2; }

# 可接收参数，也可不接收；无参数时默认发送「唤醒」
TEXT="${*:-唤醒}"

log "准备触发对话，文本: $TEXT"

# 检查语音服务是否在运行
VOICE_PID=$(read_pid voice 2>/dev/null || true)
VOICE_RUNNING=false

if [[ -n "$VOICE_PID" ]] && kill -0 "$VOICE_PID" 2>/dev/null; then
    VOICE_RUNNING=true
    log "检测到语音服务正在运行 (PID: $VOICE_PID)"
else
    # 额外检测：通过进程名检测
    if pgrep -f "python.*main\.py" >/dev/null 2>&1; then
        VOICE_PROCESS=$(pgrep -f "python.*main\.py" | head -1)
        if [[ -n "$VOICE_PROCESS" ]]; then
            PROCESS_CWD=$(readlink -f "/proc/$VOICE_PROCESS/cwd" 2>/dev/null || true)
            if [[ "$PROCESS_CWD" == "$VOICE_APP_DIR" ]]; then
                VOICE_RUNNING=true
                log "检测到语音服务进程正在运行 (PID: $VOICE_PROCESS)"
            fi
        fi
    fi
fi

if [[ "$VOICE_RUNNING" == "false" ]]; then
    warn "语音服务未运行，请先启动语音服务："
    warn "  scripts/voice_start.sh"
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
    log "已激活虚拟环境"
else
    warn "未找到虚拟环境：$VOICE_VENV_ACTIVATE，使用系统 Python"
fi

# 运行Python脚本
log "执行触发对话脚本..."
python3 trigger_dialogue.py "$TEXT"

exit_code=$?
if [[ $exit_code -eq 0 ]]; then
    log "对话触发成功"
else
    warn "对话触发失败，退出码: $exit_code"
fi

exit $exit_code
