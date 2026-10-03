#!/usr/bin/env python3
"""Supported PC2 bootstrap: explicit installation, build, then motion-only web."""
import argparse
import json
import os
import platform
import re
import shlex
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
PACKAGES = ('build-essential', 'cmake', 'git', 'curl', 'iproute2', 'iputils-ping',
    'pkg-config', 'qtbase5-dev', 'libjsoncpp-dev', 'libyaml-cpp-dev', 'libapr1-dev',
    'python3-dev', 'python3-setuptools', 'python3-pip', 'python3-venv', 'python3-opencv',
    'python3-numpy', 'python3-yaml', 'python3-pil', 'libportaudio2', 'libpcl-dev',
    'libopencv-dev', 'ros-noetic-ros-base', 'ros-noetic-navigation', 'ros-noetic-gtsam',
    'ros-noetic-tf2-sensor-msgs', 'ros-noetic-teb-local-planner',
    'ros-noetic-costmap-converter', 'ros-noetic-octomap-server', 'ros-noetic-pcl-ros',
    'ros-noetic-eigen-conversions', 'ros-noetic-roslint')


def run(command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def supported(os_release=None, arch=None):
    data = {}
    for line in (os_release or Path('/etc/os-release').read_text()).splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            data[key] = value.strip('"')
    if data.get('ID') != 'ubuntu' or data.get('VERSION_ID') != '20.04' or (arch or platform.machine()) not in ('aarch64', 'arm64'):
        raise RuntimeError('当前支持 Ubuntu 20.04 aarch64 PC2；其他系统先做兼容验收，不自动安装')
    return data


def control_python():
    value = os.environ.get('CONTROL_PYTHON')
    return Path(value) if value else Path.home()/'robot_dev/envs/unitree-core/bin/python'


def load_selected_config():
    """Read literal setup paths only; never execute config or print secrets."""
    path = PROJECT/'run/config/omni.env'
    if not path.exists():
        return
    if path.is_symlink() or path.stat().st_mode & 0o777 != 0o600:
        raise RuntimeError('私有配置必须是普通文件且权限为 600')
    for line in path.read_text().splitlines():
        match = re.match(r'^\s*(?:export\s+)?(CONTROL_PYTHON|CYCLONEDDS_HOME|CONTROL_IFACE)\s*=(.*)$', line)
        if not match:
            continue
        parts = shlex.split(match[2], comments=True)
        if len(parts) != 1 or any(char in parts[0] for char in '$`\n'):
            raise RuntimeError('环境路径必须是单个字面值：'+match[1])
        os.environ.setdefault(match[1], parts[0])


def check():
    supported()
    python = control_python()
    checks = {'ros': Path('/opt/ros/noetic/setup.bash').is_file(),
              'control_python': python.is_file(), 'navigation_build': (PROJECT/'G1Nav2D/devel/setup.bash').is_file()}
    checks['navigation_nodes'] = all(os.access(PROJECT/'G1Nav2D/devel/lib'/name, os.X_OK)
        for name in ('fastlio/map_builder_node', 'fastlio/localizer_node',
                     'velocity_smoother_ema/velocity_smoother_ema_node'))
    if checks['control_python']:
        probe = subprocess.run([str(python), '-c', 'import cyclonedds; from unitree_sdk2py.g1.loco.g1_loco_client import LocoClient'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        checks['sdk_import'] = probe.returncode == 0
    else:
        checks['sdk_import'] = False
    return checks


def prepare(with_omni=False):
    supported()
    from initialize_robot_services import find_services, validate_inventory
    from guide_mapping import session_running
    services = find_services(PROJECT, os.environ.get('CONTROL_IFACE', 'eth0'))
    validate_inventory(services)
    if services['navigation'] or services['safe_controller'] or session_running(PROJECT):
        raise RuntimeError('安装/构建前请停止导航、控制与建图服务；不会自动停止现有服务')
    dds_prefix = None
    # The operator explicitly opts into system installation. No unsigned sources.
    run(['sudo', '-v'])
    run(['sudo', 'apt-get', 'update'])
    run(['sudo', 'apt-get', 'install', '-y', *PACKAGES])
    sdk = PROJECT/'Livox-SDK2'
    run(['cmake', '-S', str(sdk), '-B', str(sdk/'build'), '-DCMAKE_BUILD_TYPE=Release'])
    run(['cmake', '--build', str(sdk/'build'), '--parallel', '4'])
    run(['sudo', 'cmake', '--install', str(sdk/'build')])
    run(['sudo', 'ldconfig'])
    python = control_python()
    if not python.is_file():
        run(['/usr/bin/python3', '-m', 'venv', '--system-site-packages', str(python.parent.parent)])
    # Keep vendor DDS if already present; do not silently replace its version.
    probe = subprocess.run([str(python), '-c', 'import cyclonedds'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if probe.returncode:
        dds = PROJECT/'run/vendor/CycloneDDS-0.10.2'
        dds.parent.mkdir(parents=True, exist_ok=True)
        if not dds.exists():
            run(['git', 'clone', '--depth', '1', '--branch', '0.10.2', '--single-branch',
                 'https://github.com/eclipse-cyclonedds/cyclonedds.git', str(dds)])
        tag = subprocess.check_output(['git', '-C', str(dds), 'describe', '--exact-match', '--tags'], text=True).strip()
        if tag != '0.10.2' or subprocess.check_output(['git', '-C', str(dds), 'status', '--porcelain']).strip():
            raise RuntimeError('DDS 源码版本或工作区异常，不自动覆盖')
        prefix = dds/'install'
        run(['cmake', '-S', str(dds), '-B', str(dds/'build'), '-DCMAKE_INSTALL_PREFIX='+str(prefix), '-DBUILD_TESTING=OFF'])
        run(['cmake', '--build', str(dds/'build'), '--parallel', '4'])
        run(['cmake', '--install', str(dds/'build')])
        environment = dict(os.environ, CYCLONEDDS_HOME=str(prefix))
        run([str(python), '-m', 'pip', 'install', 'cyclonedds==0.10.2'], env=environment)
        dds_prefix = str(prefix)
        os.environ['CYCLONEDDS_HOME'] = dds_prefix
    # Preserve existing vendor numerical stack; populate it only when missing.
    probe = subprocess.run([str(python), '-c', 'import numpy'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if probe.returncode:
        run([str(python), '-m', 'pip', 'install', 'numpy==1.24.4'])
    run([str(python), '-m', 'pip', 'install', '--no-deps', '-e', str(PROJECT/'unitree_sdk2_python')])
    run(['bash', '-lc', 'source /opt/ros/noetic/setup.bash && bash '+shlex.quote(str(PROJECT/'G1Nav2D/build.sh'))], cwd=str(PROJECT))
    # Pure navigation never depends on model dependencies or credentials.
    if with_omni:
        omni = PROJECT/'run/omni-venv'
        if not (omni/'bin/python').is_file():
            run(['/usr/bin/python3', '-m', 'venv', str(omni)])
        run([str(omni/'bin/python'), '-m', 'pip', 'install', '-r', str(PROJECT/'scripts/omni-requirements.txt')])
    config = PROJECT/'run/config/omni.env'
    if config.is_symlink():
        raise RuntimeError('私有配置是链接，不自动改写')
    config.parent.mkdir(parents=True, exist_ok=True)
    if not config.exists():
        with config.open('x') as target:
            target.write((PROJECT/'scripts/omni.env.example').read_text())
        config.chmod(0o600)
    if config.stat().st_mode & 0o777 != 0o600:
        raise RuntimeError('私有配置权限必须为 600')
    if dds_prefix:
        import re
        import tempfile
        lines = [line for line in config.read_text().splitlines()
                 if not re.match(r'^\s*(?:export\s+)?CYCLONEDDS_HOME\s*=', line)]
        descriptor, temporary = tempfile.mkstemp(prefix='.dds-', dir=str(config.parent))
        with os.fdopen(descriptor, 'w') as file:
            file.write('\n'.join(lines+['CYCLONEDDS_HOME='+shlex.quote(dds_prefix)])+'\n')
        os.chmod(temporary, 0o600)
        os.replace(temporary, str(config))
    # Record only versions, never pip freeze URL credentials or process env.
    versions = {}
    for package in PACKAGES:
        result = subprocess.run(['dpkg-query', '-W', '-f=${Version}', package], capture_output=True, text=True)
        if result.returncode == 0:
            versions[package] = result.stdout.strip()
    report = {'schema': 1, 'architecture': platform.machine(), 'os': 'ubuntu20.04',
              'packages': versions, 'motion_enabled_by_bootstrap': False,
              'hardware_and_clean_install_acceptance_pending': True}
    result = subprocess.check_output([str(python), '-c', 'import json,importlib.metadata as m; print(json.dumps({k:m.version(k) for k in ("cyclonedds","numpy","unitree_sdk2py")}))'], text=True)
    report['control_python_packages'] = json.loads(result)
    (PROJECT/'run/setup_versions.json').write_text(json.dumps(report, indent=2)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='只读检查，不安装或启动服务')
    parser.add_argument('--prepare', action='store_true', help='明确授权 apt/SDK 构建和 Python 环境安装')
    parser.add_argument('--with-omni', action='store_true', help='首次准备时另装可选模型依赖（仍不写 Key）')
    args = parser.parse_args()
    try:
        if args.with_omni and not args.prepare:
            raise RuntimeError('--with-omni 仅与 --prepare 一起使用')
        load_selected_config()
        if args.prepare:
            prepare(with_omni=args.with_omni)
        state = check()
        if args.check:
            print(json.dumps({'checks': state, 'ready': all(state.values())}, ensure_ascii=False))
            return 0 if all(state.values()) else 2
        if not all(state.values()):
            raise RuntimeError('环境未准备完成：'+','.join(k for k,v in state.items() if not v)+
                               '；兼容 PC2 首次安装运行 bash scripts/quickstart.sh --prepare')
        run(['bash', str(PROJECT/'scripts/mobile_guide_start.sh')], cwd=str(PROJECT))
        print('打开 http://<PC2-IP>:8765/setup；新场地先建图，已有地图跳过。')
        return 0
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print('准备未完成，不启动机器人任务：'+str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
