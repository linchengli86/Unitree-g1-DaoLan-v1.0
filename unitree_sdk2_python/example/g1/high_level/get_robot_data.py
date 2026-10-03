#!/usr/bin/env python3
"""
获取 G1 机器人 rt/slam_info 基础状态信息 (robot_data)
"""

import json
import sys
import time

from unitree_sdk2py.core.channel import ChannelSubscriber, ChannelFactoryInitialize
from unitree_sdk2py.idl.std_msgs.msg.dds_ import String_

SLAM_INFO_TOPIC = "rt/slam_info"


class RobotDataCollector:
    """收集并打印 robot_data 基础状态信息"""

    def __init__(self):
        self.last_robot_data = None

    def _slam_info_handler(self, msg):
        """处理 rt/slam_info 话题中的 robot_data"""
        try:
            json_data = json.loads(msg.data)
        except json.JSONDecodeError as e:
            print(f"\033[33m[slam_info] JSON parse error: {e}\033[0m")
            return

        if json_data.get("errorCode", 0) != 0:
            print(f"\033[33m[slam_info] {json_data.get('info', 'Unknown error')}\033[0m")
            return

        if json_data.get("type") != "robot_data":
            return

        data = json_data.get("data", {})
        self.last_robot_data = {
            "sec": json_data.get("sec"),
            "nanosec": json_data.get("nanosec"),
            "motorTemp": data.get("motorTemp", []),
            "motorError": data.get("motorError", []),
            "batteryAmp": data.get("batteryAmp"),
            "batteryPower": data.get("batteryPower"),
            "batteryTemp": data.get("batteryTemp"),
            "batteryVol": data.get("batteryVol"),
            "sportMode": data.get("sportMode", -1),
            "gaitType": data.get("gaitType", -1),
            "cpuTemp": data.get("cpuTemp"),
            "cpuUsage": data.get("cpuUsage"),
            "cpuMemory": data.get("cpuMemory"),
            "cpuFrequency": data.get("cpuFrequency"),
        }
        self._print_robot_data(self.last_robot_data)

    def _print_robot_data(self, d):
        """打印 robot_data 基础状态信息"""
        motor_temp = d.get("motorTemp", [])
        motor_err = d.get("motorError", [])
        print("\033[36m[robot_data] 基础状态信息:\033[0m")
        print(f"  电机温度(°C): {motor_temp[:8]}..." if len(motor_temp) > 8 else f"  电机温度(°C): {motor_temp}")
        print(f"  电机错误码:   {motor_err[:8]}..." if len(motor_err) > 8 else f"  电机错误码:   {motor_err}")
        print(f"  电池电流(mA): {d.get('batteryAmp')}  电量(%): {d.get('batteryPower')}  "
              f"温度(°C): {d.get('batteryTemp')}  电压(mV): {d.get('batteryVol')}")
        print(f"  运动模式: {d.get('sportMode')}  步态: {d.get('gaitType')}")
        print(f"  CPU温度(°C): {d.get('cpuTemp')}  占用率(%): {d.get('cpuUsage')}  "
              f"内存(%): {d.get('cpuMemory')}  频率(MHz): {d.get('cpuFrequency')}")

    def get_last_robot_data(self):
        """获取最后一次 robot_data 基础状态信息"""
        return self.last_robot_data


def main():
    if len(sys.argv) < 2:
        print(f"Usage: python3 {sys.argv[0]} networkInterface")
        print("Example: python3 get_robot_data.py eth0")
        sys.exit(-1)

    network_interface = sys.argv[1]

    print("***********************  Unitree G1 Get Robot Data ***********************")
    print("Subscribing to rt/slam_info (robot_data: 电机/电池/CPU状态)")
    print("Press Ctrl+C to exit")
    print("**************************************************************************")

    ChannelFactoryInitialize(0, network_interface)

    collector = RobotDataCollector()

    sub_slam_info = ChannelSubscriber(SLAM_INFO_TOPIC, String_)
    sub_slam_info.Init(handler=collector._slam_info_handler, queueLen=10)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nExiting...")
    finally:
        sub_slam_info.Close()


if __name__ == "__main__":
    main()
