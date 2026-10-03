#!/usr/bin/env python3
"""Finite, owned ROS mapping worker; no speed commands or motion enabling."""
import argparse
import fcntl
import json
import math
import os
import signal
import subprocess
import time
from urllib.parse import urlparse
from pathlib import Path

from guide_points import PROJECT
from guide_mapping import atomic_json, FILES
import initialize_robot_services as init


def inventory():
    uri = urlparse(os.environ.get('ROS_MASTER_URI', 'http://localhost:11311'))
    if uri.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise RuntimeError('网页建图仅管理本机 ROS Master；拒绝远程 Master')
    state = init.probe('master', 5)
    init.assert_idle(state)
    return state


def offline_check():
    state = inventory()
    if set(state.get('nodes', [])) & {
        '/move_base', '/localizer_node', '/slam_reloc', '/map_builder_node',
        '/velocity_smoother_ema', '/unitree_safe_controller', '/octomap_server'}:
        raise RuntimeError('ROS 运动/定位/建图节点仍在运行')
    if state.get('master_ready'):
        import socket
        import rosgraph
        socket.setdefaulttimeout(3)
        publishers, subscribers, services = rosgraph.Master('/daolan_mapping_offline').getSystemState()
        if any(name in ('/cmd_vel', '/cmd_vel_smooth', '/move_base/goal') and owners
               for name, owners in publishers+subscribers) or any(
               name in ('/unitree_motion_enable', '/slam_reloc') and owners for name,owners in services):
            raise RuntimeError('存在未归属的运动/定位 ROS 接口，不能切地图')


def stop_owned(record, timeout=25):
    def identity():
        current = init.process_record(record['pid'])
        if current and any(current[k] != record[k] for k in ('argv', 'cwd', 'uid', 'start_ticks')):
            raise RuntimeError('服务 PID 归属变化，拒绝停止')
        return current
    if identity():
        os.kill(record['pid'], signal.SIGINT)
    deadline = time.monotonic()+timeout
    while identity():
        if time.monotonic() >= deadline:
            raise TimeoutError('原服务未退出；不强杀，不启动重复节点')
        time.sleep(.1)


def prepare_mapping():
    interface = os.environ.get('CONTROL_IFACE', 'eth0')
    init.check_network(interface)
    services = init.find_services(PROJECT, interface)
    init.validate_inventory(services)
    state = inventory()
    # Stop only software created by this project's initializer, never pgrep.
    for role in ('safe_controller', 'navigation'):
        if services[role]:
            record = services[role][0]
            owned = init.read_owned_pid(PROJECT/'run/pids'/('init_'+role+'.json'), role, PROJECT, interface)
            if not owned or owned['pid'] != record['pid'] or owned['start_ticks'] != record['start_ticks']:
                raise RuntimeError('现有服务不是本项目初始化器启动，不能自动切建图：'+role)
            if role == 'safe_controller':
                init.verify_controller_pid(state, [record])
                init.probe('disable', 5, controller_pid=record['pid'])
            stop_owned(record)
    offline_check()


def ros_helper():
    # Apply only to this already-running Python helper, not the FastLIO parent.
    from navigation_thread_budget import apply_current_process
    apply_current_process()
    import rospy
    if not rospy.core.is_initialized():
        rospy.init_node('daolan_web_mapping_helper', anonymous=True, disable_signals=True)
    return rospy


def ready_sensors():
    rospy = ros_helper()
    from sensor_msgs.msg import Imu, PointCloud2
    for name, kind in (('/livox/imu', Imu), ('/body_cloud', PointCloud2)):
        msg = rospy.wait_for_message(name, kind, timeout=5)
        age = rospy.Time.now().to_sec()-msg.header.stamp.to_sec()
        if not -.1 <= age <= 1.5:
            raise ValueError('建图输入时间异常：'+name)
        if kind == PointCloud2 and not msg.width*msg.height:
            raise ValueError('建图点云为空')


def save_grid(directory):
    rospy = ros_helper()
    from nav_msgs.msg import OccupancyGrid
    msg = rospy.wait_for_message('/projected_map', OccupancyGrid, timeout=8)
    info = msg.info
    age = rospy.Time.now().to_sec()-msg.header.stamp.to_sec()
    if msg.header.frame_id != 'map' or not -.1 <= age <= 3:
        raise ValueError('二维地图坐标或时间无效，拒绝保存')
    width, height = info.width, info.height
    if not 0 < width*height <= 20_000_000 or len(msg.data) != width*height:
        raise ValueError('二维地图大小无效')
    if not math.isfinite(info.resolution) or not 0 < info.resolution <= 1:
        raise ValueError('二维地图分辨率无效')
    if not any(0 <= x <= 25 for x in msg.data) or not any(x >= 65 for x in msg.data):
        raise ValueError('二维地图缺少自由/障碍区域，不能激活空图')
    origin = info.origin
    values = [origin.position.x, origin.position.y, origin.position.z,
              origin.orientation.x, origin.orientation.y, origin.orientation.z, origin.orientation.w]
    if not all(math.isfinite(value) for value in values):
        raise ValueError('地图 origin 无效')
    q = origin.orientation
    if abs(q.x) > .001 or abs(q.y) > .001 or abs(q.x*q.x+q.y*q.y+q.z*q.z+q.w*q.w-1) > .01:
        raise ValueError('二维地图必须是有效平面姿态')
    yaw = math.atan2(2*q.w*q.z, 1-2*q.z*q.z)
    target = directory/'map'
    target.mkdir(parents=True, exist_ok=True)
    pixels = bytearray(width*height)
    for row in range(height):
        for col in range(width):
            value = msg.data[row*width+col]
            pixels[(height-1-row)*width+col] = 0 if value >= 65 else 254 if 0 <= value <= 25 else 205
    (target/'map.pgm').write_bytes(('P5\n%d %d\n255\n' % (width, height)).encode()+pixels)
    (target/'map.yaml').write_text('image: map.pgm\nresolution: %s\norigin: [%s, %s, %s]\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.25\n' % (
        info.resolution, origin.position.x, origin.position.y, yaw))


def run(directory):
    directory = directory.resolve(strict=True)
    directory.relative_to((PROJECT/'run/mapping').resolve())
    if directory.parent != (PROJECT/'run/mapping').resolve() or not all(
            c in '0123456789abcdef' for c in directory.name) or len(directory.name) != 32:
        raise ValueError('建图会话路径无效')
    def report(state, message):
        atomic_json(directory/'status.json', {'state': state, 'message': message})
    report('starting', '检查内部网络并安全退出归属明确的导航/控制服务')
    prepare_mapping()
    for name in FILES:
        (directory/name).parent.mkdir(parents=True, exist_ok=True)
    command = ['roslaunch', 'fastlio', 'mapping.launch', 'rviz:=false',
               'web_mapping:=true', 'map_output_path:='+str(directory/FILES[2]),
               'ground_map_output_path:='+str(directory/FILES[3]),
               'keyposes_output_path:='+str(directory/FILES[4])]
    with (directory/'roslaunch.log').open('ab') as log:
        launch = subprocess.Popen(command, cwd=str(PROJECT/'G1Nav2D'), stdin=subprocess.DEVNULL,
                                  stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    cancelled = [False]
    def cancel(_sig, _frame):
        cancelled[0] = True
    signal.signal(signal.SIGINT, cancel)
    signal.signal(signal.SIGTERM, cancel)
    try:
        # Wait for bounded fresh sensors; existence of a launch is not readiness.
        deadline = time.monotonic()+40
        ready = False
        while time.monotonic() < deadline:
            if launch.poll() is not None:
                raise RuntimeError('建图节点提前退出')
            if cancelled[0] or (directory/'cancel.request').exists():
                raise InterruptedError('建图已取消')
            state = init.probe('master', 5)
            if '/map_builder_node' in state.get('nodes', []):
                ready = True
                break
            time.sleep(.2)
        if not ready:
            raise TimeoutError('建图启动超时')
        ready_sensors()
        report('mapping', '正在建图：仅使用遥控器走完整个场地，结束后停稳并点保存')
        deadline = time.monotonic()+3600
        while not (directory/'finish.request').exists():
            if cancelled[0] or (directory/'cancel.request').exists():
                raise InterruptedError('建图已取消')
            if launch.poll() is not None:
                raise RuntimeError('建图会话意外退出')
            if time.monotonic() >= deadline:
                raise TimeoutError('建图超过一小时，会话停止')
            time.sleep(.2)
        report('saving', '保存 map 坐标二维栅格并退出节点，保存三维/地面地图和关键位姿')
        save_grid(directory)
    finally:
        if launch.poll() is None:
            # Own new session only; no unrelated rosnode/roscore is killed.
            os.killpg(launch.pid, signal.SIGINT)
            try:
                launch.wait(timeout=45)
            except subprocess.TimeoutExpired:
                # Never mark a potentially live mapping tree as safely stopped.
                report('saving', '保存/退出尚未结束；禁止激活地图，需检查本会话进程')
                while launch.poll() is None:
                    time.sleep(.5)
                raise TimeoutError('建图节点未在保存预算内退出；本会话不验收')
        offline_check()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--offline-check', action='store_true')
    args = parser.parse_args()
    try:
        if args.offline_check:
            offline_check()
        elif args.directory:
            lock_path = PROJECT/'run/mapping/session.lock'
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open('a') as guard:
                fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
                run(args.directory)
        else:
            parser.error('缺少固定会话路径')
    except Exception as exc:
        print(json.dumps({'status': 'error', 'message': str(exc)}, ensure_ascii=False), flush=True)
        raise SystemExit(1)
