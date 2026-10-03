#!/usr/bin/env bash
# 桌面快捷方式：使用本地包更新
# 弹出文件选择框让用户选择 zip 包，再执行更新
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 从 update_config.json 读取 sudo 密码（填写后可免输入）
SUDO_PASSWORD=""
[[ -f "$SCRIPT_DIR/update_config.json" ]] && SUDO_PASSWORD=$(grep -o '"sudo_password"[[:space:]]*:[[:space:]]*"[^"]*"' "$SCRIPT_DIR/update_config.json" 2>/dev/null | sed 's/.*"\([^"]*\)".*/\1/')

echo "=========================================="
echo "  DaoLan 系统更新（本地包）"
echo "=========================================="
echo ""

# 预先验证 sudo
if [[ -n "$SUDO_PASSWORD" ]]; then
  echo "$SUDO_PASSWORD" | sudo -S -v 2>/dev/null || {
    echo ""; echo "密码错误或权限验证失败。"; read -p "按回车键关闭..."; exit 1
  }
else
  echo "需要管理员权限，请输入密码..."
  if ! sudo -v; then
    echo ""
    echo "权限验证失败，无法继续更新。"
    read -p "按回车键关闭..."
    exit 1
  fi
fi

echo ""
# 选择 zip 文件：优先用 zenity 图形选择，否则用 kdialog，最后退化为手动输入
ZIP_PATH=""
if command -v zenity &>/dev/null; then
  ZIP_PATH=$(zenity --file-selection --title="选择更新包" --file-filter="更新包 (*.zip) | *.zip" --file-filter="所有文件 (*) | *" 2>/dev/null)
elif command -v kdialog &>/dev/null; then
  ZIP_PATH=$(kdialog --getopenfilename "$HOME" "更新包 (*.zip) | *.zip" 2>/dev/null)
fi

if [[ -z "$ZIP_PATH" || ! -f "$ZIP_PATH" ]]; then
  echo "未选择有效文件，请手动输入更新包路径（.zip）："
  read -r ZIP_PATH
fi

if [[ -z "$ZIP_PATH" || ! -f "$ZIP_PATH" ]]; then
  echo ""
  echo "未找到文件，更新已取消。"
  read -p "按回车键关闭..."
  exit 1
fi

echo "使用本地包: $ZIP_PATH"
echo ""
set +e
./do_update.sh --local "$ZIP_PATH" --all
_ret=$?
set -e
echo ""
if [[ $_ret -ne 0 ]]; then
  echo "[失败] 请查看上方错误信息，或联系技术支持。"
fi
read -p "按回车键关闭..."
exit $_ret
