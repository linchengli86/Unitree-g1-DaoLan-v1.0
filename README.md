# DaoLan · Unitree G1 导览系统

先看 [Quickstart：一个入口，从网页开始](docs/QUICKSTART.md)。当前分支补齐环境准备和网页建图向导，是待实机验收的候选版；已验证的导航基线仍保留，不宣称零调试发行版。

PC2 完成 **定位 → 自主规划到导览点 → 到达核验**，网站统一管理初始化、地图、展点、资料和任务。Omni 提供可选的多模态理解和动态讲解；模型提出计划，确定性安全执行器控制机器人。

## 已完成

- MID360 + FastLIO 建图、旧地图恢复、有界重定位与独立扫描核验。
- 实时 2D 位姿/障碍地图、8 个登记导览点、展板上传及分块整理、文献通道。
- 不依赖示教路线的单点自主导航；网站独立“点位导航”入口，不调用 Omni 或语音。
- ROS 导航、EMA 平滑、Unitree 安全控制器贯通；当前上限 **0.60 m/s、0.70 rad/s**，不是恒速或 SDK 高速模式。
- 导航速度反馈适配、TF 防护与有限 Python 检查的线程争用修复。
- 文字/图片/录音 → Omni 白名单计划，点位先验驱动的动态构思及机器人 TTS。
- 任务预览、PIN 确认、模拟、取消、超时、到达验证、日志和重启不续跑。

实机已验证单点移动和到达核验；独立网页调度已部署并模拟通过，**不代表所有点位路线均已实机验收**。2026-10-03 回归：本机 500 项通过；PC2 500 项中 5 项可选测试跳过，其余通过。[验证与边界](docs/VALIDATION.md)

## 使用

兼容 PC2 首次准备（安装/构建需 sudo；可信 ROS Noetic 源须已配置）：

```bash
cd ~/robot/DaoLan
bash scripts/quickstart.sh --prepare
```

以后启动：`bash scripts/quickstart.sh`。局域网打开 `http://<PC2-IP>:8765/setup`，默认纯运动，不要求语音/Omni Key：

1. 新场地：开始建图 → 遥控走场地 → 停稳保存 → 人工预览 → 备份并激活；已有地图跳过。
2. 初始化/核对定位 → 遥控到展点、调整朝向并登记；首次设置 PIN 和已验收导航权限。
3. 点位导航 → 物理安全确认 → 预览/PIN 执行；到达或失败后禁用运动。

源码仓库不含现场地图、点位、密钥和运行环境，不能 clone 后直接驱动机器人。[部署](docs/DEPLOYMENT.md) · [操作](docs/OPERATIONS.md)

网页建图在隔离会话输出 PCD、地面图、关键位姿和二维图，结构校验与人工审核后离线切换，失败回滚。新图激活归档旧点位登记，照片/文献保留。[详细生命周期与输入输出](docs/WEB_ONBOARDING.md)

## 架构与开发

```text
网页选点 ─────────────────────┐
文字 / 图片 / 录音 → Omni 计划 ─┴→ 预览 / PIN → PC2 TaskExecutor
                                             ├→ 安全导航 → ROS → Unitree
                                             └→ 动态构思 / TTS（独立模块）
```

`scripts/`：网站/调度/安全辅助；`G1Nav2D/`：ROS 工作空间；`unitree_sdk2_python/` 和 `Livox-SDK2/`：上游 SDK 与本地适配。

```bash
/usr/bin/python3 -m unittest discover -s scripts -p 'test_*.py'
python3 scripts/export_repository.py --output dist/DaoLan.bundle
```

测试使用隔离数据与模拟接口。导出命令生成可克隆 Git bundle，不提交密钥和现场数据、不连接 PC2、不让机器人运动。[文档总览](docs/README.md) · [模块输入输出](docs/ARCHITECTURE.md) · [完整 API/Omni 契约](docs/OMNI_GUIDE.md) · [仓库交付](docs/REPOSITORY.md)

仅用于受控局域网、操作员持官方遥控器监护的联调；网页停止不替代物理急停。主动摄像头观测、视觉地图优化、麦克风阵列与完整自主探索仍在规划。第三方代码保留各自许可，项目尚未指定统一开源许可。[来源与许可](docs/THIRD_PARTY.md)
