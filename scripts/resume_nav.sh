#!/usr/bin/env bash

set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
source "$SCRIPT_DIR/env.sh"

json_fail() { # message
  local msg="${1:-操作失败}"
  printf '{"success":false,"message":"%s"}\n' "$msg"
}

NAV_PID=$(read_pid nav_script)
if [[ -z "${NAV_PID}" ]]; then
  warn "未找到导航进程 PID 文件：$PID_DIR/nav_script.pid"
  json_fail "未找到导航进程 PID"
  exit 1
fi

if ! is_running "$NAV_PID"; then
  warn "导航进程不存在 (PID: $NAV_PID)，正在清理 PID 文件"
  rm -f "$PID_DIR/nav_script.pid"
  json_fail "导航进程不存在"
  exit 1
fi

if [[ ! -r "/proc/$NAV_PID/cmdline" ]]; then
  warn "无法读取进程命令行，清理无效PID文件: $NAV_PID"
  rm -f "$PID_DIR/nav_script.pid"
  json_fail "无法读取导航进程信息"
  exit 1
fi

CMDLINE=$(tr '\0' ' ' < "/proc/$NAV_PID/cmdline" 2>/dev/null || true)
if [[ ! "$CMDLINE" =~ python ]] || [[ ! "$CMDLINE" =~ multi_nav_.*\.py ]]; then
  warn "PID文件指向的进程不是导航脚本，已清理: $NAV_PID"
  rm -f "$PID_DIR/nav_script.pid"
  json_fail "PID文件无效：不是导航脚本进程"
  exit 1
fi

kill -USR2 "$NAV_PID"
log "已发送恢复信号(SIGUSR2)到导航进程 PID: $NAV_PID"

