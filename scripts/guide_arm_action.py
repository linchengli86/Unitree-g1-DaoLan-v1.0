#!/usr/bin/env python3
"""Execute one explicitly verified preset and restore the arm on exit."""

import argparse
import json
import os
import signal
import time

from guide_actions import ARM_ACTIONS, verified_arms


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("gesture", choices=tuple(ARM_ACTIONS), nargs="?")
    parser.add_argument("--list", action="store_true", help="只查询官方动作列表，不执行动作")
    parser.add_argument("--test", action="store_true", help="人工现场测试入口，会实际执行动作；网页无法使用此开关")
    parser.add_argument("--interface", default=os.environ.get("CONTROL_IFACE", "eth0"))
    parser.add_argument("--hold", type=float, default=3)
    args = parser.parse_args()
    if not args.list and not args.gesture:
        parser.error("请指定 gesture 或 --list")
    if not args.list and not args.test and (os.environ.get("GUIDE_ARM_ENABLED") != "1" or args.gesture not in verified_arms()):
        raise SystemExit("Arm gesture is not enabled and verified")
    if not 1 <= args.hold <= 5:
        raise SystemExit("Hold must be 1–5 seconds")
    from unitree_sdk2py.core.channel import ChannelFactoryInitialize
    from unitree_sdk2py.g1.arm.g1_arm_action_client import G1ArmActionClient, action_map
    def interrupted(_signal, _frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    ChannelFactoryInitialize(0, args.interface)
    client = G1ArmActionClient()
    client.SetTimeout(3.0)
    client.Init()
    if args.list:
        code, actions = client.GetActionList()
        print(json.dumps({"sdk_code": code, "actions": actions}, ensure_ascii=False))
        return
    try:
        code = client.ExecuteAction(action_map[ARM_ACTIONS[args.gesture]["sdk_name"]])
        if code != 0:
            raise RuntimeError("Arm command rejected: {}".format(code))
        time.sleep(args.hold)
    finally:
        code = client.ExecuteAction(action_map["release arm"])
        if code != 0:
            raise RuntimeError("Release arm rejected: {}".format(code))
    print(json.dumps({"status": "success", "completion": "accepted_and_timed_hold"}))


if __name__ == "__main__":
    main()
