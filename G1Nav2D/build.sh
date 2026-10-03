#!/bin/bash
# G1Nav2D 工作空间编译脚本

echo "=== G1Nav2D 工作空间编译 ==="

# 获取脚本所在目录（工作空间根目录）
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "工作空间目录: $SCRIPT_DIR"

# 检查是否已 source ROS 环境
if [ -z "$ROS_DISTRO" ]; then
    echo "警告: 未检测到 ROS 环境，尝试自动 source..."
    if [ -f /opt/ros/noetic/setup.bash ]; then
        source /opt/ros/noetic/setup.bash
        echo "已 source ROS Noetic"
    elif [ -f /opt/ros/melodic/setup.bash ]; then
        source /opt/ros/melodic/setup.bash
        echo "已 source ROS Melodic"
    else
        echo "错误: 未找到 ROS 安装，请手动 source ROS 环境"
        exit 1
    fi
fi

# Livox 的自定义消息头文件被 fastlio 和 tool 直接引用。先生成这些头文件，
# 避免 catkin 并行编译时其他包先于 livox_ros_driver2 开始编译。
echo ""
echo "步骤 1/2：编译 Livox 驱动并生成自定义消息..."
if ! catkin_make -DROS_EDITION=ROS1 --pkg livox_ros_driver2 -j4; then
    echo "Livox 驱动编译失败"
    exit 1
fi

source "$SCRIPT_DIR/devel/setup.bash"

echo ""
echo "步骤 2/2：编译完整工作空间..."
catkin_make -DROS_EDITION=ROS1 -j4

# 检查编译结果
if [ $? -eq 0 ]; then
    echo ""
    echo "=== 编译成功！ ==="
    echo ""
    echo "可执行文件位置:"
    echo "  - localizer_node: $SCRIPT_DIR/devel/lib/fastlio/localizer_node"
    echo "  - map_builder_node: $SCRIPT_DIR/devel/lib/fastlio/map_builder_node"
    echo "  - temp_node: $SCRIPT_DIR/devel/lib/fastlio/temp_node"
    echo ""
    echo "请运行以下命令 source 环境:"
    echo "  source $SCRIPT_DIR/devel/setup.bash"
else
    echo ""
    echo "=== 编译失败！ ==="
    echo "请检查错误信息"
    exit 1
fi
