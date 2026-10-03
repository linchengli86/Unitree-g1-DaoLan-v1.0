#!/usr/bin/env bash
# 停止导览点位讲解音频（audio_file / TTS），不暂停导航、不杀进程
# 用法: ./nav_stop_audio.sh
#
# 向导航进程发送 SIGRTMIN，触发 pygame.mixer.music.stop() 并恢复麦克风
# 前端调用示例:
#   runShFile({ filename: '/home/ztx/robot/DaoLan/scripts/nav_stop_audio.sh' })

set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
source "$SCRIPT_DIR/env.sh"

json_fail() { # message
  local msg="${1:-操作失败}"
  printf '{"success":false,"message":"%s"}\n' "$msg"
}

json_ok() { # message
  local msg="${1:-操作成功}"
  printf '{"success":true,"message":"%s"}\n' "$msg"
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

RTMIN=$(kill -l RTMIN 2>/dev/null | awk '{print $1}' || true)
if [[ -z "$RTMIN" ]]; then
  warn "当前系统不支持 SIGRTMIN"
  json_fail "当前系统不支持停止讲解音频信号(SIGRTMIN)"
  exit 1
fi

kill "-$RTMIN" "$NAV_PID"
log "已发送停止讲解音频信号(SIGRTMIN=$RTMIN)到导航进程 PID: $NAV_PID"
json_ok "已停止讲解音频（导航继续）"

