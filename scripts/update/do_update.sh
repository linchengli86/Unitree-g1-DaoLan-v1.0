#!/usr/bin/env bash
# 更新脚本 - 在客户机执行
# 用法: ./do_update.sh --all
#       ./do_update.sh --local /path/to/update_v1.0.0.zip --all
#       ./do_update.sh --frontend
#       ./do_update.sh --local /path/to/zip --daolan
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
BAILIAN_DIR="${BAILIAN_DIR:-/home/ztx/robot_program/alibabacloud-bailian-speech-demo}"
OMNI_DIR="${OMNI_DIR:-$BAILIAN_DIR/samples/conversation/omni}"
OMNI_PYTHON_DIR="${OMNI_PYTHON_DIR:-$OMNI_DIR/python}"
ROBOT_PROGRAM_DIR="${ROBOT_PROGRAM_DIR:-/home/ztx/robot_program}"
RUNTIME_DIR="${RUNTIME_DIR:-$BASE_DIR/run}"

# 从 update_config.json 读取版本 API 配置，或使用环境变量
VERSION_API_BASE="${VERSION_API_BASE:-}"
VERSION_ID="${VERSION_ID:-}"
if [[ -f "$SCRIPT_DIR/update_config.json" ]]; then
  [[ -z "$VERSION_API_BASE" ]] && VERSION_API_BASE=$(grep -o '"version_api_base"[[:space:]]*:[[:space:]]*"[^"]*"' "$SCRIPT_DIR/update_config.json" 2>/dev/null | sed 's/.*"\([^"]*\)".*/\1/')
  [[ -z "$VERSION_ID" ]] && VERSION_ID=$(grep -o '"version_id"[[:space:]]*:[[:space:]]*"[^"]*"' "$SCRIPT_DIR/update_config.json" 2>/dev/null | sed 's/.*"\([^"]*\)".*/\1/')
fi

LOCAL_ZIP=""
COMPONENTS=()
FORCE=0

log() { echo "[$(date +'%F %T')] $*"; }
warn() { echo "[$(date +'%F %T')] [WARN] $*" >&2; }

# 大文件更新包：优先 curl；仅 wget 或 wget 崩溃/失败后再用 curl 重试（去掉 wget --show-progress 以降低个别环境段错误概率）
download_update_zip() {
  local url="$1" dest="$2"
  local ok=0 has_curl=0 has_wget=0
  command -v curl &>/dev/null && has_curl=1
  command -v wget &>/dev/null && has_wget=1
  if [[ $has_curl -eq 0 && $has_wget -eq 0 ]]; then
    warn "需要 curl 或 wget"
    return 1
  fi
  _try_curl() { curl -fL --connect-timeout 30 --progress-bar -o "$dest" "$url"; }
  _try_wget() { wget -O "$dest" "$url"; }
  if [[ $has_curl -eq 1 ]]; then
    if _try_curl; then ok=1; fi
  fi
  if [[ $ok -eq 0 && $has_wget -eq 1 ]]; then
    if [[ $has_curl -eq 1 ]]; then
      warn "curl 下载未成功，改用 wget..."
    else
      log "使用 wget 下载（未检测到 curl）"
    fi
    if _try_wget; then ok=1; fi
  fi
  if [[ $ok -eq 0 && $has_curl -eq 1 ]]; then
    warn "下载仍未成功，使用 curl 重试（将清除可能不完整的临时文件）..."
    rm -f "$dest"
    if _try_curl; then ok=1; fi
  fi
  [[ $ok -eq 1 ]]
}

usage() {
  cat <<EOF
用法: $0 [--local /path/to/update_v1.0.0.zip] [--all|--daolan|--build2|--frontend|--face|--linkerhand|--omni] [--force]

  --local PATH   使用本地压缩包，不从服务器下载
  --all          更新全部组件（默认）
  --daolan       仅更新 DaoLan（包含 unitree_sdk2_python）
  --build2       仅更新后端 build2/bin
  --frontend     仅更新前端
  --face         仅更新人脸识别
  --linkerhand   仅更新灵巧手
  --omni         仅更新 alibabacloud-bailian-speech-demo（解压到 robot_program/；保留 robot.json；动作表用机载 actionOptions.json；自动建 venv）
  --force        跳过版本校验
EOF
  exit 1
}

# 解析参数
while [[ $# -gt 0 ]]; do
  case "$1" in
    --local) LOCAL_ZIP="$2"; shift 2 ;;
    --all) COMPONENTS=(daolan build2 frontend face linkerhand omni); shift ;;
    --daolan) COMPONENTS+=(daolan); shift ;;
    --build2) COMPONENTS+=(build2); shift ;;
    --frontend) COMPONENTS+=(frontend); shift ;;
    --face) COMPONENTS+=(face); shift ;;
    --linkerhand) COMPONENTS+=(linkerhand); shift ;;
    --omni) COMPONENTS+=(omni); shift ;;
    --force) FORCE=1; shift ;;
    -h|--help) usage ;;
    *) warn "未知参数: $1"; usage ;;
  esac
done

[[ ${#COMPONENTS[@]} -eq 0 ]] && COMPONENTS=(daolan build2 frontend face linkerhand omni)

WORK_DIR=$(mktemp -d)
trap "rm -rf '$WORK_DIR'" EXIT

# 获取更新包
if [[ -n "$LOCAL_ZIP" ]]; then
  [[ ! -f "$LOCAL_ZIP" ]] && { warn "本地包不存在: $LOCAL_ZIP"; exit 1; }
  log "使用本地包: $LOCAL_ZIP"
  cp "$LOCAL_ZIP" "$WORK_DIR/update.zip"
else
  # 通过版本 API 获取下载地址
  if [[ -z "$VERSION_API_BASE" || -z "$VERSION_ID" ]]; then
    warn "未配置 version_api_base 或 version_id，请检查 update_config.json 或设置环境变量"; exit 1
  fi
  _api_url="${VERSION_API_BASE%/}/$VERSION_ID"
  log "请求版本 API: $_api_url"
  _resp=""
  if command -v curl &>/dev/null; then
    _resp=$(curl -sL --connect-timeout 15 "$_api_url" 2>/dev/null) || { warn "API 请求失败"; exit 1; }
  elif command -v wget &>/dev/null; then
    _resp=$(wget -q -O - --timeout=15 "$_api_url" 2>/dev/null) || { warn "API 请求失败"; exit 1; }
  else
    warn "需要 curl 或 wget"; exit 1
  fi
  _download_url=$(echo "$_resp" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    if d.get("code") != 200:
        sys.exit(1)
    data = d.get("data", d)
    if isinstance(data, dict):
        pkg = data.get("packagePath") or d.get("packagePath")
        status = data.get("status") or d.get("status")
    else:
        pkg = d.get("packagePath")
        status = d.get("status")
    if status != "0":
        sys.exit(2)
    if not pkg:
        sys.exit(3)
    print(pkg)
except Exception:
    sys.exit(4)
' 2>/dev/null) || {
    _err=$?
    case $_err in 1) warn "API 返回 code!=200" ;; 2) warn "status!=0，禁止更新" ;; 3) warn "解析 packagePath 失败" ;; *) warn "解析 API 响应失败" ;; esac
    exit 1
  }
  log "从服务器下载: $_download_url"
  download_update_zip "$_download_url" "$WORK_DIR/update.zip" || { warn "下载失败，请检查网络或使用 --local"; exit 1; }
fi

# 解压总包
log "解压总包..."
unzip -q -o "$WORK_DIR/update.zip" -d "$WORK_DIR/extracted"
EXT_DIR="$WORK_DIR/extracted"

# 版本校验
NEW_VERSION=""
[[ -f "$EXT_DIR/VERSION" ]] && NEW_VERSION=$(cat "$EXT_DIR/VERSION")
CUR_VERSION=""
[[ -f "$RUNTIME_DIR/VERSION" ]] && CUR_VERSION=$(cat "$RUNTIME_DIR/VERSION")

if [[ $FORCE -eq 0 && -n "$NEW_VERSION" && -n "$CUR_VERSION" ]]; then
  # 简单版本比较：若 minor 不同则提示
  _cur_major=$(echo "$CUR_VERSION" | cut -d. -f1)
  _cur_minor=$(echo "$CUR_VERSION" | cut -d. -f2)
  _cur_patch=$(echo "$CUR_VERSION" | cut -d. -f3)
  _new_major=$(echo "$NEW_VERSION" | cut -d. -f1)
  _new_minor=$(echo "$NEW_VERSION" | cut -d. -f2)
  _new_patch=$(echo "$NEW_VERSION" | cut -d. -f3)
  if [[ "$_cur_major" != "$_new_major" || "$_cur_minor" != "$_new_minor" ]]; then
    warn "版本跨度较大: $CUR_VERSION -> $NEW_VERSION，建议全量更新"
    read -r -p "继续? (y/N): " ans
    [[ "${ans,,}" != "y" && "${ans,,}" != "yes" ]] && exit 0
  fi
fi

# 备份需保留的配置
BACKUP_DIR="$WORK_DIR/backup"
mkdir -p "$BACKUP_DIR"

backup_if_exists() {
  local src="$1" dst="$2"
  if [[ -e "$src" ]]; then
    mkdir -p "$(dirname "$dst")"
    cp -a "$src" "$dst" 2>/dev/null || true
  fi
}

# 确保目标目录存在，不存在则创建并提示
ensure_dir() {
  local dir="$1" desc="${2:-$1}"
  if [[ ! -d "$dir" ]]; then
    log "目录不存在，正在创建: $dir"
    if ! mkdir -p "$dir"; then
      warn "创建目录失败: $dir，请检查权限"; return 1
    fi
  fi
  return 0
}

# 安装 g1_control_mpc_stable_fast.py 依赖（高成功率策略）
install_mpc_python_deps() {
  local py_bin="${CONTROL_PYTHON_BIN:-python3}"
  local mirror_url="https://mirrors.aliyun.com/pypi/simple"

  if ! command -v "$py_bin" >/dev/null 2>&1; then
    warn "未找到 Python 解释器: $py_bin，跳过 MPC 依赖安装"
    return 1
  fi

  log "开始安装 MPC Python 依赖（numpy/scipy/osqp），解释器: $py_bin"

  # 确保 pip 可用
  if ! "$py_bin" -m pip --version >/dev/null 2>&1; then
    warn "pip 不可用，尝试启用 ensurepip..."
    "$py_bin" -m ensurepip --upgrade >/dev/null 2>&1 || true
  fi
  if ! "$py_bin" -m pip --version >/dev/null 2>&1; then
    warn "pip 仍不可用，跳过 MPC 依赖安装"
    return 1
  fi

  # 先升级 numpy，再安装 scipy/osqp，降低版本冲突概率
  if ! "$py_bin" -m pip install --user --upgrade "numpy>=1.19.5,<1.27" -i "$mirror_url"; then
    warn "镜像安装 numpy 失败，重试官方源..."
    "$py_bin" -m pip install --user --upgrade "numpy>=1.19.5,<1.27" || return 1
  fi

  if ! "$py_bin" -m pip install --user "scipy<1.11" "osqp<1.0" -i "$mirror_url"; then
    warn "镜像安装 scipy/osqp 失败，重试官方源..."
    "$py_bin" -m pip install --user "scipy<1.11" "osqp<1.0" || return 1
  fi

  if "$py_bin" -c "import numpy, scipy, osqp; print(numpy.__version__, scipy.__version__, osqp.__version__)" >/dev/null 2>&1; then
    log "MPC 依赖安装完成并验证成功"
    return 0
  fi

  warn "MPC 依赖安装后验证失败，请手动执行: $py_bin -c \"import numpy, scipy, osqp\""
  return 1
}

# 选择用于创建 omni venv 的 Python（优先 3.11，避免系统 3.8）
resolve_omni_python_bin() {
  if [[ -n "${OMNI_PYTHON_BIN:-}" ]]; then
    command -v "$OMNI_PYTHON_BIN" >/dev/null 2>&1 && { echo "$OMNI_PYTHON_BIN"; return 0; }
    [[ -x "$OMNI_PYTHON_BIN" ]] && { echo "$OMNI_PYTHON_BIN"; return 0; }
  fi
  local cand
  for cand in \
    /usr/local/python3.11/bin/python3.11 \
    /usr/local/bin/python3.11 \
    python3.11 \
    python3
  do
    if [[ -x "$cand" ]]; then
      echo "$cand"
      return 0
    fi
    if command -v "$cand" >/dev/null 2>&1; then
      command -v "$cand"
      return 0
    fi
  done
  return 1
}

# 安装 AI 语音助手 omni 依赖（缺 venv 则创建；无论有无 venv 都 pip install）
install_omni_python_deps() {
  local omni_py="$OMNI_PYTHON_DIR"
  local venv_dir="$omni_py/venv"
  local req_file="$omni_py/requirements.txt"
  local mirror_url="https://mirrors.aliyun.com/pypi/simple"
  local py_sys
  local py_bin pip_rc

  if [[ ! -f "$omni_py/run_with_camera.py" ]]; then
    warn "未找到 omni 入口: $omni_py/run_with_camera.py，跳过依赖安装"
    return 1
  fi

  if ! py_sys="$(resolve_omni_python_bin)"; then
    warn "未找到可用 Python 解释器，跳过 omni 依赖安装"
    return 1
  fi
  log "omni 使用解释器创建/校验 venv: $py_sys ($("$py_sys" -V 2>&1 || true))"

  if [[ ! -x "$venv_dir/bin/python" ]]; then
    log "创建 omni venv: $venv_dir"
    "$py_sys" -m venv "$venv_dir" || {
      warn "创建 omni venv 失败"
      return 1
    }
  fi

  py_bin="$venv_dir/bin/python"
  if [[ ! -x "$py_bin" ]]; then
    warn "venv python 不可用: $py_bin"
    return 1
  fi
  log "venv python: $("$py_bin" -V 2>&1 || true)"

  # pyaudio 编译需要 portaudio.h 等系统包（不能写进 requirements.txt 的 pip 行）
  if [[ ! -f /usr/include/portaudio.h ]] && [[ ! -f /usr/local/include/portaudio.h ]]; then
    warn "未检测到 portaudio.h，尝试安装系统依赖（portaudio19-dev 等）..."
    if command -v sudo >/dev/null 2>&1; then
      sudo -n env DEBIAN_FRONTEND=noninteractive bash -lc \
        'apt-get update -qq && apt-get install -y -qq portaudio19-dev python3-dev build-essential libasound2-dev' \
        2>/dev/null || warn "系统依赖自动安装失败，请手动: sudo apt install -y portaudio19-dev python3-dev build-essential libasound2-dev"
    else
      warn "无 sudo，请手动: sudo apt install -y portaudio19-dev python3-dev build-essential"
    fi
  fi

  # 用 python -m pip，避免 venv 里 pip 脚本损坏
  if ! "$py_bin" -m pip --version >/dev/null 2>&1; then
    warn "venv 中 pip 不可用，尝试 ensurepip..."
    "$py_bin" -m ensurepip --upgrade >/dev/null 2>&1 || true
  fi
  if ! "$py_bin" -m pip --version >/dev/null 2>&1; then
    warn "venv pip 仍不可用，请手动重建: rm -rf $venv_dir && $py_sys -m venv $venv_dir"
    return 1
  fi

  log "升级 pip/setuptools/wheel..."
  "$py_bin" -m pip install -U pip setuptools wheel -i "$mirror_url" >/dev/null 2>&1 \
    || "$py_bin" -m pip install -U pip setuptools wheel || true

  log "安装 omni Python 依赖（无论 venv 是否已存在都会执行）..."
  if [[ ! -f "$req_file" ]]; then
    warn "未找到 $req_file，跳过 pip install"
    return 1
  fi
  log "requirements: $req_file"
  cat "$req_file" || true

  pip_rc=0
  if ! "$py_bin" -m pip install -U -r "$req_file" -i "$mirror_url"; then
    warn "镜像安装 omni 依赖失败，重试官方源..."
    if ! "$py_bin" -m pip install -U -r "$req_file"; then
      warn "omni 依赖安装失败，请手动: cd $omni_py && source venv/bin/activate && python -m pip install -r requirements.txt"
      pip_rc=1
    fi
  fi

  # 显式再装一遍关键包，避免 -r 部分失败被忽略后缺 cv2
  if [[ $pip_rc -eq 0 ]]; then
    if ! "$py_bin" -m pip install -U dashscope pyaudio opencv-python-headless pygame requests -i "$mirror_url"; then
      "$py_bin" -m pip install -U dashscope pyaudio opencv-python-headless pygame requests || pip_rc=1
    fi
  fi

  find "$omni_py" -maxdepth 1 -name "*.sh" -exec chmod +x {} \; 2>/dev/null || true

  if "$py_bin" -c "import dashscope, cv2, pyaudio; print('ok')" >/dev/null 2>&1; then
    log "omni Python 依赖安装完成并验证成功 (dashscope+cv2+pyaudio)"
    return 0
  fi

  warn "omni 依赖验证失败（缺 dashscope / cv2 / pyaudio）"
  if [[ ! -f /usr/include/portaudio.h ]] && [[ ! -f /usr/local/include/portaudio.h ]]; then
    warn "仍缺 portaudio.h：sudo apt install -y portaudio19-dev python3-dev build-essential"
  fi
  warn "请手动执行:"
  warn "  cd $omni_py && source venv/bin/activate"
  warn "  python -m pip install -U -r requirements.txt"
  warn "  python -c 'import cv2, dashscope, pyaudio; print(\"ok\")'"
  return 1
}

# 按组件更新（缺失的目录会自动创建：/home/ztx/g1_install、/home/ztx/robot_program 等）
log "目标路径: frontend=$FRONTEND_DIR, build2=$BUILD2_DIR, face=$FACE_DIR, linkerhand=$LINKERHAND_PARENT, bailian=$BAILIAN_DIR"
for c in "${COMPONENTS[@]}"; do
  case "$c" in
    daolan)
      if [[ -f "$EXT_DIR/update_daolan.zip" ]]; then
        ensure_dir "$ROBOT_DIR" "ROBOT_DIR" || { warn "DaoLan 更新跳过：无法创建 $ROBOT_DIR"; continue; }
        log "备份 DaoLan 配置..."
        backup_if_exists "$BASE_DIR/PythonProject/py-xiaozhi-main/config/config.json" "$BACKUP_DIR/config.json"
        backup_if_exists "$BASE_DIR/PythonProject/py-xiaozhi-main/config/efuse.json" "$BACKUP_DIR/efuse.json"
        backup_if_exists "$BASE_DIR/PythonProject/py-xiaozhi-main/config/navigation_config.json" "$BACKUP_DIR/navigation_config.json"
        backup_if_exists "$BASE_DIR/PythonProject/py-xiaozhi-main/cache" "$BACKUP_DIR/py_xiaozhi_cache"
        backup_if_exists "$BASE_DIR/PythonProject/py-xiaozhi-main/logs" "$BACKUP_DIR/py_xiaozhi_logs"
        backup_if_exists "$BASE_DIR/scripts/license.txt" "$BACKUP_DIR/license.txt"
        # 备份 screen_bg，避免更新包覆盖现场背景图
        backup_if_exists "$BASE_DIR/screen_bg" "$BACKUP_DIR/screen_bg"
        # 备份 G1Nav2D fastlio2 的 PCD 与 path 目录
        backup_if_exists "$BASE_DIR/G1Nav2D/src/fastlio2/PCD" "$BACKUP_DIR/fastlio2_PCD"
        backup_if_exists "$BASE_DIR/G1Nav2D/src/fastlio2/path" "$BACKUP_DIR/fastlio2_path"
        log "更新 DaoLan..."
        unzip -q -o "$EXT_DIR/update_daolan.zip" -d "$ROBOT_DIR" || { warn "DaoLan 解压失败"; exit 1; }
        log "恢复 DaoLan 配置..."
        mkdir -p "$BASE_DIR/PythonProject/py-xiaozhi-main/config"
        [[ -f "$BACKUP_DIR/config.json" ]] && cp "$BACKUP_DIR/config.json" "$BASE_DIR/PythonProject/py-xiaozhi-main/config/"
        [[ -f "$BACKUP_DIR/efuse.json" ]] && cp "$BACKUP_DIR/efuse.json" "$BASE_DIR/PythonProject/py-xiaozhi-main/config/"
        [[ -f "$BACKUP_DIR/navigation_config.json" ]] && cp "$BACKUP_DIR/navigation_config.json" "$BASE_DIR/PythonProject/py-xiaozhi-main/config/"
        if [[ -d "$BACKUP_DIR/py_xiaozhi_cache" ]]; then
          log "恢复 py-xiaozhi-main/cache 目录..."
          rm -rf "$BASE_DIR/PythonProject/py-xiaozhi-main/cache"
          cp -a "$BACKUP_DIR/py_xiaozhi_cache" "$BASE_DIR/PythonProject/py-xiaozhi-main/cache"
        fi
        if [[ -d "$BACKUP_DIR/py_xiaozhi_logs" ]]; then
          log "恢复 py-xiaozhi-main/logs 目录..."
          rm -rf "$BASE_DIR/PythonProject/py-xiaozhi-main/logs"
          cp -a "$BACKUP_DIR/py_xiaozhi_logs" "$BASE_DIR/PythonProject/py-xiaozhi-main/logs"
        fi
        [[ -f "$BACKUP_DIR/license.txt" ]] && cp "$BACKUP_DIR/license.txt" "$BASE_DIR/scripts/"
        if [[ -d "$BACKUP_DIR/screen_bg" ]]; then
          log "恢复 screen_bg 目录..."
          rm -rf "$BASE_DIR/screen_bg"
          cp -a "$BACKUP_DIR/screen_bg" "$BASE_DIR/screen_bg"
        fi
        # 恢复 fastlio2 的 PCD 与 path 内容（保留原始地图与路径）
        if [[ -d "$BACKUP_DIR/fastlio2_PCD" ]]; then
          log "恢复 G1Nav2D fastlio2/PCD 目录..."
          mkdir -p "$BASE_DIR/G1Nav2D/src/fastlio2"
          rm -rf "$BASE_DIR/G1Nav2D/src/fastlio2/PCD"
          cp -a "$BACKUP_DIR/fastlio2_PCD" "$BASE_DIR/G1Nav2D/src/fastlio2/PCD"
        fi
        if [[ -d "$BACKUP_DIR/fastlio2_path" ]]; then
          log "恢复 G1Nav2D fastlio2/path 目录..."
          mkdir -p "$BASE_DIR/G1Nav2D/src/fastlio2"
          rm -rf "$BASE_DIR/G1Nav2D/src/fastlio2/path"
          cp -a "$BACKUP_DIR/fastlio2_path" "$BASE_DIR/G1Nav2D/src/fastlio2/path"
        fi
        log "修复 DaoLan 脚本权限..."
        find "$BASE_DIR/scripts" -maxdepth 2 -name "*.sh" -exec chmod +x {} \; 2>/dev/null || true
        log "修复 unitree_sdk2_python 脚本权限..."
        find "$BASE_DIR/unitree_sdk2_python" -name "*.sh" -exec chmod +x {} \; 2>/dev/null || true
        install_mpc_python_deps || warn "MPC 依赖自动安装失败，不影响本次升级完成"
        log "DaoLan 更新完成"
      fi
      ;;
    build2)
      if [[ -f "$EXT_DIR/update_build2.zip" ]]; then
        ensure_dir "$BUILD2_DIR" "BUILD2_DIR" || { warn "build2 更新跳过：无法创建 $BUILD2_DIR（需 /home/ztx/robot_program/）"; continue; }
        log "更新 build2，先停止 houduan02..."
        sudo systemctl stop houduan02.service 2>/dev/null || true
        log "更新 houduan02.service..."
        sudo tee /etc/systemd/system/houduan02.service >/dev/null <<'HOUDUAN02_SVC'
[Unit]
Description=G1 Robot HouDan Controller
After=network.target sound.target
Wants=sound.target

[Service]
Type=simple
User=ztx
WorkingDirectory=/home/ztx/robot_program/unitree_sdk2/build2/bin

# 关键：让 system service 能连上用户会话的 PulseAudio/PipeWire
Environment=XDG_RUNTIME_DIR=/run/user/1000
Environment=PULSE_SERVER=unix:/run/user/1000/pulse/native

ExecStart=/home/ztx/robot_program/unitree_sdk2/build2/bin/houduan02 enp2s0
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
HOUDUAN02_SVC
        sudo systemctl daemon-reload
        log "解压 build2..."
        unzip -q -o "$EXT_DIR/update_build2.zip" -d "$BUILD2_DIR" || { warn "build2 解压失败"; exit 1; }
        log "修复 build2/bin 可执行权限..."
        [[ -d "$BUILD2_BIN" ]] && chmod +x "$BUILD2_BIN"/* 2>/dev/null || true
        log "启动 houduan02..."
        sudo systemctl start houduan02.service 2>/dev/null || true
        log "build2 更新完成"
      fi
      ;;
    frontend)
      if [[ -f "$EXT_DIR/update_frontend.zip" ]]; then
        ensure_dir "$FRONTEND_DIR" "FRONTEND_DIR" || { warn "frontend 更新跳过：无法创建 $FRONTEND_DIR（需 /home/ztx/g1_install/）"; continue; }
        log "更新 frontend（包含 config.js 与 config.js.bak）..."
        unzip -q -o "$EXT_DIR/update_frontend.zip" -d "$FRONTEND_DIR" || { warn "frontend 解压失败"; exit 1; }
        log "重启 nginx 容器..."
        sudo docker restart nginx-static-container 2>/dev/null || true
        log "frontend 更新完成"
      fi
      ;;
    face)
      if [[ -f "$EXT_DIR/update_face.zip" ]]; then
        ensure_dir "$FACE_PARENT" "FACE_PARENT" || { warn "face 更新跳过：无法创建 $FACE_PARENT（需 /home/ztx/robot_program/）"; continue; }
        log "更新 face..."
        unzip -q -o "$EXT_DIR/update_face.zip" -d "$FACE_PARENT" || { warn "face 解压失败"; exit 1; }
        log "修复 face 脚本权限..."
        find "$FACE_DIR" -name "*.sh" -exec chmod +x {} \; 2>/dev/null || true
        _face_venv_activate="$FACE_DIR/Dlib_face_recognition_from_camera/venv/bin/activate"
        if [[ -f "$_face_venv_activate" ]]; then
          (
            # shellcheck disable=SC1090
            source "$_face_venv_activate"
            _mirror_url="https://mirrors.aliyun.com/pypi/simple"
            pip install websocket-client -i "$_mirror_url" || pip install websocket-client || warn "pip install websocket-client 失败"
            if ! pip install ultralytics -i "$_mirror_url"; then
              warn "阿里源安装 ultralytics 失败，尝试先安装 CPU 版 torch/torchvision 后重试..."
              pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu || warn "pip install torch torchvision (cpu) 失败"
              pip install ultralytics -i "$_mirror_url" || pip install ultralytics || warn "二次安装 ultralytics 仍失败，请手动检查网络或源配置"
            fi
          ) || warn "face 依赖安装步骤执行失败"
        else
          warn "未找到人脸识别 venv: $_face_venv_activate"
        fi
        log "face 更新完成"
      fi
      ;;
    linkerhand)
      if [[ -f "$EXT_DIR/update_linkerhand.zip" ]]; then
        ensure_dir "$LINKERHAND_PARENT" "LINKERHAND_PARENT" || { warn "linkerhand 更新跳过：无法创建 $LINKERHAND_PARENT（需 /home/ztx/robot_program/）"; continue; }
        log "更新 linkerhand..."
        unzip -q -o "$EXT_DIR/update_linkerhand.zip" -d "$LINKERHAND_PARENT" || { warn "linkerhand 解压失败"; exit 1; }
        log "修复 linkerhand 脚本权限..."
        find "$LINKERHAND_DIR" -name "*.sh" -exec chmod +x {} \; 2>/dev/null || true
        log "linkerhand 更新完成"
      fi
      ;;
    omni)
      if [[ -f "$EXT_DIR/update_omni.zip" ]]; then
        # robot_program 一定存在；解压后才会有 alibabacloud-bailian-speech-demo
        ensure_dir "$ROBOT_PROGRAM_DIR" "ROBOT_PROGRAM_DIR" || { warn "omni 更新跳过：无法创建 $ROBOT_PROGRAM_DIR"; continue; }
        log "备份 omni robot.json（防止覆盖 api_key）..."
        backup_if_exists "$OMNI_PYTHON_DIR/config/robot.json" "$BACKUP_DIR/omni_robot.json"
        backup_if_exists "$OMNI_PYTHON_DIR/venv" "$BACKUP_DIR/omni_venv"
        log "更新 alibabacloud-bailian-speech-demo → $ROBOT_PROGRAM_DIR/..."
        unzip -q -o "$EXT_DIR/update_omni.zip" -d "$ROBOT_PROGRAM_DIR" || { warn "alibabacloud-bailian-speech-demo 解压失败"; exit 1; }
        if [[ ! -d "$BAILIAN_DIR" ]]; then
          warn "解压后未找到 $BAILIAN_DIR，请检查 update_omni.zip 是否以该目录为根"
          exit 1
        fi
        mkdir -p "$OMNI_PYTHON_DIR/config"
        # 恢复本地密钥配置；动作表不随 omni 包更新（读机载 actionOptions.json）
        if [[ -f "$BACKUP_DIR/omni_robot.json" ]]; then
          log "恢复 omni robot.json..."
          cp -a "$BACKUP_DIR/omni_robot.json" "$OMNI_PYTHON_DIR/config/robot.json"
        fi
        # 包内不得残留密钥 / 废弃的 actions.json
        rm -f "$OMNI_PYTHON_DIR/config/actions.json"
        if [[ -f "$OMNI_PYTHON_DIR/config/robot.json" ]] && [[ ! -f "$BACKUP_DIR/omni_robot.json" ]]; then
          if grep -q 'api_key' "$OMNI_PYTHON_DIR/config/robot.json" 2>/dev/null; then
            warn "更新包中的 robot.json 含 api_key，已删除（请用前端重新保存）"
            rm -f "$OMNI_PYTHON_DIR/config/robot.json"
          fi
        fi
        # venv 不打包；若设备已有则保留（依赖安装会复用并 pip install）
        if [[ -d "$BACKUP_DIR/omni_venv" && ! -d "$OMNI_PYTHON_DIR/venv" ]]; then
          log "恢复 omni venv..."
          cp -a "$BACKUP_DIR/omni_venv" "$OMNI_PYTHON_DIR/venv"
        fi
        log "修复脚本权限并安装 omni 依赖..."
        find "$OMNI_DIR" -name "*.sh" -exec chmod +x {} \; 2>/dev/null || true
        install_omni_python_deps || warn "omni 依赖安装未完全成功，可稍后手动重试"
        if [[ ! -f "$OMNI_PYTHON_DIR/run_with_camera.py" ]]; then
          warn "更新后缺少 run_with_camera.py，请检查 update_omni.zip"
        fi
        if [[ ! -f "/home/ztx/robot_program/unitree_sdk2/build2/bin/actionOptions.json" ]]; then
          warn "机载缺少 actionOptions.json，语音动作列表可能为空"
        fi
        log "alibabacloud-bailian-speech-demo 更新完成"
      else
        warn "更新包中无 update_omni.zip，跳过 omni"
      fi
      ;;
  esac
done

# 写入新版本
mkdir -p "$RUNTIME_DIR"
[[ -n "$NEW_VERSION" ]] && echo "$NEW_VERSION" > "$RUNTIME_DIR/VERSION"

log "更新完成. 版本: ${NEW_VERSION:-未知}"

