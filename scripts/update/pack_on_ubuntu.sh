#!/usr/bin/env bash
# 一键打包脚本 - 仅在最新 Ubuntu 打包机上执行
# 用法: ./pack_on_ubuntu.sh --full [--version 1.0.0]
#       ./pack_on_ubuntu.sh --select --daolan [--version 1.0.0]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="${BASE_DIR:-/home/ztx/robot/DaoLan}"
ROBOT_DIR="${ROBOT_DIR:-/home/ztx/robot}"
BUILD2_BIN="${BUILD2_BIN:-/home/ztx/robot_program/unitree_sdk2/build2/bin}"
BUILD2_DIR="$(dirname "$BUILD2_BIN")"
FRONTEND_DIST="${FRONTEND_DIST:-/home/ztx/g1_install/dist}"
FRONTEND_DIR="$(dirname "$FRONTEND_DIST")"
FACE_DIR="${FACE_DIR:-/home/ztx/robot_program/face}"
FACE_PARENT="$(dirname "$FACE_DIR")"
LINKERHAND_DIR="${LINKERHAND_DIR:-/home/ztx/robot_program/linkerhand-python-sdk}"
LINKERHAND_PARENT="$(dirname "$LINKERHAND_DIR")"
# robot_program 下第一层目录；设备上可能尚不存在，靠更新包创建
BAILIAN_DIR="${BAILIAN_DIR:-/home/ztx/robot_program/alibabacloud-bailian-speech-demo}"
OMNI_DIR="${OMNI_DIR:-$BAILIAN_DIR/samples/conversation/omni}"
ROBOT_PROGRAM_DIR="${ROBOT_PROGRAM_DIR:-/home/ztx/robot_program}"
OUTPUT_BASE="${OUTPUT_BASE:-$HOME/update_packages}"

VERSION=""
MODE=""
COMPONENTS=()

log() { echo "[$(date +'%F %T')] $*"; }
warn() { echo "[$(date +'%F %T')] [WARN] $*" >&2; }

usage() {
  cat <<EOF
用法: $0 --full [--version VER]
      $0 --select --daolan [--build2] [--frontend] [--face] [--linkerhand] [--omni] [--version VER]

  --full           全量打包所有组件
  --select         按需打包，需指定组件
  --daolan         打包 DaoLan（包含 unitree_sdk2_python）
  --build2         打包 build2/bin 可执行文件
  --frontend       打包前端 dist
  --face           打包人脸识别
  --linkerhand     打包灵巧手
  --omni           打包 alibabacloud-bailian-speech-demo（解压到 robot_program/；排除 robot.json/venv；动作表用机载 actionOptions.json）
  --version VER    版本号，如 1.0.0（默认从 update_config.json 读取）
  --output DIR     输出目录（默认 ~/update_packages）
EOF
  exit 1
}

# 解析参数
while [[ $# -gt 0 ]]; do
  case "$1" in
    --full) MODE="full"; shift ;;
    --select) MODE="select"; shift ;;
    --daolan) COMPONENTS+=(daolan); shift ;;
    --build2) COMPONENTS+=(build2); shift ;;
    --frontend) COMPONENTS+=(frontend); shift ;;
    --face) COMPONENTS+=(face); shift ;;
    --linkerhand) COMPONENTS+=(linkerhand); shift ;;
    --omni) COMPONENTS+=(omni); shift ;;
    --version) VERSION="$2"; shift 2 ;;
    --output) OUTPUT_BASE="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) warn "未知参数: $1"; usage ;;
  esac
done

[[ -z "$MODE" ]] && { warn "请指定 --full 或 --select"; usage; }
[[ "$MODE" == "select" && ${#COMPONENTS[@]} -eq 0 ]] && { warn "--select 需指定至少一个组件"; usage; }

# 版本号
if [[ -z "$VERSION" ]]; then
  if [[ -f "$SCRIPT_DIR/update_config.json" ]]; then
    VERSION=$(grep -o '"version"[[:space:]]*:[[:space:]]*"[^"]*"' "$SCRIPT_DIR/update_config.json" | head -1 | sed 's/.*"\([^"]*\)".*/\1/')
  fi
  [[ -z "$VERSION" ]] && VERSION="1.0.0"
fi

WORK_DIR=$(mktemp -d)
trap "rm -rf '$WORK_DIR'" EXIT
mkdir -p "$OUTPUT_BASE"
OUT_DIR="$WORK_DIR/out"
mkdir -p "$OUT_DIR"

log "版本: $VERSION, 模式: $MODE"
[[ "$MODE" == "full" ]] && COMPONENTS=(daolan build2 frontend face linkerhand omni)

# 打包 DaoLan
pack_daolan() {
  log "打包 DaoLan..."
  local dest="$OUT_DIR/daolan"
  mkdir -p "$dest"
  rsync -a --delete \
    --exclude='map/' \
    --exclude='run/' \
    --exclude='screen_bg/' \
    --exclude='Livox-SDK2' \
    --exclude='PythonProject/point_nav/' \
    --exclude='PythonProject/*.zip' \
    --exclude='PythonProject/py-xiaozhi-main/config/' \
    --exclude='PythonProject/py-xiaozhi-main/.venv/' \
    --exclude='PythonProject/py-xiaozhi-main/venv/' \
    --exclude='PythonProject/py-xiaozhi-main/captured_photos/' \
    --exclude='PythonProject/py-xiaozhi-main/cache/' \
    --exclude='PythonProject/py-xiaozhi-main/logs/' \
    --exclude='PythonProject/py-xiaozhi-main/__pycache__/' \
    --exclude='PythonProject/py-xiaozhi-main/**/__pycache__/' \
    --exclude='scripts/license.txt' \
    --exclude='scripts/gen_license.py' \
    --exclude='**/*.cpp' \
    --exclude='**/*.hpp' \
    --exclude='**/*.h' \
    --exclude='**/venv/' \
    --exclude='**/.venv/' \
    --exclude='**/*.zip' \
    --exclude='**/*.pyc' \
    "$BASE_DIR/" "$dest/DaoLan/"
  (cd "$dest" && zip -rq "$OUT_DIR/update_daolan.zip" DaoLan)
  log "DaoLan 打包完成"
}

# 打包 build2/bin 仅可执行文件
pack_build2() {
  log "打包 build2/bin 可执行文件..."
  local dest="$OUT_DIR/build2"
  mkdir -p "$dest/bin"
  while IFS= read -r -d '' f; do
    cp "$f" "$dest/bin/"
  done < <(find "$BUILD2_BIN" -maxdepth 1 -type f -executable ! -name "*.json" ! -name "*.csv" ! -name "*.wav" ! -name "*.py" -print0 2>/dev/null || true)
  (cd "$dest" && zip -rq "$OUT_DIR/update_build2.zip" bin)
  log "build2 打包完成"
}

# 打包 frontend（包含 config.js / config.js.bak）
pack_frontend() {
  log "打包 frontend..."
  local dest="$OUT_DIR/frontend"
  mkdir -p "$dest"
  rsync -a --delete "$FRONTEND_DIST/" "$dest/dist/"
  (cd "$dest" && zip -rq "$OUT_DIR/update_frontend.zip" dist)
  log "frontend 打包完成"
}

# 打包 face
pack_face() {
  log "打包 face..."
  local dest="$OUT_DIR/face"
  mkdir -p "$dest"
  rsync -a --delete \
    --exclude='face_data/' \
    --exclude='config/' \
    --exclude='Dlib_face_recognition_from_camera/venv/' \
    --exclude='**/venv/' \
    --exclude='**/.venv/' \
    --exclude='**/*.zip' \
    --exclude='**/__pycache__/' \
    --exclude='**/*.pyc' \
    "$FACE_DIR/" "$dest/face/"
  (cd "$dest" && zip -rq "$OUT_DIR/update_face.zip" face)
  log "face 打包完成"
}

# 打包 linkerhand
pack_linkerhand() {
  log "打包 linkerhand-python-sdk..."
  local dest="$OUT_DIR/linkerhand"
  mkdir -p "$dest"
  rsync -a --delete \
    --exclude='venv/' \
    --exclude='**/*.zip' \
    --exclude='**/__pycache__/' \
    --exclude='**/*.pyc' \
    "$LINKERHAND_DIR/" "$dest/linkerhand-python-sdk/"
  (cd "$dest" && zip -rq "$OUT_DIR/update_linkerhand.zip" linkerhand-python-sdk)
  log "linkerhand 打包完成"
}


# 打包 alibabacloud-bailian-speech-demo（robot_program 下第一层目录整树）
# 排除：venv、run、robot.json（含 api_key）；动作表不打包（运行时读机载 actionOptions.json）
pack_omni() {
  log "打包 alibabacloud-bailian-speech-demo..."
  if [[ ! -d "$BAILIAN_DIR" ]]; then
    warn "未找到目录: $BAILIAN_DIR"
    return 1
  fi
  local dest="$OUT_DIR/omni"
  local top="alibabacloud-bailian-speech-demo"
  local omni_cfg_dst="$dest/$top/samples/conversation/omni/python/config"
  mkdir -p "$dest"
  # 从 robot_program 下第一层目录整树打包，解压到 /home/ztx/robot_program/ 即可创建该目录
  rsync -a --delete \
    --exclude='samples/conversation/omni/python/config/' \
    --exclude='**/venv/' \
    --exclude='**/.venv/' \
    --exclude='**/__pycache__/' \
    --exclude='**/*.pyc' \
    --exclude='samples/conversation/omni/python/run/' \
    --exclude='**/*.pcm' \
    --exclude='**/*.zip' \
    --exclude='.git/' \
    "$BAILIAN_DIR/" "$dest/$top/"

  # config 目录只保留占位；robot.json / actions.json 均不打包
  # （robot.json 含 api_key；动作表用机载 unitree_sdk2/.../actionOptions.json）
  mkdir -p "$omni_cfg_dst"
  rm -f "$omni_cfg_dst/robot.json" "$omni_cfg_dst/actions.json"
  if grep -R -l --include='*.json' 'api_key' "$omni_cfg_dst" >/dev/null 2>&1; then
    warn "警告: omni config 打包结果疑似含 api_key，已清理"
    find "$omni_cfg_dst" -type f -name '*.json' -delete
  fi

  if [[ ! -f "$dest/$top/samples/conversation/omni/python/run_with_camera.py" ]]; then
    warn "打包结果缺少 run_with_camera.py，请检查源目录"
    return 1
  fi
  if [[ ! -f "$dest/$top/samples/conversation/omni/python/start_omni.sh" ]]; then
    warn "打包结果缺少 start_omni.sh"
    return 1
  fi
  (cd "$dest" && zip -rq "$OUT_DIR/update_omni.zip" "$top")
  log "alibabacloud-bailian-speech-demo 打包完成（不含 robot.json/actions.json；动作表用机载 actionOptions.json；解压目标: $ROBOT_PROGRAM_DIR/$top）"
}

# 执行打包
for c in "${COMPONENTS[@]}"; do
  case "$c" in
    daolan) pack_daolan ;;
    build2) pack_build2 ;;
    frontend) pack_frontend ;;
    face) pack_face ;;
    linkerhand) pack_linkerhand ;;
    omni) pack_omni ;;
  esac
done

# 生成 VERSION 和 MANIFEST
echo "$VERSION" > "$OUT_DIR/VERSION"
{
  echo "VERSION=$VERSION"
  echo "Components: ${COMPONENTS[*]}"
  for z in "$OUT_DIR"/*.zip; do
    [[ -f "$z" ]] && echo "$(basename "$z") $(stat -c%s "$z" 2>/dev/null || stat -f%z "$z" 2>/dev/null) bytes"
  done
} > "$OUT_DIR/MANIFEST.txt"

# 打成总包
FINAL_ZIP="$OUTPUT_BASE/update_v${VERSION}.zip"
log "生成总包: $FINAL_ZIP"
rm -f "$FINAL_ZIP"
(cd "$OUT_DIR" && zip -rq "$FINAL_ZIP" VERSION MANIFEST.txt update_*.zip)

log "完成: $FINAL_ZIP"
ls -la "$FINAL_ZIP"

