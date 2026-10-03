# PC2 部署

基线为已使用的 Ubuntu 20.04 / ROS Noetic / aarch64 PC2，不承诺任意系统一键兼容。仓库不携带地图、点位、Key、虚拟环境；先读 [交付说明](REPOSITORY.md)。

## 新入口（候选版）

兼容环境首次执行 `bash scripts/quickstart.sh --prepare`，之后执行 `bash scripts/quickstart.sh`；网页进入 `/setup`，无地图先遥控建图审核激活，有地图跳过。依赖可信 Noetic apt 源、网络和 sudo；不改源、不绕过签名、不在运行导航/控制/建图时编译。保留已有地图和配置，未准备成功不启动任务。

新模板默认 `GUIDE_AUDIO_ENABLED=0`，纯运动不初始化音频；按需另装 Omni（`--prepare --with-omni`）、隐藏配置 Key，再启用音频。PIN/导航权限可在 `/setup` 配置，但实际导航模块验收后才能确认开放，手臂仍关闭。[实现和产物](WEB_ONBOARDING.md)

下文为原现场分步维护方式；新增安装器/C++/网页新图尚未干净 PC2 和新场地实机验收，历史编译成功不代表本候选版验收。

## 连接和目录

```bash
# 操作电脑执行，地址按现场调整
ssh unitree@192.168.3.64
# 有线常用地址：192.168.123.164
hostname
ip -br addr
cd ~/robot/DaoLan
```

换电脑通常是重新建立 SSH 信任/密钥及网络路由，不意味着重新部署机器人。内部网卡当前 `eth0`、MID360 `192.168.123.120`。操作电脑网线与内部雷达/控制连接不是一回事；拔操作电脑网线前确认 Wi-Fi 可用，勿断内部连接。

## 系统与构建

需 ROS Noetic、catkin、编译工具、PCL/OpenCV 等开发依赖及 Livox SDK2。曾核验的额外 apt 依赖如下，不是全新空系统的完整 provisioning：

```bash
sudo apt-get update
sudo apt-get install -y libportaudio2 ros-noetic-navigation ros-noetic-gtsam \
  ros-noetic-tf2-sensor-msgs ros-noetic-teb-local-planner \
  ros-noetic-costmap-converter ros-noetic-octomap-server
```

Noetic 已结束常规支持，安装时确认源与签名；不要关闭签名检查。曾遇 ROS Key 过期，先查实际源文件，不能假定存在 `ros-fish.list`。完整依赖以各包 `package.xml`、`CMakeLists.txt` 和构建日志为准。

Livox SDK2 按 `Livox-SDK2/README.md` 构建安装，系统安装需要 sudo。ROS1 工作空间：

```bash
source /opt/ros/noetic/setup.bash
cd ~/robot/DaoLan/G1Nav2D
bash build.sh
source devel/setup.bash
```

构建先以 `-DROS_EDITION=ROS1` 生成 Livox 消息，再编译全部包（默认 `-j4`）。ROS1 驱动误入 ament 分支时不要盲装 ROS2。曾缺失三个 FastLIO 节点与平滑器源码，当前已经包含；clone 原始上游不能替代项目适配。

## Python 与配置

ROS 辅助程序使用 `/usr/bin/python3`（PC2 Python 3.8），加载 Noetic rospy/tf/消息及 NumPy/YAML。控制/网站使用已含 Unitree SDK/CycloneDDS 的环境，默认 `~/robot_dev/envs/unitree-core/bin/python`。

```bash
cd ~/robot/DaoLan
source scripts/env.sh
printf '%s\n' "$CONTROL_PYTHON" "$CONTROL_IFACE"
bash scripts/omni_setup.sh
python3 scripts/omni_configure.py
```

最后一条在本地隐藏输入 Key，不需 nano。Omni 独立环境是 `run/omni-venv`，依赖见 `scripts/omni-requirements.txt`。私有配置 `run/config/omni.env` 必须权限 600。无 Key 可选点移动和直接 TTS，但不能真实调用 Omni。

新部署默认不开放运动。现场验收后 `python3 scripts/omni_configure.py --motion on` 设置权限/PIN；点位导航另需私有配置的 `GUIDE_NAMED_NAV_VERIFIED=1`，当前配置 CLI 没有该门的参数，不能仅开 motion 就认为可导航。手臂门保持关闭。导出模板所有运动门为 0。

必要私有资产：二维地图与相关 JSON、`fastlio2/PCD/` 三维/地面地图、关键位姿、`config/guide_points.json` 及关联照片/文献。登记点需匹配地图指纹；示教路线不是自主点位导航的必需输入。

## 启动和升级

```bash
cd ~/robot/DaoLan
bash scripts/mobile_guide_start.sh
curl -fsS http://127.0.0.1:8765/api/health
```

再按 [操作手册](OPERATIONS.md) 初始化。启动网站会初始化现有音频客户端，但点位移动不调用 Omni、不排入语音队列。

升级须确认无任务/初始化/重定位，备份更新文件并核实进程归属。只更新对应源码，不用 `rsync --delete`，不覆盖现场 `map/`、`config/`、`run/`、`routes/`、PCD。网页更新仅重载网站，不应重启定位/导航/控制器；历史整套停止脚本不是在线无损重载工具。当前文档整理没有重新部署或重启 PC2 服务。
