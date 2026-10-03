#!/usr/bin/env bash
# 桌面快捷方式用包装脚本：先验证 sudo 权限，再执行更新
# 在终端中运行，便于用户输入密码并查看输出
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 从 update_config.json 读取 sudo 密码（填写后可免输入，存在安全风险）
SUDO_PASSWORD=""
[[ -f "$SCRIPT_DIR/update_config.json" ]] && SUDO_PASSWORD=$(grep -o '"sudo_password"[[:space:]]*:[[:space:]]*"[^"]*"' "$SCRIPT_DIR/update_config.json" 2>/dev/null | sed 's/.*"\([^"]*\)".*/\1/')

echo "=========================================="
echo "  DaoLan 系统更新"
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
set +e
./do_update.sh --all
_ret=$?
set -e
echo ""
if [[ $_ret -ne 0 ]]; then
  echo "[失败] 请查看上方错误信息，或联系技术支持。"
fi
read -p "按回车键关闭..."
exit $_ret
