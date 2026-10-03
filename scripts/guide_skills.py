#!/usr/bin/env python3
"""Small, fixed-contract adapters for the verified DaoLan robot capabilities.

The web server and Omni may only call the functions listed in CATALOG.  Motion
is proposed first; an operator must confirm it through the web endpoint.
"""

import json
import os
import shlex
import subprocess
import urllib.request
from pathlib import Path

from guide_actions import WIRE_PLAN_SCHEMA, action_catalog, validate_plan


PROJECT = Path(__file__).resolve().parent.parent
ROUTE_SCRIPT = PROJECT / "scripts" / "route_demo.sh"
STOP_SCRIPT = PROJECT / "scripts" / "guide_stop_motion.sh"
ROUTE = PROJECT / "routes" / "demo_route.txt"
MAP = PROJECT / "map" / "map.yaml"
PCD = PROJECT / "G1Nav2D" / "src" / "fastlio2" / "PCD" / "map.pcd"


CATALOG = {
    "list_guide_points": {
        "description": "只读查询已登记导览点的ID、名称、位姿和先验资料状态，导航必须用查询得到的point_id。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "get_point_knowledge": {
        "description": "读取某个导览点的先验、资料来源和候选信息。现有摘要不是固定讲解脚本，讲解用present_point到达后生成。",
        "parameters": {"type": "object", "additionalProperties": False,
            "properties": {"point_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"}}, "required": ["point_id"]},
    },
    "get_observation_policy": {
        "description": "查询摄像头补充观察与受限位姿调整是否已验收，未启用时不得发起观测移动。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "get_action_catalog": {
        "description": "查询标准动作、参数、完成条件和手臂动作验证状态。只有 verified 为 true 的手臂动作可实际执行。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "get_task_status": {
        "description": "查询最新动作任务的执行进度、结果或失败原因。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "request_action_plan": {
        "description": "生成待确认顺序计划，不执行。必须传 title 和 steps_json 两个字符串；steps_json 的内容是完整步骤数组，每步含 action 和 parameters，可选 timeout。speak 参数 text；navigate_route 参数 destination=start/end；wait 参数 seconds；arm_gesture 参数 gesture 和 hold_seconds；check_status 参数 require_localized。必须一次给齐全部步骤，不能只传标题。",
        "parameters": WIRE_PLAN_SCHEMA,
    },
    "get_robot_status": {
        "description": "查询机器人定位、导航和安全控制器状态。只读取，不移动机器人。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "get_guide_assets": {
        "description": "查询已验证的二维地图、三维点云和导览路线是否存在。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "request_route": {
        "description": "请求沿已录制路线去终点或回起点。返回待确认请求，不会立即移动。",
        "parameters": {
            "type": "object",
            "properties": {"destination": {"type": "string", "enum": ["start", "end"]}},
            "required": ["destination"],
            "additionalProperties": False,
        },
    },
    "stop_navigation": {
        "description": "立即停止导航，取消目标并禁用机器人运动。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}


def omni_tools():
    return [
        {"type": "function", "function": {"name": name, **definition}}
        for name, definition in CATALOG.items()
    ]


def _ros(command, timeout=5):
    setup = "source /opt/ros/noetic/setup.bash && source " + shlex.quote(str(
        PROJECT / "G1Nav2D" / "devel" / "setup.bash"
    ))
    try:
        result = subprocess.run(
            ["bash", "-lc", setup + " && " + command],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=timeout, check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def robot_status():
    nodes = set(_ros("rosnode list", timeout=4).splitlines())
    reloc = _ros('rosservice call /slam_reloc_check "code: true"', timeout=4)
    return {
        "localizer": "/localizer_node" in nodes,
        "planner": "/move_base" in nodes,
        "safe_controller": "/unitree_safe_controller" in nodes,
        "localized": "status: True" in reloc,
        "route_running": route_running(),
    }


def guide_assets():
    return {
        "map_2d": MAP.is_file(),
        "map_3d": PCD.is_file(),
        "route": ROUTE.is_file(),
    }


def route_running():
    pid_file = PROJECT / "run" / "pids" / "guide_route.pid"
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, OSError):
        return False


def stop_navigation():
    result = subprocess.run(
        ["bash", str(STOP_SCRIPT)], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, timeout=18, check=False,
    )
    return {"stopped": result.returncode == 0, "message": result.stdout.strip() or result.stderr.strip()}


def execute(name, arguments):
    if name not in CATALOG:
        return {"error": "unknown skill"}
    if not isinstance(arguments, dict):
        return {"error": "arguments must be an object"}
    if name in ("list_guide_points", "get_observation_policy"):
        if arguments:
            return {"error": "unexpected arguments"}
        if name == "list_guide_points":
            from guide_knowledge import KnowledgeStore
            return {"points": KnowledgeStore(project=PROJECT).list_points(), "read_only": True}
        from guide_conception import OBSERVATION_POLICY
        return {**OBSERVATION_POLICY, "read_only": True}
    if name == "get_point_knowledge":
        if set(arguments) != {"point_id"}:
            return {"error": "only point_id is accepted"}
        from guide_knowledge import KnowledgeStore
        return {"knowledge": KnowledgeStore(project=PROJECT).get(arguments["point_id"]), "read_only": True}
    if name == "get_action_catalog":
        return action_catalog() if not arguments else {"error": "unexpected arguments"}
    if name == "get_task_status":
        if arguments:
            return {"error": "unexpected arguments"}
        path = PROJECT / "run" / "tasks" / "current.json"
        return json.loads(path.read_text()) if path.exists() else {"state": "idle"}
    if name == "request_action_plan":
        if "steps_json" in arguments:
            if set(arguments) != {"title", "steps_json"}:
                raise ValueError("计划工具只接收 title 和 steps_json")
            serialized = arguments["steps_json"]
            if not isinstance(serialized, str) or not 1 <= len(serialized) <= 20000:
                raise ValueError("steps_json 须为 1–20000 字符的 JSON 数组字符串")
            arguments = {"title": arguments["title"], "steps": json.loads(serialized)}
        return {"confirmation_required": True, "plan": validate_plan(arguments)}
    if name == "get_robot_status":
        return robot_status() if not arguments else {"error": "unexpected arguments"}
    if name == "get_guide_assets":
        return guide_assets() if not arguments else {"error": "unexpected arguments"}
    if name == "stop_navigation":
        if arguments:
            return {"error": "unexpected arguments"}
        gateway = os.environ.get("GUIDE_GATEWAY_URL")
        if gateway:
            # Delegate to the owning process: it cancels the full sequence
            # before stopping ROS. Clearing only move_base would leave later
            # task steps able to start another route.
            request = urllib.request.Request(gateway + "/api/stop", data=b"{}",
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=25) as response:
                return json.loads(response.read())
        return stop_navigation()
    if set(arguments) != {"destination"} or arguments["destination"] not in ("start", "end"):
        return {"error": "destination must be start or end"}
    return {"confirmation_required": True, "destination": arguments["destination"]}


if __name__ == "__main__":
    print(json.dumps({"skills": CATALOG, "assets": guide_assets()}, ensure_ascii=False))
