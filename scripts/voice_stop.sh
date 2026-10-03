#!/usr/bin/env bash
# 停止语音服务

set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
source "$SCRIPT_DIR/env.sh"

log "开始停止语音服务"

# 1) 通过PID文件停止语音服务（先停语音，避免停止过程中又触发对话/动作）
if stop_pid voice TERM 3; then
  log "已停止语音服务 (通过PID文件)"
else
  pid=$(read_pid voice)
  if [[ -n "$pid" ]]; then
    warn "语音服务 (pid=$pid) 停止失败，尝试 kill -KILL"
    kill -KILL "$pid" 2>/dev/null || true
    rm -f "$PID_DIR/voice.pid"
  else
    log "语音服务未在 PID 文件中找到，尝试进程检测"
  fi
fi

# 2) 强力清理语音残留进程（即使 PID 丢失 / 父 shell 已退出）
# 注意：在 set -euo pipefail 下，grep 无匹配会返回 1，必须加 || true，否则会提前退出、跳过停动作
list_voice_pids() {
  ps -eo pid,cmd 2>/dev/null \
    | grep -E "python" \
    | grep -F "$VOICE_APP_DIR" \
    | grep -F "main.py" \
    | grep -v grep \
    | awk '{print $1}' || true
}

voice_residuals_exist() {
  [[ -n "$(list_voice_pids)" ]]
}

kill_voice_residuals() {
  local phase="$1" signal="$2"
  local pids
  pids=$(list_voice_pids)
  [[ -z "$pids" ]] && return 0
  warn "阶段: $phase, 信号: $signal, 发现残留语音进程: $pids"
  for p in $pids; do
    kill -$signal "$p" 2>/dev/null || true
  done
}

# 第一轮 TERM
log "第一轮：优雅终止语音进程"
kill_voice_residuals "优雅终止" TERM
sleep 1

# 第二轮 TERM
if voice_residuals_exist; then
  log "第二轮：重复 TERM 信号"
  kill_voice_residuals "重复 TERM" TERM
  sleep 2
fi

# 第三轮 KILL
if voice_residuals_exist; then
  log "第三轮：强制 KILL 信号"
  kill_voice_residuals "强制 KILL" KILL
  sleep 1
fi

# 最终检查
if voice_residuals_exist; then
  warn "仍有无法清理的语音进程，请手动检查:"
  list_voice_pids | while read -r p; do
    ps -p "$p" -o pid=,cmd= 2>/dev/null || true
  done
  # 仍继续停动作，不因残留检查失败而跳过
else
  log "语音服务已完全停止"
fi

# 3) 最后停止机器人动作（语音已停，避免停止动作时又被对话重新触发）
stop_robot_actions() {
  local stop_cmd="$ACTION_EXECUTION_PATH/stop_all_exec05"

  if [[ -x "$stop_cmd" ]]; then
    log "停止机器人动作: $stop_cmd $CONTROL_IFACE"
    "$stop_cmd" "$CONTROL_IFACE" >/dev/null 2>&1 || warn "stop_all_exec05 执行失败（忽略）"
  else
    warn "未找到 stop_all_exec05: $stop_cmd"
  fi
}

stop_robot_actions

log "语音停止流程完成"

