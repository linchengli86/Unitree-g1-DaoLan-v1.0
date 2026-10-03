"""Trusted-LAN operator setup; explicit permission, never arm or send goals."""
import os
import re
import shlex
import tempfile
from pathlib import Path


def configure_operator(project, payload):
    if set(payload) != {'new_pin', 'enable_navigation', 'module_reviewed'}:
        raise ValueError('设置只接收 PIN 和导航权限确认')
    pin = payload['new_pin']
    if not isinstance(pin, str) or not pin.isascii() or not pin.isdigit() or not 6 <= len(pin) <= 32:
        raise ValueError('PIN 必须是 6 至 32 位数字')
    if type(payload['enable_navigation']) is not bool or payload['module_reviewed'] is not True:
        raise ValueError('请人工确认兼容环境与导航模块验收；不代表所有路线已验收')
    target = Path(project)/'run/config/omni.env'
    if target.is_symlink():
        raise ValueError('私有配置不能是链接')
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_mode & 0o777 != 0o600:
        raise ValueError('配置权限必须为 600')
    content = target.read_text() if target.exists() else (Path(project)/'scripts/omni.env.example').read_text()
    enabled = '1' if payload['enable_navigation'] else '0'
    values = {'GUIDE_OPERATOR_PIN': pin, 'GUIDE_MOTION_ENABLED': enabled,
              'GUIDE_NAMED_NAV_VERIFIED': enabled, 'GUIDE_ARM_ENABLED': '0'}
    lines = []
    for line in content.splitlines():
        match = re.match(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=', line)
        if not match or match.group(1) not in values:
            lines.append(line)
    lines += [name+'='+shlex.quote(value) for name, value in values.items()]
    fd, name = tempfile.mkstemp(prefix='.operator-', dir=str(target.parent))
    try:
        with os.fdopen(fd, 'w') as file:
            file.write('\n'.join(lines)+'\n')
        os.chmod(name, 0o600)
        os.replace(name, str(target))
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return values
