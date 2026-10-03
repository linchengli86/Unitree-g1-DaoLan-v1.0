#!/usr/bin/env bash
# 停止通过 start_all.sh 启动的后台进程

set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
source "$SCRIPT_DIR/env.sh"

log "开始停止服务"

# 停止顺序：nav_script -> voice -> control -> auto_reloc -> nav_ros_bridge -> navigation -> roscore
for name in nav_script voice control auto_reloc nav_ros_bridge navigation roscore; do
  pid=$(read_pid "$name")
  if [[ -z "$pid" ]]; then
    log "$name 未在 PID 文件中找到，跳过"
    continue
  fi
  
  if ! is_running "$pid"; then
    log "$name (pid=$pid) 进程已不存在，清理 PID 文件"
    rm -f "$PID_DIR/$name.pid"
    continue
  fi
  
  # 先尝试停止子进程
  pkill -P "$pid" TERM 2>/dev/null || true
  sleep 0.5
  
  if stop_pid "$name" TERM 10; then
    log "已停止 $name"
    # 确保子进程也被停止
    pkill -P "$pid" KILL 2>/dev/null || true
  else
    warn "$name (pid=$pid) 停止失败，尝试 kill -KILL"
    # 先停止子进程
    pkill -P "$pid" KILL 2>/dev/null || true
    kill -KILL "$pid" 2>/dev/null || true
    rm -f "$PID_DIR/$name.pid"
  fi
done

# ---------- 强力清理 voice 残留进程（即使 PID 丢失 / 父 shell 已退出） ----------
kill_voice_residuals() {
  local phase="$1" signal="$2"
  local pids
  pids=$(ps -eo pid,cmd 2>/dev/null | grep -E "python" 2>/dev/null | grep -F "$VOICE_APP_DIR" 2>/dev/null | grep -F "main.py" 2>/dev/null | grep -v grep 2>/dev/null | awk '{print $1}' 2>/dev/null || true)
  [[ -z "$pids" ]] && return 0
  warn "阶段: $phase, 信号: $signal, 发现残留 voice 进程: $pids"
  for p in $pids; do
    kill -$signal "$p" 2>/dev/null || true
  done
}

# 第一轮 TERM
kill_voice_residuals "优雅终止" TERM
sleep 1
# 第二轮 TERM
if ps -eo pid,cmd 2>/dev/null | grep -E "python" 2>/dev/null | grep -F "$VOICE_APP_DIR" 2>/dev/null | grep -F "main.py" 2>/dev/null | grep -v grep >/dev/null 2>&1; then
  kill_voice_residuals "重复 TERM" TERM
  sleep 2
fi
# 第三轮 KILL
if ps -eo pid,cmd 2>/dev/null | grep -E "python" 2>/dev/null | grep -F "$VOICE_APP_DIR" 2>/dev/null | grep -F "main.py" 2>/dev/null | grep -v grep >/dev/null 2>&1; then
  kill_voice_residuals "强制 KILL" KILL
  sleep 1
fi

if ps -eo pid,cmd 2>/dev/null | grep -E "python" 2>/dev/null | grep -F "$VOICE_APP_DIR" 2>/dev/null | grep -F "main.py" 2>/dev/null | grep -v grep >/dev/null 2>&1; then
  warn "仍有无法清理的 voice 相关进程，请手动检查:"
  ps -eo pid,cmd 2>/dev/null | grep -E "python" 2>/dev/null | grep -F "$VOICE_APP_DIR" 2>/dev/null | grep -F "main.py" 2>/dev/null | grep -v grep || true
else
  log "voice 残留进程已全部清理"
fi

# ---------- 强力清理 auto_reloc 残留进程（即使 PID 丢失 / 父 shell 已退出） ----------
kill_auto_reloc_residuals() {
  local phase="$1" signal="$2"
  local pids
  pids=$(ps -eo pid,cmd 2>/dev/null | grep -F "auto_reloc.sh" 2>/dev/null | grep -v grep 2>/dev/null | awk '{print $1}' 2>/dev/null || true)
  [[ -z "$pids" ]] && return 0
  warn "阶段: $phase, 信号: $signal, 发现残留 auto_reloc 进程: $pids"
  for p in $pids; do
    kill -$signal "$p" 2>/dev/null || true
  done
}

# 第一轮 TERM
kill_auto_reloc_residuals "优雅终止" TERM
sleep 1
# 第二轮 TERM
if ps -eo pid,cmd 2>/dev/null | grep -F "auto_reloc.sh" 2>/dev/null | grep -v grep >/dev/null 2>&1; then
  kill_auto_reloc_residuals "重复 TERM" TERM
  sleep 1
fi
# 第三轮 KILL
if ps -eo pid,cmd 2>/dev/null | grep -F "auto_reloc.sh" 2>/dev/null | grep -v grep >/dev/null 2>&1; then
  kill_auto_reloc_residuals "强制 KILL" KILL
  sleep 1
fi

if ps -eo pid,cmd 2>/dev/null | grep -F "auto_reloc.sh" 2>/dev/null | grep -v grep >/dev/null 2>&1; then
  warn "仍有无法清理的 auto_reloc 相关进程，请手动检查:"
  ps -eo pid,cmd 2>/dev/null | grep -F "auto_reloc.sh" 2>/dev/null | grep -v grep || true
else
  log "auto_reloc 残留进程已全部清理"
fi

# ---------- 清理所有通过 bash -lc 启动的残留进程 ----------
kill_bash_lc_residuals() {
  local phase="$1" signal="$2"
  local pids
  # 查找所有通过 bash -lc 启动的进程（包括 auto_reloc.sh, navigation.launch, g1_control.py 等）
  pids=$(ps -eo pid,cmd 2>/dev/null | grep -E "bash.*-lc.*(auto_reloc|navigation\.launch|g1_control\.py|main\.py)" 2>/dev/null | grep -v grep 2>/dev/null | awk '{print $1}' 2>/dev/null || true)
  [[ -z "$pids" ]] && return 0
  warn "阶段: $phase, 信号: $signal, 发现残留 bash -lc 进程: $pids"
  for p in $pids; do
    kill -$signal "$p" 2>/dev/null || true
  done
}

# 清理 bash -lc 残留进程
log "开始清理 bash -lc 残留进程"
kill_bash_lc_residuals "优雅终止" TERM
sleep 1

# 如果还有残留进程，强制终止
if ps -eo pid,cmd 2>/dev/null | grep -E "bash.*-lc.*(auto_reloc|navigation\.launch|g1_control\.py|main\.py)" 2>/dev/null | grep -v grep >/dev/null 2>&1; then
  kill_bash_lc_residuals "强制终止" KILL
  sleep 1
fi

if ps -eo pid,cmd 2>/dev/null | grep -E "bash.*-lc.*(auto_reloc|navigation\.launch|g1_control\.py|main\.py)" 2>/dev/null | grep -v grep >/dev/null 2>&1; then
  warn "仍有无法清理的 bash -lc 相关进程，请手动检查:"
  ps -eo pid,cmd 2>/dev/null | grep -E "bash.*-lc.*(auto_reloc|navigation\.launch|g1_control\.py|main\.py)" 2>/dev/null | grep -v grep || true
else
  log "bash -lc 残留进程已全部清理"
fi

# ---------- 清理 roslaunch 残留进程 ----------
kill_roslaunch_residuals() {
  local phase="$1" signal="$2"
  local pids
  # 查找所有 roslaunch 进程
  pids=$(ps -eo pid,cmd 2>/dev/null | grep -E "roslaunch.*navigation\.launch" 2>/dev/null | grep -v grep 2>/dev/null | awk '{print $1}' 2>/dev/null || true)
  [[ -z "$pids" ]] && return 0
  warn "阶段: $phase, 信号: $signal, 发现残留 roslaunch 进程: $pids"
  for p in $pids; do
    kill -$signal "$p" 2>/dev/null || true
  done
}

# 清理 roslaunch 残留进程
log "开始清理 roslaunch 残留进程"
kill_roslaunch_residuals "优雅终止" TERM
sleep 1

# 如果还有残留进程，强制终止
if ps -eo pid,cmd 2>/dev/null | grep -E "roslaunch.*navigation\.launch" 2>/dev/null | grep -v grep >/dev/null 2>&1; then
  kill_roslaunch_residuals "强制终止" KILL
  sleep 1
fi

if ps -eo pid,cmd 2>/dev/null | grep -E "roslaunch.*navigation\.launch" 2>/dev/null | grep -v grep >/dev/null 2>&1; then
  warn "仍有无法清理的 roslaunch 相关进程，请手动检查:"
  ps -eo pid,cmd 2>/dev/null | grep -E "roslaunch.*navigation\.launch" 2>/dev/null | grep -v grep || true
else
  log "roslaunch 残留进程已全部清理"
fi

# ---------- 清理 run_nav_script.sh 启动的导航脚本进程 ----------
kill_nav_script_processes() {
  local phase="$1" signal="$2"
  local pids
  # 查找在 point_nav/generated 目录下运行的Python脚本进程
  pids=$(ps -eo pid,cmd 2>/dev/null | grep -E "python.*multi_nav.*\.py" 2>/dev/null | grep -v grep 2>/dev/null | awk '{print $1}' 2>/dev/null || true)
  [[ -z "$pids" ]] && return 0
  warn "阶段: $phase, 信号: $signal, 发现导航脚本进程: $pids"
  for p in $pids; do
    kill -$signal "$p" 2>/dev/null || true
  done
}

# 清理导航脚本进程
log "开始清理导航脚本进程"
kill_nav_script_processes "优雅终止" TERM
sleep 1

# 如果还有残留进程，强制终止
if ps -eo pid,cmd 2>/dev/null | grep -E "python.*multi_nav.*\.py" 2>/dev/null | grep -v grep >/dev/null 2>&1; then
  kill_nav_script_processes "强制终止" KILL
  sleep 1
fi

if ps -eo pid,cmd 2>/dev/null | grep -E "python.*multi_nav.*\.py" 2>/dev/null | grep -v grep >/dev/null 2>&1; then
  warn "仍有无法清理的导航脚本进程，请手动检查:"
  ps -eo pid,cmd 2>/dev/null | grep -E "python.*multi_nav.*\.py" 2>/dev/null | grep -v grep || true
else
  log "导航脚本进程已全部清理"
fi

log "停止流程完成"

# ---------- 强制关闭所有终端窗口 ----------
log "开始关闭所有终端窗口"

# 常见的终端程序列表
TERMINAL_PROGRAMS=(
  "gnome-terminal"
  "xterm"
  "konsole"
  "terminator"
  "tilix"
  "alacritty"
  "xfce4-terminal"
  "mate-terminal"
  "lxterminal"
  "qterminal"
  "urxvt"
  "rxvt"
)

# 查找所有终端进程
found_terminals=0
for term_prog in "${TERMINAL_PROGRAMS[@]}"; do
  if pgrep -x "$term_prog" >/dev/null 2>&1; then
    found_terminals=1
    log "发现 $term_prog 进程，准备关闭"
    pkill -x "$term_prog" 2>/dev/null || true
  fi
done

# 如果没找到常见的终端程序，尝试通过进程名查找所有可能的终端
if [[ "$found_terminals" -eq 0 ]]; then
  # 查找所有包含 terminal 的进程
  terminal_pids=$(ps -eo pid,comm 2>/dev/null | grep -iE "(terminal|term)" 2>/dev/null | grep -v grep 2>/dev/null | awk '{print $1}' 2>/dev/null || true)
  if [[ -n "$terminal_pids" ]]; then
    log "发现其他终端进程，准备关闭: $terminal_pids"
    for pid in $terminal_pids; do
      kill "$pid" 2>/dev/null || true
    done
    found_terminals=1
  fi
fi

if [[ "$found_terminals" -eq 0 ]]; then
  log "未发现终端进程，跳过关闭操作"
else
  # 等待一下，确保进程被关闭
  sleep 1
  
  # 如果还有残留的终端进程，强制关闭
  for term_prog in "${TERMINAL_PROGRAMS[@]}"; do
    if pgrep -x "$term_prog" >/dev/null 2>&1; then
      warn "强制关闭残留的 $term_prog 进程"
      pkill -9 -x "$term_prog" 2>/dev/null || true
    fi
  done
  
  log "所有终端窗口已关闭"
fi
