#!/usr/bin/env bash
# 桌面快捷方式：先选择更新方式（服务器 / 本地包），再执行更新
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 图形选择：优先 zenity，其次 kdialog
CHOICE=""
if command -v zenity &>/dev/null; then
  CHOICE=$(zenity --list --radiolist \
    --title="DaoLan 系统更新" \
    --text="请选择更新方式：" \
    --column="" --column="方式" \
    TRUE "从服务器下载更新" \
    FALSE "使用本地包更新" \
    2>/dev/null)
elif command -v kdialog &>/dev/null; then
  CHOICE=$(kdialog --radiolist "请选择更新方式：" server "从服务器下载更新" on local "使用本地包更新" off 2>/dev/null)
  [[ "$CHOICE" == "server" ]] && CHOICE="从服务器下载更新"
  [[ "$CHOICE" == "local" ]] && CHOICE="使用本地包更新"
fi

# 无图形界面时在终端选择
if [[ -z "$CHOICE" ]]; then
  echo "=========================================="
  echo "  DaoLan 系统更新"
  echo "=========================================="
  echo ""
  echo "请选择更新方式："
  echo "  1) 从服务器下载更新"
  echo "  2) 使用本地包更新"
  echo ""
  read -r -p "请输入 1 或 2: " ans
  case "$ans" in
    1) CHOICE="从服务器下载更新" ;;
    2) CHOICE="使用本地包更新" ;;
    *) echo "无效选择，已取消"; exit 0 ;;
  esac
fi

if [[ "$CHOICE" == "从服务器下载更新" ]]; then
  exec "$SCRIPT_DIR/run_update_desktop.sh"
elif [[ "$CHOICE" == "使用本地包更新" ]]; then
  exec "$SCRIPT_DIR/run_update_local_desktop.sh"
else
  echo "已取消"
  exit 0
fi
