#!/usr/bin/env python3
"""Fixed, finite PC2 initializer. No goals, arming, TTS or automatic relocation.

Only navigation.launch (RViz off) and the DISARMED safe controller can be
started. Existing/partial services are checked, not restarted. ROS probes run
in bounded children so a stuck ROS peer cannot block the web worker forever.
"""

import argparse
import json
import math
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from guide_points import PROJECT, GuidePointStore, validate_samples

REQUIRED_NODES = {"/localizer_node", "/slam_reloc", "/move_base", "/velocity_smoother_ema"}
ACTIVE_STATES = {0, 1, 6, 7}
CANCEL = threading.Event()


def emit(data):
    print(json.dumps(data, ensure_ascii=False, allow_nan=False), flush=True)


def check_cancel():
    if CANCEL.is_set():
        raise InterruptedError("初始化已取消；不会关闭已有服务或启用自动运动")


def process_record(pid, proc_root=Path("/proc")):
    """Read immutable launch identity, not kill(0) or a text pgrep match."""
    directory = proc_root / str(pid)
    try:
        argv = [part.decode("utf-8", "replace") for part in (directory / "cmdline").read_bytes().split(b"\0") if part]
        stat = (directory / "stat").read_text()
        fields = stat[stat.rfind(")") + 2:].split()
        if not argv or fields[0] in ("Z", "X"):
            return None
        return {"pid": int(pid), "argv": argv, "start_ticks": int(fields[19]),
                "cwd": str((directory / "cwd").resolve()), "uid": directory.stat().st_uid}
    except (OSError, ValueError, IndexError):
        return None


def process_role(record, project=PROJECT, interface="eth0"):
    if not record:
        return None
    project = Path(project).resolve()
    argv, cwd = record["argv"], Path(record["cwd"])
    names = [Path(arg).name for arg in argv]
    if any(name.startswith("g1_control") and name.endswith(".py") and name != "g1_control_safe.py" for name in names):
        return "unsafe_controller"
    if "g1_control_safe.py" in names:
        index = names.index("g1_control_safe.py")
        script = Path(argv[index])
        absolute = script if script.is_absolute() else cwd / script
        expected = project / "unitree_sdk2_python/example/g1/high_level/g1_control_safe.py"
        if absolute.resolve() != expected or index + 1 >= len(argv) or argv[index + 1] != interface or record["uid"] != os.getuid():
            return "foreign_controller"
        return "safe_controller"
    if "roslaunch" in names and "fastlio" in argv and "navigation.launch" in argv:
        # A launch from another project/uid cannot be owned or safely reused.
        if cwd.resolve() not in (project, project / "G1Nav2D") or record["uid"] != os.getuid():
            return "foreign_navigation"
        return "navigation"
    return None


def find_services(project=PROJECT, interface="eth0", proc_root=Path("/proc")):
    services = {name: [] for name in ("navigation", "safe_controller", "unsafe_controller", "foreign_controller", "foreign_navigation")}
    for directory in proc_root.iterdir():
        if directory.name.isdigit():
            record = process_record(int(directory.name), proc_root)
            role = process_role(record, project, interface)
            if role:
                services[role].append(record)
    return services


def validate_inventory(services):
    if services["unsafe_controller"]:
        raise RuntimeError("检测到旧 g1_control.py，拒绝初始化；请由操作员关闭旧控制器，不会自动终止它")
    if services["foreign_controller"] or services["foreign_navigation"]:
        raise RuntimeError("发现其他路径、用户或网卡的控制/导航进程，不能安全确认归属；请人工检查")
    if len(services["safe_controller"]) > 1 or len(services["navigation"]) > 1:
        raise RuntimeError("发现重复的控制器或导航启动进程；不会重复启动或自动终止进程")


def read_owned_pid(path, role, project=PROJECT, interface="eth0", proc_root=Path("/proc")):
    try:
        stored = json.loads(Path(path).read_text())
        current = process_record(stored["pid"], proc_root)
        if not current or stored.get("start_ticks") != current["start_ticks"] or stored.get("project") != str(Path(project).resolve()):
            return None
        return current if process_role(current, project, interface) == role else None
    except (OSError, ValueError, TypeError, KeyError):
        return None


def write_pid(path, process, role, project=PROJECT, interface="eth0"):
    deadline = time.monotonic() + 1
    record = None
    while time.monotonic() < deadline:
        check_cancel()
        record = process_record(process.pid)
        if record and process_role(record, project, interface) == role:
            break
        if process.poll() is not None:
            raise RuntimeError("服务启动进程提前退出，请检查初始化日志")
        time.sleep(0.02)
    if record is None or process_role(record, project, interface) != role:
        raise RuntimeError("无法核验新启动进程的真实命令，未记录不可靠 PID")
    record.update(project=str(Path(project).resolve()), role=role)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False))
    os.replace(str(temporary), str(path))
    return record


def run_finite(command, timeout, check=True):
    check_cancel()
    # A probe shares this helper's process group so web cancellation can reap
    # it as well. Persistent services below use separate sessions instead.
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.monotonic() + timeout
    try:
        while True:
            check_cancel()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("初始化检查超时：" + Path(command[0]).name)
            try:
                output, errors = process.communicate(timeout=min(0.1, remaining))
                if len(output) > 65536 or len(errors) > 65536:
                    raise RuntimeError("初始化检查返回数据过大")
                if check and process.returncode:
                    raise RuntimeError("初始化检查失败：" + errors.decode("utf-8", "replace")[-600:].strip())
                return process.returncode, output
            except subprocess.TimeoutExpired:
                continue
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=0.3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=0.3)


def probe(mode, timeout=5, controller_pid=None):
    command = ["/usr/bin/python3", "-u", str(Path(__file__).resolve()), "--probe", mode]
    if controller_pid is not None:
        if mode != "disable" or type(controller_pid) is not int or controller_pid <= 0:
            raise ValueError("初始化控制器 PID 校验参数无效")
        command += ["--controller-pid", str(controller_pid)]
    _, output = run_finite(command, timeout, check=False)
    for line in reversed(output.decode("utf-8", "replace").splitlines()):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("status") in ("success", "error"):
            if data["status"] == "error":
                raise RuntimeError(data.get("message", "ROS 检查失败"))
            return data
    raise RuntimeError("ROS 检查未返回有效结果")


def verify_files(project=PROJECT):
    import yaml
    from capture_guide_pose import map_fingerprint
    project = Path(project)
    required = [project / "G1Nav2D/devel/setup.bash", project / "G1Nav2D/src/fastlio2/launch/navigation.launch",
                project / "map/map.yaml", project / "G1Nav2D/src/fastlio2/PCD/map.pcd",
                project / "G1Nav2D/src/fastlio2/PCD/ground_map.pcd"]
    for path in required:
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError("缺少或为空的部署文件：" + str(path.relative_to(project)))
    config = yaml.safe_load((project / "map/map.yaml").read_text())
    if not isinstance(config, dict) or not isinstance(config.get("image"), str):
        raise RuntimeError("map.yaml 的图像设置无效")
    image = (project / "map" / config["image"]).resolve()
    image.relative_to(project.resolve())
    if not image.is_file() or image.stat().st_size == 0:
        raise RuntimeError("地图栅格图像缺失或为空")
    # The imported fingerprint intentionally hashes the same deployed map as
    # capture_guide_pose. This helper is installed in and run from that project.
    if project.resolve() != PROJECT.resolve():
        raise RuntimeError("地图指纹检查必须使用当前部署项目")
    fingerprint = map_fingerprint()
    store = GuidePointStore(project / "config/guide_points.json")
    points = store.list()
    ids = set()
    for point in points:
        point_id = point.get("id", "")
        if not isinstance(point_id, str) or len(point_id) != 32 or any(c not in "0123456789abcdef" for c in point_id) or point_id in ids:
            raise RuntimeError("保存的导览点编号无效或重复，原文件已保留")
        ids.add(point_id)
        if point.get("map_fingerprint") != fingerprint:
            raise RuntimeError("导览点 " + str(point.get("name", point_id))[:40] + " 与当前地图版本不匹配，原资料已保留")
        if point.get("image"):
            path = store.image_path(point_id)
            if not path.is_file() or path.stat().st_size == 0:
                raise RuntimeError("保存的展板照片缺失：" + str(point.get("name", point_id))[:40])
    return {"point_count": len(points), "photo_count": sum(bool(p.get("image")) for p in points), "map_fingerprint": fingerprint}


def check_network(interface):
    if interface != "eth0":
        raise RuntimeError("当前部署只验收过 eth0；请人工核验 CONTROL_IFACE，不自动选择其他网卡")
    _, output = run_finite(["ip", "-j", "addr", "show", "dev", interface], 2)
    adapters = json.loads(output.decode("utf-8"))
    if not adapters or "UP" not in adapters[0].get("flags", []):
        raise RuntimeError("eth0 未启用，请检查机器人内部以太网")
    ipv4 = [address.get("local") for address in adapters[0].get("addr_info", []) if address.get("family") == "inet"]
    if not any(isinstance(address, str) and address.startswith("192.168.123.") for address in ipv4):
        raise RuntimeError("eth0 没有机器人网段地址 192.168.123.x")
    code, _ = run_finite(["ping", "-c", "1", "-W", "1", "-I", interface, "192.168.123.120"], 2, check=False)
    if code:
        raise RuntimeError("雷达 192.168.123.120 不可达；请检查硬件供电和网线，网页无法给传感器通电")
    return {"interface": interface, "ipv4": ipv4, "lidar_ip": "192.168.123.120", "hardware_power_control": False}


def start_service(role, project, interface, python):
    project = Path(project)
    log_dir, pid_dir = project / "run/logs", project / "run/pids"
    log_dir.mkdir(parents=True, exist_ok=True)
    pid_dir.mkdir(parents=True, exist_ok=True)
    if role == "navigation":
        command, cwd = ["roslaunch", "fastlio", "navigation.launch", "rviz:=false"], project / "G1Nav2D"
    elif role == "safe_controller":
        command = [python, "-u", str(project / "unitree_sdk2_python/example/g1/high_level/g1_control_safe.py"), interface]
        cwd = project / "unitree_sdk2_python/example/g1/high_level"
    else:
        raise ValueError("不能启动未授权的服务")
    check_cancel()
    with (log_dir / ("init_" + role + ".log")).open("ab", buffering=0) as log:
        process = subprocess.Popen(command, cwd=str(cwd), stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    return write_pid(pid_dir / ("init_" + role + ".json"), process, role, project, interface)


def assert_idle(state):
    if state.get("active_navigation") is True:
        raise RuntimeError("检测到 move_base 有活动导航目标，请操作员先停止任务；初始化不会取消其他目标")
    if state.get("navigation_idle_known") is not True:
        raise RuntimeError("无法确认现有导航处于空闲状态；请检查 move_base 状态后重试")


def verify_controller_pid(state, records):
    if len(records) != 1 or state.get("controller_pid") != records[0]["pid"]:
        raise RuntimeError("ROS 安全控制节点 PID 与已核实本机进程不一致，拒绝调用运动服务")
    return records[0]["pid"]


def initialize(project=PROJECT, report=None):
    project = Path(project)
    deadline = time.monotonic() + 53
    interface = os.environ.get("CONTROL_IFACE", "eth0")
    python = os.environ.get("CONTROL_PYTHON", str(Path.home() / "robot_dev/envs/unitree-core/bin/python"))
    report = report or (lambda stage, state, message, details=None: emit({"event": "stage", "id": stage,
        "state": state, "message": message, "details": details or {}}))

    def phase(stage, message, action):
        check_cancel()
        if time.monotonic() >= deadline:
            raise TimeoutError("初始化总检查超过 53 秒")
        report(stage, "running", message)
        try:
            result = action()
        except Exception as exc:
            report(stage, "failed", str(exc))
            raise
        report(stage, "succeeded", message + "：完成", result)
        return result

    setup = Path(os.environ.get("ROS_SETUP", "/opt/ros/noetic/setup.bash"))
    if not setup.is_file():
        raise RuntimeError("ROS Noetic 环境不存在，请先完成软件部署")
    master_host = urlparse(os.environ.get("ROS_MASTER_URI", "http://localhost:11311")).hostname
    if master_host not in ("localhost", "127.0.0.1", "::1", os.uname().nodename):
        raise RuntimeError("初始化只允许本机 ROS Master，请核验 ROS_MASTER_URI")
    assets = phase("assets", "检查已部署地图、导览点和展板资料（不覆盖文件）", lambda: verify_files(project))
    network = phase("network", "检查机器人内部网卡和雷达连接（仅检查，不给硬件通电）", lambda: check_network(interface))
    services = find_services(project, interface)
    validate_inventory(services)
    if not Path(python).is_absolute() or not os.access(python, os.X_OK):
        raise RuntimeError("CONTROL_PYTHON 必须是已部署、可执行的 Python 绝对路径")
    controller_file = project / "unitree_sdk2_python/example/g1/high_level/g1_control_safe.py"
    if not controller_file.is_file():
        raise RuntimeError("缺少已部署的 g1_control_safe.py，拒绝使用旧控制器")
    initial = probe("master", 4)
    assert_idle(initial)
    from guide_mapping import session_running
    if session_running(project) or '/map_builder_node' in initial.get('nodes', []):
        raise RuntimeError('建图会话仍在运行，请先保存/取消并等待退出，不启动导航')
    if services["safe_controller"]:
        if "/unitree_safe_controller" not in initial.get("nodes", []):
            raise RuntimeError("已有安全控制器进程未接入当前 ROS Master，不重复启动")
        verified_pid = verify_controller_pid(initial, services["safe_controller"])
        phase("controller", "确认已有安全控制器并禁用自动运动", lambda: probe("disable", 4, controller_pid=verified_pid))
    existing = REQUIRED_NODES.intersection(initial.get("nodes", []))
    if existing and existing != REQUIRED_NODES:
        raise RuntimeError("导航服务只启动了一部分，缺少 " + ", ".join(sorted(REQUIRED_NODES - existing)) + "；不会重启或覆盖已有服务")

    def navigation():
        reused = existing == REQUIRED_NODES
        launched_here = False
        if not reused and not services["navigation"]:
            current = find_services(project, interface)
            validate_inventory(current)
            if current["navigation"]:
                services["navigation"] = current["navigation"]
            else:
                services["navigation"] = [start_service("navigation", project, interface, python)]
                launched_here = True
        ready_deadline = min(deadline - 15, time.monotonic() + 20)
        while time.monotonic() < ready_deadline:
            check_cancel()
            current = probe("master", 4)
            # A newly launched action server may register its goal subscriber
            # before publishing the first status heartbeat. Wait for a known
            # idle state only for the launch owned by this invocation; never
            # ignore an active goal or change pre-existing unknown services.
            if current.get("active_navigation") is True or not launched_here:
                assert_idle(current)
            if current.get("navigation_idle_known") is True and REQUIRED_NODES.issubset(current.get("nodes", [])):
                return {"reused": reused, "ready_nodes": sorted(REQUIRED_NODES), "rviz_started": False}
            time.sleep(0.15)
        raise RuntimeError("导航服务启动未就绪，请检查 run/logs/init_navigation.log；未重启任何已有服务")
    navigation_result = phase("navigation", "检查或启动 ROS 雷达驱动、定位和导航（RViz 关闭，不自动重定位）", navigation)

    def controller():
        current = find_services(project, interface)
        validate_inventory(current)
        state = probe("master", 4)
        assert_idle(state)
        if "/unitree_safe_controller" in state.get("nodes", []) and not current["safe_controller"]:
            raise RuntimeError("安全控制节点存在但无法核实本机进程归属，拒绝重复启动")
        reused = bool(current["safe_controller"])
        expected_records = current["safe_controller"]
        if not reused:
            run_finite([python, "-c", "import rospy; from unitree_sdk2py.g1.loco.g1_loco_client import LocoClient"], 3)
            services["safe_controller"] = [start_service("safe_controller", project, interface, python)]
            expected_records = services["safe_controller"]
        ready_deadline = min(deadline - 9, time.monotonic() + 6)
        while time.monotonic() < ready_deadline:
            check_cancel()
            state = probe("master", 4)
            assert_idle(state)
            if "/unitree_safe_controller" in state.get("nodes", []) and state.get("controller_ready") is True:
                verified_pid = verify_controller_pid(state, expected_records)
                disabled = probe("disable", 4, controller_pid=verified_pid)
                return {"reused": reused, "motion_disabled": disabled.get("motion_disabled") is True}
            time.sleep(0.15)
        raise RuntimeError("安全控制器未就绪，请检查 run/logs/init_safe_controller.log")
    controller_result = phase("controller", "检查或启动安全控制器，并确认自动运动禁用", controller)
    if controller_result.get("motion_disabled") is not True:
        raise RuntimeError("安全控制器没有确认禁用自动运动")
    sensors = phase("sensors", "读取新鲜点云、IMU、激光扫描和里程计", lambda: probe("sensors", min(7, max(1, deadline - time.monotonic()))))
    localization = phase("localization", "读取定位状态；未定位时等待人工地图初值，不猜测当前位置", lambda: probe("localization", min(7, max(1, deadline - time.monotonic()))))
    if localization.get("localized") is True:
        pose = validate_samples(localization.get("samples", []), time.time())
        if localization.get("map_fingerprint") != assets["map_fingerprint"]:
            raise RuntimeError("读取位姿期间地图版本发生变化，请重新检查")
    else:
        pose = None
    final_idle = probe("master", min(3, max(0.1, deadline - time.monotonic())))
    assert_idle(final_idle)
    result = {"status": "success", "localized": localization.get("localized") is True,
        "needs_initial_pose": localization.get("localized") is not True, "motion_disabled": True,
        "frame_id": "map", "map_fingerprint": assets["map_fingerprint"],
        "services": {"navigation": navigation_result, "controller": controller_result, "sensors": sensors, "network": network},
        "automatic_motion_enabled": False}
    if pose is not None:
        result["pose"] = pose
    return result


def controller_node_pid(master):
    from xmlrpc.client import ServerProxy
    code, message, pid = ServerProxy(master.lookupNode("/unitree_safe_controller")).getPid("/daolan_init_probe")
    if code != 1 or type(pid) is not int or pid <= 0:
        raise RuntimeError("无法核验 ROS 安全控制节点的实际 PID")
    return pid


def ros_probe(mode, expected_controller_pid=None):
    import rosgraph
    import rospy
    import rosservice
    master = rosgraph.Master("/daolan_init_probe")
    if mode == "master":
        try:
            publishers, subscribers, services = master.getSystemState()
        except Exception:
            return {"status": "success", "master_ready": False, "nodes": [],
                    "active_navigation": False, "navigation_idle_known": True}
        nodes = sorted({node for entries in (publishers, subscribers, services) for _, owners in entries for node in owners})
        status_published = any(topic == "/move_base/status" and owners for topic, owners in publishers)
        goal_subscribed = any(topic == "/move_base/goal" and owners for topic, owners in subscribers)
        result = {"status": "success", "master_ready": True, "nodes": nodes,
                  "active_navigation": False, "navigation_idle_known": not status_published and not goal_subscribed,
                  "controller_ready": dict(services).get("/unitree_motion_enable", []) == ["/unitree_safe_controller"]}
        if "/unitree_safe_controller" in nodes:
            result["controller_pid"] = controller_node_pid(master)
        if not status_published:
            return result
        from actionlib_msgs.msg import GoalStatusArray
        rospy.init_node("daolan_init_read_status", anonymous=True, disable_signals=True)
        if rospy.get_param("/use_sim_time", False):
            raise RuntimeError("实机初始化不能使用仿真时间")
        try:
            message = rospy.wait_for_message("/move_base/status", GoalStatusArray, timeout=1.5)
        except rospy.ROSException:
            # A registered but starting action server is unknown, not idle.
            # initialize() only retries this for its own fresh launch.
            result["navigation_idle_known"] = False
            return result
        age = time.time() - message.header.stamp.to_sec()
        result["navigation_idle_known"] = -0.1 <= age <= 1.5
        result["active_navigation"] = any(status.status in ACTIVE_STATES for status in message.status_list)
        return result
    rospy.init_node("daolan_init_" + mode, anonymous=True, disable_signals=True)
    if rospy.get_param("/use_sim_time", False):
        raise RuntimeError("实机初始化不能使用仿真时间")
    if mode == "disable":
        from std_srvs.srv import SetBool
        owners = dict(master.getSystemState()[2]).get("/unitree_motion_enable", [])
        if owners != ["/unitree_safe_controller"]:
            raise RuntimeError("运动使能服务并非唯一的已核实安全控制器，拒绝调用")
        if type(expected_controller_pid) is not int or controller_node_pid(master) != expected_controller_pid:
            raise RuntimeError("调用前安全控制节点 PID 校验失败，未调用运动服务")
        # Recheck navigation before this only allowed mutation: disabling motion.
        from actionlib_msgs.msg import GoalStatusArray
        if any(topic == "/move_base/status" and owners for topic, owners in master.getSystemState()[0]):
            status = rospy.wait_for_message("/move_base/status", GoalStatusArray, timeout=1.5)
            age = time.time() - status.header.stamp.to_sec()
            if not math.isfinite(age) or not -0.1 <= age <= 1.5:
                raise RuntimeError("导航状态已过期，不能安全初始化")
            if any(item.status in ACTIVE_STATES for item in status.status_list):
                raise RuntimeError("导航目标刚刚变为活动，初始化不会打断其他任务")
        rospy.wait_for_service("/unitree_motion_enable", timeout=1.5)
        response = rospy.ServiceProxy("/unitree_motion_enable", SetBool)(data=False)
        if not response.success:
            raise RuntimeError("安全控制器拒绝禁用自动运动")
        return {"status": "success", "motion_disabled": True, "sdk_stop_ack_verified": False}
    if mode == "sensors":
        import rostopic
        from navigation_readiness import navigation_tf_limit, validate_navigation_odom
        navigation_age = navigation_tf_limit(
            rospy.get_param("/move_base/global_costmap/transform_tolerance", None),
            rospy.get_param("/move_base/local_costmap/transform_tolerance", None))
        values = {}
        for topic in ("/livox/lidar", "/livox/imu", "/scan", "/slam_odom", "/navigation_odom"):
            message_class, resolved, _ = rostopic.get_topic_class(topic, blocking=False)
            if message_class is None or resolved != topic:
                raise RuntimeError("传感器话题未就绪：" + topic)
            message = rospy.wait_for_message(topic, message_class, timeout=1)
            if topic == "/navigation_odom":
                validate_navigation_odom(message, time.time(), navigation_age)
            age = time.time() - message.header.stamp.to_sec()
            if not math.isfinite(age) or not -0.1 <= age <= 1.5:
                raise RuntimeError("传感器消息已过期或时钟异常：" + topic)
            values[topic] = {"fresh": True, "age_seconds": max(0, age), "frame_id": message.header.frame_id}
        return {"status": "success", "topics": values}
    if mode == "localization":
        rospy.wait_for_service("/slam_reloc_check", timeout=1.5)
        service_class = rosservice.get_service_class_by_name("/slam_reloc_check")
        if service_class is None:
            raise RuntimeError("无法读取重定位检查服务类型")
        localized = bool(rospy.ServiceProxy("/slam_reloc_check", service_class)(code=True).status)
        if not localized:
            return {"status": "success", "localized": False, "needs_initial_pose": True}
        from capture_guide_pose import capture
        return capture(init_ros=False)
    raise ValueError("未知初始化检查模式")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", choices=("master", "disable", "sensors", "localization"))
    parser.add_argument("--controller-pid", type=int)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: CANCEL.set())
    signal.signal(signal.SIGINT, lambda *_: CANCEL.set())
    try:
        emit(ros_probe(args.probe, args.controller_pid) if args.probe else initialize())
        code = 0
    except Exception as exc:
        emit({"status": "error", "message": str(exc)})
        code = 1
    if args.probe:
        # No cleanup in a rospy atexit hook may hold the web initialization open.
        # These finite probe children own no persistent service or goal.
        os._exit(code)
    return code


if __name__ == "__main__":
    sys.exit(main())
