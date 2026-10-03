#!/usr/bin/env bash
# 安装 DaoLan 更新 桌面快捷方式
# 在客户机执行一次即可，之后可从桌面或应用菜单双击运行更新
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNNER="$SCRIPT_DIR/run_update_choose_desktop.sh"

# 检测可用的终端
TERM_CMD=""
for cmd in gnome-terminal xfce4-terminal konsole xterm; do
  if command -v "$cmd" &>/dev/null; then
    TERM_CMD="$cmd"
    break
  fi
done
[[ -z "$TERM_CMD" ]] && TERM_CMD="x-terminal-emulator"

# 根据终端类型构建 Exec 命令
case "$TERM_CMD" in
  gnome-terminal|xfce4-terminal)
    EXEC="$TERM_CMD -- bash -c \"$RUNNER\""
    ;;
  konsole)
    EXEC="konsole -e bash -c \"$RUNNER\""
    ;;
  *)
    EXEC="$TERM_CMD -e bash -c \"$RUNNER\""
    ;;
esac

DESKTOP_CONTENT="[Desktop Entry]
Version=1.0
Type=Application
Name=DaoLan 系统更新
Comment=可选择从服务器下载或使用本地包更新 DaoLan 全部组件
Exec=$EXEC
Icon=system-software-update
Terminal=false
Categories=System;Settings;
StartupNotify=true
"

# 安装到应用菜单
APPS_DIR="$HOME/.local/share/applications"
mkdir -p "$APPS_DIR"
echo "$DESKTOP_CONTENT" > "$APPS_DIR/daolan-update.desktop"
chmod +x "$APPS_DIR/daolan-update.desktop"
echo "已安装到应用菜单: $APPS_DIR/daolan-update.desktop"

# 安装到桌面（支持中文等本地化目录名，如 ~/桌面）
DESKTOP_DIR=""
if command -v xdg-user-dir &>/dev/null; then
  DESKTOP_DIR=$(xdg-user-dir DESKTOP 2>/dev/null)
fi
[[ -z "$DESKTOP_DIR" || ! -d "$DESKTOP_DIR" ]] && DESKTOP_DIR="${XDG_DESKTOP_DIR:-$HOME/Desktop}"
if [[ -d "$DESKTOP_DIR" ]]; then
  cp "$APPS_DIR/daolan-update.desktop" "$DESKTOP_DIR/"
  chmod +x "$DESKTOP_DIR/daolan-update.desktop"
  # 标记为可信，否则双击可能提示「未信任」且允许启动无效
  if command -v gio &>/dev/null; then
    gio set "$DESKTOP_DIR/daolan-update.desktop" metadata::trusted true 2>/dev/null || true
  fi
  echo "已创建桌面快捷方式: $DESKTOP_DIR/daolan-update.desktop"
else
  echo "未找到桌面目录，仅安装到应用菜单"
fi

# 确保包装脚本可执行
chmod +x "$RUNNER" "$SCRIPT_DIR/run_update_desktop.sh" "$SCRIPT_DIR/run_update_local_desktop.sh" 2>/dev/null || true

echo ""
echo "安装完成。双击「DaoLan 系统更新」即可运行，首次会提示选择：从服务器下载 或 使用本地包，并输入管理员密码。"
