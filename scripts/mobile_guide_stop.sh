#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
source "$SCRIPT_DIR/env.sh"

if stop_pid mobile_guide TERM 30; then
  log "手机导览网页已停止"
else
  log "手机导览网页未运行"
fi
