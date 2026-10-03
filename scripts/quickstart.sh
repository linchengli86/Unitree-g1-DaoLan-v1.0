#!/usr/bin/env bash
# One entrypoint; check mode never installs, starts ROS or moves the robot.
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
exec /usr/bin/python3 "$SCRIPT_DIR/setup_pc2.py" "$@"
