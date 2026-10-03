#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
source "$SCRIPT_DIR/env.sh"

WS_DIR="$NAV_WS"
MAP_OUT_PREFIX="$BASE_DIR/map/map"

cd "$WS_DIR"
set +u
source "$WS_DIR/devel/setup.bash"
set -u

exec rosrun map_server map_saver map:=/projected_map -f "$MAP_OUT_PREFIX"

