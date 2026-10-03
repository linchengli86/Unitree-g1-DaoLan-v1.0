#!/usr/bin/env bash
# 停止当前正在执行的导览项（multi_nav 进程），不影响语音 / ROS 导航栈 / 运控
# 用法: ./stop_nav.sh
#
# 行为:
#   1. 向导航进程发送 SIGRTMIN+1，取消 move_base 目标、停止讲解/动作后退出
#   2. 旧版脚本无该信号时，先 SIGUSR1 暂停（取消目标），再 SIGTERM 结束进程
#   3. 不停止 voice、roscore、navigation.launch、运控
#
# 前端调用示例:
#   runShFile({ filename: '/home/ztx/robot/DaoLan/scripts/stop_nav.sh' })

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

is_nav_python_process() {
  local pid="$1"
  local cmdline
  if [[ ! -r "/proc/$pid/cmdline" ]]; then
    return 1
  fi
  cmdline=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
  [[ "$cmdline" =~ python ]] && [[ "$cmdline" =~ multi_nav_.*\.py ]]
}

stop_nav_process_tree() {
  local pid="$1"
  local timeout="${2:-8}"

  pkill -P "$pid" TERM 2>/dev/null || true
  kill -TERM "$pid" 2>/dev/null || true

  for _ in $(seq "$timeout"); do
    if ! kill -0 "$pid" 2>/dev/null; then
      pkill -P "$pid" KILL 2>/dev/null || true
      return 0
    fi
    sleep 1
  done

  pkill -P "$pid" KILL 2>/dev/null || true
  kill -KILL "$pid" 2>/dev/null || true
  sleep 1
  ! kill -0 "$pid" 2>/dev/null
}

cancel_move_base_goal() {
  if [[ ! -f "$ROS_SETUP" ]]; then
    return 0
  fi
  set +u
  # shellcheck disable=SC1090
  source "$ROS_SETUP" >/dev/null 2>&1 || true
  set -u
  if ! command -v timeout >/dev/null 2>&1 || ! command -v rostopic >/dev/null 2>&1; then
    return 0
  fi
  timeout 2 rostopic pub -1 /move_base/cancel actionlib_msgs/GoalID \
    "{stamp: {secs: 0, nsecs: 0}, id: ''}" >/dev/null 2>&1 || true
}

stop_tour_actions() {
  local stop_bin="${ACTION_EXECUTION_PATH}/stop_all_exec05"
  if [[ -x "$stop_bin" ]]; then
    timeout 8 "$stop_bin" "$CONTROL_IFACE" >/dev/null 2>&1 || true
  fi
  if [[ -x "$SCRIPT_DIR/tts_stop.sh" ]]; then
    bash "$SCRIPT_DIR/tts_stop.sh" >/dev/null 2>&1 || true
  fi
}

NAV_PID=$(read_pid nav_script)
if [[ -z "${NAV_PID}" ]]; then
  warn "未找到导航进程 PID 文件：$PID_DIR/nav_script.pid"
  json_fail "当前没有正在执行的导览项"
  exit 1
fi

if ! is_running "$NAV_PID"; then
  warn "导航进程不存在 (PID: $NAV_PID)，正在清理 PID 文件"
  rm -f "$PID_DIR/nav_script.pid"
  json_fail "当前没有正在执行的导览项"
  exit 1
fi

if ! is_nav_python_process "$NAV_PID"; then
  warn "PID文件指向的进程不是导航脚本，已清理: $NAV_PID"
  rm -f "$PID_DIR/nav_script.pid"
  json_fail "PID文件无效：不是导航脚本进程"
  exit 1
fi

log "准备停止当前导览项 (PID: $NAV_PID)"

# 先尽量取消导航目标，避免进程退出后机器人继续走
cancel_move_base_goal

sent_stop_signal=0
RTMIN=$(kill -l RTMIN 2>/dev/null | awk '{print $1}' || true)
if [[ -n "$RTMIN" ]]; then
  RTMIN_STOP=$((RTMIN + 1))
  if kill "-$RTMIN_STOP" "$NAV_PID" 2>/dev/null; then
    sent_stop_signal=1
    log "已发送停止导览信号(SIGRTMIN+1=$RTMIN_STOP)到 PID: $NAV_PID"
  fi
fi

if [[ "$sent_stop_signal" -eq 0 ]]; then
  # 旧版生成脚本没有停止信号：先暂停以取消目标，再结束进程
  kill -USR1 "$NAV_PID" 2>/dev/null || true
  log "已发送暂停信号(SIGUSR1)以取消当前导航目标"
  sleep 0.5
fi

for _ in $(seq 8); do
  if ! is_running "$NAV_PID"; then
    rm -f "$PID_DIR/nav_script.pid"
    stop_tour_actions
    log "当前导览项已停止"
    json_ok "已停止当前导览项"
    exit 0
  fi
  sleep 0.5
done

warn "导航进程未在信号后退出，改为结束进程树"
if stop_nav_process_tree "$NAV_PID" 6; then
  rm -f "$PID_DIR/nav_script.pid"
  cancel_move_base_goal
  stop_tour_actions
  log "当前导览项已强制停止"
  json_ok "已停止当前导览项"
  exit 0
fi

warn "无法停止导航进程 PID: $NAV_PID"
json_fail "停止当前导览项失败"
exit 1

