#!/usr/bin/env python3
"""Versioned, bounded contracts for DaoLan action plans (no hardware calls)."""

import math
import os
import re


ARM_ACTIONS = {
    "wave": {"sdk_name": "high wave", "label": "挥手"},
    "handshake": {"sdk_name": "shake hand", "label": "握手"},
    "clap": {"sdk_name": "clap", "label": "拍手"},
    "heart": {"sdk_name": "heart", "label": "比心"},
    "raise_hand": {"sdk_name": "right hand up", "label": "举右手"},
}

ACTION_CATALOG = {
    "check_status": {"label": "检查系统", "motion": False, "default_timeout": 12,
        "completion": "fresh ROS node and localization service checks"},
    "speak": {"label": "播报", "motion": False, "default_timeout": 65,
        "completion": "SDK accepted + estimated playback wait (no end feedback)"},
    "navigate_route": {"label": "沿已录制路线导航", "motion": True, "default_timeout": 240,
        "completion": "all move_base goals SUCCEEDED and route process exited 0"},
    "navigate_to_point": {"label": "自主导航至导览点", "motion": True, "default_timeout": 240,
        "completion": "owned move_base goal SUCCEEDED and fresh point arrival verification"},
    "verify_arrival": {"label": "核对导览点到达状态", "motion": False, "default_timeout": 20,
        "completion": "fresh localized pose within point position/heading bounds and stationary"},
    "present_point": {"label": "基于点位先验动态讲解", "motion": False, "default_timeout": 240,
        "completion": "fresh arrival verified before/after conception; accepted speech segments with playback wait"},
    "wait": {"label": "等待", "motion": False, "default_timeout": 35,
        "completion": "interruptible monotonic timer"},
    "arm_gesture": {"label": "手臂预设动作", "motion": True, "default_timeout": 40,
        "completion": "SDK accepted + timed hold + release-arm accepted (no end feedback)"},
    "stop": {"label": "停止运动", "motion": False, "default_timeout": 18,
        "completion": "safe controller acknowledged emergency stop or disable"},
}

STEP_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": list(ACTION_CATALOG)},
        "parameters": {"type": "object", "additionalProperties": False,
            "properties": {
                "require_localized": {"type": "boolean"},
                "text": {"type": "string", "maxLength": 150},
                "destination": {"type": "string", "enum": ["start", "end"]},
                "point_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
                "topic": {"type": "string", "maxLength": 200},
                "seconds": {"type": "number", "minimum": 0, "maximum": 30},
                "gesture": {"type": "string", "enum": list(ARM_ACTIONS)},
                "hold_seconds": {"type": "number", "minimum": 1, "maximum": 5},
            }},
        "timeout": {"type": "number", "minimum": 1, "maximum": 300},
    },
    "required": ["action", "parameters"],
}

PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "title": {"type": "string", "maxLength": 80},
        "steps": {"type": "array", "minItems": 1, "maxItems": 10, "items": STEP_SCHEMA},
    },
    "required": ["title", "steps"],
}

# Keep the realtime tool's transport shallow. The inner action contract is
# still the same structured, strictly validated plan; JSON is data, not code.
WIRE_PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "title": {"type": "string", "maxLength": 80},
        "steps_json": {"type": "string", "maxLength": 20000,
            "description": '完整步骤数组的 JSON 字符串，例如 [{"action":"speak","parameters":{"text":"欢迎。"}},{"action":"navigate_route","parameters":{"destination":"end"}}]。不可省略。'},
    },
    "required": ["title", "steps_json"],
}


def number(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(name + " 必须是数字")
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError("{} 必须在 {}–{} 之间".format(name, low, high))
    return value


def validate_plan(raw):
    if not isinstance(raw, dict) or set(raw) - {"title", "steps", "version"}:
        raise ValueError("任务必须包含 title 和 steps，不能含未知字段")
    if type(raw.get("version", 1)) is not int or raw.get("version", 1) != 1:
        raise ValueError("只支持动作契约版本 1")
    title = raw.get("title", "")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 80:
        raise ValueError("任务标题须为 1–80 字")
    steps = raw.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 10:
        raise ValueError("任务须包含 1–10 步")
    normalized = []
    total = 0.0
    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict) or set(step) - {"action", "parameters", "timeout"}:
            raise ValueError("第 {} 步格式错误".format(index))
        action = step.get("action")
        if not isinstance(action, str) or action not in ACTION_CATALOG:
            raise ValueError("第 {} 步动作不在白名单".format(index))
        args = step.get("parameters", {})
        if not isinstance(args, dict):
            raise ValueError("动作参数必须是对象")
        args = dict(args)
        allowed = {
            "check_status": {"require_localized"}, "speak": {"text"},
            "navigate_route": {"destination"}, "wait": {"seconds"},
            "navigate_to_point": {"point_id"}, "verify_arrival": {"point_id"},
            "present_point": {"point_id", "topic"},
            "arm_gesture": {"gesture", "hold_seconds"}, "stop": set(),
        }[action]
        if set(args) - allowed:
            raise ValueError("{} 包含未知参数".format(action))
        if action == "check_status":
            args.setdefault("require_localized", False)
            if not isinstance(args["require_localized"], bool):
                raise ValueError("require_localized 必须是布尔值")
        elif action == "speak":
            text = args.get("text")
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= 150:
                raise ValueError("每步播报必须为 1–150 字")
            args["text"] = text.strip()
        elif action == "navigate_route":
            if args.get("destination") not in ("start", "end"):
                raise ValueError("路线方向只能是 start/end")
        elif action in ("navigate_to_point", "verify_arrival", "present_point"):
            if not isinstance(args.get("point_id"), str) or not re.fullmatch(r"[0-9a-f]{32}", args["point_id"]):
                raise ValueError("point_id 必须是已登记点位的 32 位小写十六进制 ID")
            if action == "present_point":
                topic = args.get("topic", "")
                if not isinstance(topic, str) or len(topic) > 200:
                    raise ValueError("讲解主题须为不超过 200 字的字符串")
                args["topic"] = topic.strip()
        elif action == "wait":
            args["seconds"] = number(args.get("seconds"), "等待秒数", 0, 30)
        elif action == "arm_gesture":
            if not isinstance(args.get("gesture"), str) or args.get("gesture") not in ARM_ACTIONS:
                raise ValueError("手臂动作不在白名单")
            args["hold_seconds"] = number(args.get("hold_seconds", 3), "动作保持秒数", 1, 5)
        timeout = number(step.get("timeout", ACTION_CATALOG[action]["default_timeout"]), "timeout", 1, 300)
        minimum = {"speak": len(args.get("text", "")) * 0.3 + 12,
            "wait": args.get("seconds", 0) + 1,
            "arm_gesture": args.get("hold_seconds", 0) + 8, "stop": 16}.get(action, 1)
        if timeout < minimum:
            raise ValueError("{} 的 timeout 太短".format(action))
        total += timeout
        normalized.append({"action": action, "parameters": args, "timeout": timeout})
    if total > 900:
        raise ValueError("任务超时预算总和不得超过 900 秒")
    return {"version": 1, "title": title.strip(), "steps": normalized}


def needs_motion(plan):
    return any(ACTION_CATALOG[s["action"]]["motion"] for s in plan["steps"])


def verified_arms():
    return {value.strip() for value in os.environ.get("GUIDE_VERIFIED_ARM_ACTIONS", "").split(",")} & set(ARM_ACTIONS)


def action_catalog():
    verified = verified_arms()
    fields = {
        "check_status": ["require_localized"], "speak": ["text"],
        "navigate_route": ["destination"], "wait": ["seconds"],
        "navigate_to_point": ["point_id"], "verify_arrival": ["point_id"],
        "present_point": ["point_id", "topic"],
        "arm_gesture": ["gesture", "hold_seconds"], "stop": [],
    }
    required = {"speak": ["text"], "navigate_route": ["destination"],
        "navigate_to_point": ["point_id"], "verify_arrival": ["point_id"], "present_point": ["point_id"],
        "wait": ["seconds"], "arm_gesture": ["gesture"]}
    actions = {}
    for name, metadata in ACTION_CATALOG.items():
        actions[name] = {**metadata, "parameters": {"type": "object",
            "additionalProperties": False,
            "properties": {key: STEP_SCHEMA["properties"]["parameters"]["properties"][key]
                for key in fields[name]}, "required": required.get(name, [])}}
    return {
        "version": 1, "actions": actions,
        "named_navigation_verified": os.environ.get("GUIDE_NAMED_NAV_VERIFIED") == "1",
        "arm_enabled": os.environ.get("GUIDE_ARM_ENABLED") == "1",
        "arm_gestures": {key: {**value, "verified": key in verified} for key, value in ARM_ACTIONS.items()},
        "limits": {"max_steps": 10, "max_timeout_budget_seconds": 900,
            "speech_max_characters": 150, "wait_max_seconds": 30},
    }


def check_execution_policy(plan, motion_enabled, arm_enabled, verified):
    if needs_motion(plan) and not motion_enabled:
        raise ValueError("运动开关未启用；可先预览任务")
    for step in plan["steps"]:
        if step["action"] == "navigate_to_point" and os.environ.get("GUIDE_NAMED_NAV_VERIFIED") != "1":
            raise ValueError("点位自主导航尚未实机验证；先预览，并由操作员授权首次测试")
        if step["action"] == "arm_gesture":
            if not arm_enabled or step["parameters"]["gesture"] not in verified:
                raise ValueError("手臂动作尚未实机验证或未启用：" + step["parameters"]["gesture"])
