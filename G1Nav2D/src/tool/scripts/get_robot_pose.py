#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
获取机器人在世界坐标系（map）中的位置和角度

使用方法：
    rosrun tool get_robot_pose.py
    python3 get_robot_pose.py
    
    # 输出JSON格式（方便其他进程解析）
    python3 get_robot_pose.py --json
"""

import rospy
import tf
import tf.transformations as tft
import math
import sys
import json


class RobotPoseGetter:
    """获取机器人在世界坐标系中的位置和角度"""
    
    def __init__(self):
        rospy.init_node('get_robot_pose', anonymous=True)
        
        # TF监听器
        self.tf_listener = tf.TransformListener()
        
        # 坐标系名称（从参数服务器获取，或使用默认值）
        self.map_frame = rospy.get_param('~map_frame', 'map')
        self.body_frame = rospy.get_param('~body_frame', 'body')
        self.base_link_frame = rospy.get_param('~base_link_frame', 'base_link')
    
    def get_current_pose(self, timeout=5.0):
        """
        获取当前机器人在map坐标系中的位置和角度
        
        参数:
            timeout: 超时时间（秒）
        
        返回:
            dict: 包含位置和角度的字典，失败返回None
                {
                    'position': {'x': float, 'y': float, 'z': float},
                    'orientation': {'roll': float, 'pitch': float, 'yaw': float},
                    'orientation_degrees': {'roll': float, 'pitch': float, 'yaw': float},
                    'quaternion': {'x': float, 'y': float, 'z': float, 'w': float}
                }
        """
        start_time = rospy.Time.now()
        while (rospy.Time.now() - start_time).to_sec() < timeout:
            try:
                # 获取最新的变换
                (trans, rot) = self.tf_listener.lookupTransform(
                    self.map_frame,
                    self.body_frame,
                    rospy.Time(0)
                )
                
                # 四元数转欧拉角
                (roll, pitch, yaw) = tft.euler_from_quaternion(rot)
                
                return {
                    'position': {
                        'x': float(trans[0]),
                        'y': float(trans[1]),
                        'z': float(trans[2])
                    },
                    'orientation': {
                        'roll': float(roll),
                        'pitch': float(pitch),
                        'yaw': float(yaw)
                    },
                    'orientation_degrees': {
                        'roll': float(math.degrees(roll)),
                        'pitch': float(math.degrees(pitch)),
                        'yaw': float(math.degrees(yaw))
                    },
                    'quaternion': {
                        'x': float(rot[0]),
                        'y': float(rot[1]),
                        'z': float(rot[2]),
                        'w': float(rot[3])
                    }
                }
            except (tf.LookupException, tf.ConnectivityException, tf.ExtrapolationException) as e:
                rospy.sleep(0.1)  # 等待TF树建立
                continue
        
        rospy.logerr("获取位姿失败: 超时或TF树未建立")
        return None


def main():
    """主函数"""
    # 检查是否输出JSON格式
    output_json = '--json' in sys.argv
    
    try:
        getter = RobotPoseGetter()
        
        # 等待TF树建立
        rospy.sleep(1.0)
        
        # 获取一次当前位姿
        pose = getter.get_current_pose(timeout=5.0)
        
        if pose:
            if output_json:
                # JSON格式输出（方便其他进程解析）
                result = {
                    'success': True,
                    'x': pose['position']['x'],
                    'y': pose['position']['y'],
                    'z': pose['position']['z'],
                    'roll': pose['orientation_degrees']['roll'],
                    'pitch': pose['orientation_degrees']['pitch'],
                    'yaw': pose['orientation_degrees']['yaw'],
                    'roll_rad': pose['orientation']['roll'],
                    'pitch_rad': pose['orientation']['pitch'],
                    'yaw_rad': pose['orientation']['yaw']
                }
                print(json.dumps(result, indent=2))
                sys.exit(0)
            else:
                # 人类可读格式输出
                print("\n=== 当前机器人位姿 ===")
                print(f"位置: x={pose['position']['x']:.3f}, y={pose['position']['y']:.3f}, z={pose['position']['z']:.3f}")
                print(f"角度: roll={pose['orientation_degrees']['roll']:.2f}°, "
                      f"pitch={pose['orientation_degrees']['pitch']:.2f}°, "
                      f"yaw={pose['orientation_degrees']['yaw']:.2f}°")
                print("====================\n")
                sys.exit(0)
        else:
            if output_json:
                result = {'success': False, 'error': '获取位姿失败'}
                print(json.dumps(result))
            else:
                print("错误: 无法获取机器人位姿")
            sys.exit(1)
        
    except rospy.ROSInterruptException:
        if output_json:
            result = {'success': False, 'error': 'ROS中断'}
            print(json.dumps(result))
        sys.exit(1)
    except Exception as e:
        if output_json:
            result = {'success': False, 'error': str(e)}
            print(json.dumps(result))
        else:
            print(f"错误: {str(e)}")
        sys.exit(1)


if __name__ == '__main__':
    main()

