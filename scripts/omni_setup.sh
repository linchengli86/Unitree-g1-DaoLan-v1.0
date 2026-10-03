#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
source "$SCRIPT_DIR/env.sh"

VENV="$RUNTIME_DIR/omni-venv"
PYTHON=${OMNI_PYTHON_BIN:-python3}
if [[ ! -x "$VENV/bin/python" ]]; then
  "$PYTHON" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install -r "$SCRIPT_DIR/omni-requirements.txt"
"$VENV/bin/python" -c 'import websocket; print("Omni WebSocket dependency ready:", websocket.__version__)'
mkdir -p "$RUNTIME_DIR/config"
if [[ ! -e "$RUNTIME_DIR/config/omni.env" ]]; then
  cp "$SCRIPT_DIR/omni.env.example" "$RUNTIME_DIR/config/omni.env"
  chmod 600 "$RUNTIME_DIR/config/omni.env"
  log "已创建 $RUNTIME_DIR/config/omni.env；请在 PC2 本地填写 API Key"
fi
