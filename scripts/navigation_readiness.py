"""Pure fail-closed navigation freshness policy (no ROS or SDK side effects)."""

import math


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def navigation_tf_limit(global_tolerance, local_tolerance):
    for value in (global_tolerance, local_tolerance):
        if not number(value) or value <= 0:
            raise RuntimeError("代价地图 TF 容差配置无效；拒绝导航，不使用宽松默认值")
    return min(1.5, global_tolerance, local_tolerance)


def validate_tf_age(age, maximum):
    if not number(maximum) or not 0 < maximum <= 1.5:
        raise RuntimeError("导航 TF 新鲜性配置无效")
    if not number(age) or not -0.1 <= age <= maximum:
        actual = "无效" if not number(age) else "{:.3f} 秒".format(age)
        raise RuntimeError("导航 TF 已过期或时钟异常（时间差 {}，代价地图上限 {:.3f} 秒）；"
                           "运动保持禁用或请求停止".format(actual, maximum))


def validate_navigation_odom(message, now, maximum):
    if message is None:
        raise RuntimeError("未收到 /navigation_odom 实际速度反馈；拒绝导航")
    try:
        stamp = message.header.stamp.to_sec()
        frame, child = message.header.frame_id, message.child_frame_id
        twist = message.twist.twist
        values = [getattr(vector, axis) for vector in (twist.linear, twist.angular)
                  for axis in ("x", "y", "z")]
        position, rotation = message.pose.pose.position, message.pose.pose.orientation
        pose_values = [getattr(position, axis) for axis in ("x", "y", "z")]
        quaternion = [getattr(rotation, axis) for axis in ("x", "y", "z", "w")]
    except (AttributeError, TypeError, ValueError):
        raise RuntimeError("导航速度反馈消息格式无效")
    if not number(now) or not number(stamp) or stamp <= 0:
        raise RuntimeError("导航速度反馈时间戳无效")
    if frame != "local" or child != "base_link" or not all(
            number(value) for value in values + pose_values + quaternion) or abs(
                sum(value * value for value in quaternion) - 1.) > .01:
        raise RuntimeError("导航速度反馈坐标系或数值无效；拒绝使用 body 帧或指令替代实速")
    try:
        validate_tf_age(now - stamp, maximum)
    except (TypeError, RuntimeError) as exc:
        raise RuntimeError("导航速度反馈已过期或时钟异常：" + str(exc))
    return message
