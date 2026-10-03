#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
source "$SCRIPT_DIR/env.sh"
OMNI_CONFIG="$RUNTIME_DIR/config/omni.env"
if [[ -f "$OMNI_CONFIG" ]]; then
  if [[ "$(stat -c %a "$OMNI_CONFIG")" != "600" ]]; then
    warn "Omni 配置文件权限必须是 600: $OMNI_CONFIG"
    exit 1
  fi
  set -a
  source "$OMNI_CONFIG"
  set +a
fi

NAME=mobile_guide
PORT=${MOBILE_GUIDE_PORT:-8765}
VOLUME=${MOBILE_GUIDE_VOLUME:-100}
LOG_FILE="$LOG_DIR/mobile_guide.log"

old_pid=$(read_pid "$NAME")
if is_running "$old_pid"; then
  log "手机导览网页已运行 (PID=$old_pid): http://192.168.3.64:$PORT"
  exit 0
fi

nohup "$CONTROL_PYTHON" "$SCRIPT_DIR/mobile_guide_server.py" \
  --interface "$CONTROL_IFACE" \
  --host 0.0.0.0 \
  --port "$PORT" \
  --volume "$VOLUME" \
  >"$LOG_FILE" 2>&1 &
pid=$!
write_pid "$NAME" "$pid"

for _ in $(seq 1 20); do
  if ! is_running "$pid"; then
    warn "手机导览网页启动失败"
    tail -n 30 "$LOG_FILE" >&2 || true
    exit 1
  fi
  if curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    log "手机导览网页已启动 (PID=$pid)"
    log "手机访问: http://192.168.3.64:$PORT"
    exit 0
  fi
  sleep 0.5
done

warn "服务进程仍在运行，但健康检查超时；日志: $LOG_FILE"
exit 1
