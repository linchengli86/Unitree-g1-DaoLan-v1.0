#!/usr/bin/env bash
# 停止 TTS 播放 - 直接 kill -9 终止 tts.py 及关联播放进程
# 用法: ./tts_stop.sh
#
# 前端调用示例:
#   runShFile({ filename: '/home/ztx/robot/DaoLan/scripts/tts_stop.sh' })

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/env.sh"

TTS_DIR="${BASE_DIR}/PythonProject/tts"
TTS_PY="${TTS_DIR}/tts.py"

found=0

kill_pids() {
  local pattern="$1"
  local pids
  pids=$(ps -eo pid,cmd 2>/dev/null | grep -v grep | grep -E "$pattern" | awk '{print $1}' || true)
  [[ -z "$pids" ]] && return 0
  found=1
  for pid in $pids; do
    kill -9 "$pid" 2>/dev/null || true
  done
  log "已终止: $pids ($pattern)"
}

log "停止 TTS 进程"

# tts.py 主进程（完整路径匹配，避免误杀）
kill_pids "python.*${TTS_PY}"

# tts_speak.sh 包装进程（exec 前或 Popen 启动的 bash）
kill_pids "tts_speak\\.sh"

# ffplay 流式播放子进程（边收边播）
if pkill -9 -f "ffplay.*pipe:0" 2>/dev/null; then
  found=1
  log "已终止: ffplay 流式播放进程"
fi

if [[ "$found" -eq 0 ]]; then
  log "未发现运行中的 TTS 进程"
else
  log "TTS 已停止"
fi

exit 0

