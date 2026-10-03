#!/usr/bin/env python3
"""Configure the PC2 Omni key without an editor or terminal echo."""

import getpass
import argparse
import os
import re
import shlex
import tempfile
from pathlib import Path

from guide_actions import ARM_ACTIONS


def main():
    parser = argparse.ArgumentParser(description="配置百炼密钥或网页运动权限")
    parser.add_argument("--motion", choices=("on", "off"), help="开启时提示输入操作员 PIN")
    parser.add_argument("--verified-arm", help="现场验证后登记动作，用逗号分隔，如 wave,clap；none 关闭")
    args = parser.parse_args()
    if args.motion == "off" and args.verified_arm not in (None, "none"):
        parser.error("关闭运动时不能同时开启手臂动作")
    project = Path(__file__).resolve().parent.parent
    target = project / "run" / "config" / "omni.env"
    target.parent.mkdir(parents=True, exist_ok=True)
    content = target.read_text() if target.exists() else (
        project / "scripts" / "omni.env.example"
    ).read_text()
    updates = {}
    if args.motion == "on":
        pin = getpass.getpass("设置操作员 PIN（至少 6 位数字，输入隐藏）: ").strip()
        if len(pin) < 6 or not pin.isascii() or not pin.isdigit():
            raise SystemExit("PIN 须为至少 6 位数字，配置未修改")
        updates.update(GUIDE_MOTION_ENABLED="1", GUIDE_OPERATOR_PIN=pin)
    elif args.motion == "off":
        updates.update(GUIDE_MOTION_ENABLED="0", GUIDE_ARM_ENABLED="0")
    if args.verified_arm is not None:
        gestures = [value.strip() for value in args.verified_arm.split(",")]
        if gestures == ["none"]:
            updates.update(GUIDE_ARM_ENABLED="0", GUIDE_VERIFIED_ARM_ACTIONS="")
        else:
            if not gestures or any(value not in ARM_ACTIONS for value in gestures):
                raise SystemExit("未知手臂动作；可选 " + ",".join(ARM_ACTIONS))
            updates.update(GUIDE_ARM_ENABLED="1", GUIDE_VERIFIED_ARM_ACTIONS=",".join(sorted(set(gestures))))
    if not updates:
        key = getpass.getpass("粘贴百炼 API Key，然后按回车（输入隐藏）: ").strip()
        if not key or any(c.isspace() for c in key):
            raise SystemExit("Key 为空或包含空白，配置未修改")
        updates["DASHSCOPE_API_KEY"] = key
    lines = content.splitlines()
    kept = []
    for line in lines:
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if not match or match.group(1) not in updates:
            kept.append(line)
    lines = kept + [name + "=" + shlex.quote(value) for name, value in updates.items()]
    descriptor, name = tempfile.mkstemp(prefix=".omni-", dir=str(target.parent))
    try:
        with os.fdopen(descriptor, "w") as output:
            output.write("\n".join(lines) + "\n")
        os.chmod(name, 0o600)
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    print("配置已保存，权限为 600；请重启手机导览服务。")


if __name__ == "__main__":
    main()
