# 架构与能力输入输出

## 分层

PC2 是部署和调度中心，网页负责交互，Omni 负责可选的语义理解和内容构思。模型不能直接发布速度或执行 shell。确定性执行器检查白名单、互斥、超时和完成结果；独立点位移动不依赖模型、语音或示教路线。

```text
MID360 点云 / IMU → FastLIO → 定位 / TF / navigation_odom
                                         ↓
二维地图 + 登记目标 + scan → move_base / TEB → cmd_vel
                                               ↓
                         EMA → cmd_vel_smooth → 安全控制器 → Unitree DDS
网页选点 / Omni 计划 → 预览 + token + PIN → TaskExecutor → 安全导航
点位展板 / 摘要 / 文献 → conception → 到达复核 → Unitree TTS
```

## 模块契约

| 模块 / 实现 | 输入 | 输出 / 成功判据 | 产物与边界 |
| --- | --- | --- | --- |
| 建图 `mapping.sh`、FastLIO | 点云/IMU、人工安全带行 | PCD、地面地图、关键位姿；二维地图另保存 | `fastlio2/PCD/`、`path/`、`map/`；不与导航同时运行 |
| 初始化 `guide_initialization.py`、`initialize_robot_services.py` | PIN、现场准备确认、已有资产 | assets/network/controller/navigation/sensors/localization 状态 | `run/pids/`、`run/logs/`；启动软件不是硬件通电，不猜当前位置 |
| 重定位 `web_relocalize.py`、`guide_relocalization.py`、ICP | 地图、点云、人工粗位置/朝向、地图指纹 | 有界候选和独立扫描核验，最终位姿待人工核对 | 粗先验 1 m、±45°；修正量不是精度；失败禁用运动 |
| 地图 `guide_map.py`、`guide_live_map.py` | 固定底图、有效 TF、scan、局部 costmap | 约 2 Hz 只读缓存和实时叠加 | 过期隐藏，不永久重建或优化地图 |
| 点位 `guide_points.py`、`capture_guide_pose.py` | 停稳位姿、名称、展板、人工审核 | 新鲜稳定位姿、点 ID、地图指纹 | `config/guide_points.json` 及资料；采集位置不是未知环境全局定位 |
| 导航 `navigate_to_point_safe.py` | 点 ID、定位、地图、传感器、本目标路径 | 目标成功、停稳、到达核验、禁用运动 | 位置 ≤.20 m、朝向 ≤.20 rad；不接受任意坐标或无限重试 |
| 平滑/控制 `velocity_smoother_ema`、`g1_control_safe.py` | cmd_vel、明确使能、新鲜持续指令 | 限速、限加速度、看门狗停止、官方 DDS 请求 | 当前 vx≤.60 m/s、wz≤.70 rad/s、vy=0；看门狗 .30 s；不切硬件模式 |
| 网站 `guide_web.py`、`mobile_guide_server.py` | 页面操作 / JSON | 独立初始化、导航、地图、资料页面 | 局域网 8765；不暴露 Key/PIN；加载不运动 |
| 调度 `guide_actions.py`、`guide_task_executor.py` | 白名单计划、90 s token、PIN | 逐步状态、失败跳过后续、停止 | `run/tasks/`；重启不续跑；导航页没有 speak/present_point |
| Omni `omni_client.py`、`guide_skills.py` | 文字、JPEG、PCM16 录音 | 回答或待确认计划 | 工具循环有预算；Key 仅在 PC2 |
| 展板 `guide_board_processing.py` | 图片 | 分块整理与汇总草稿 | 人工核对后保存，不默认识别完全准确 |
| 先验/构思 `guide_knowledge.py`、`guide_conception*.py` | 点位展板、摘要、审核文献、主题 | 动态段落、来源 ID、准备票据 | 本点先验为主，不以旧稿作为唯一固定话术 |
| TTS `UnitreeSpeechWorker` | 文字/分段讲解 | SDK 接受与估算播放等待 | 不是麦克风回采；请求索引修复避免重复忽略 |
| 手臂 `guide_arm_action.py` | 官方预设、保持时间、验收白名单 | 请求接受、计时保持、release | 默认禁用；查询成功不是动作验收 |

## 速度与 TF 适配

`/navigation_odom` 提供滤波器实际速度供局部规划反馈，保留原定位链路；静态外参走 `/tf_static`，避免高频重复广播。EMA 分离原始输入和输出循环，持续输出但原始指令失效后归零。

有限 Python 位姿/导航检查在导入数值库之前设置进程级 OpenBLAS/OMP/MKL/NumExpr 单线程预算，降低 CPU 争用。不能将此预算施加到启动 FastLIO 的父进程，也不能改 TF 时间戳或放宽过期阈值来掩盖堵塞。

## Agentic 的边界

现有能力可组成 perception（雷达/定位状态）、action（安全导航）、conception（先验驱动构思）、presentation（到点复核后播报）。主动摄像头观察、调姿读展板、视觉优化地图、长时自主探索仍在规划。麦克风阵列可通过采音/STT 替换输入入口，但流式噪声、会话、时序与打断仍需独立工程设计。
