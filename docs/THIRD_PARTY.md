# 来源与许可

本仓库包含上游代码、项目适配与历史文件，不应为整个树随意指定统一 MIT 等许可。发布前确认各组件实际许可证、图片/展板/文献版权和场地数据披露授权；第三方原许可保留。

| 目录 | 来源与说明 |
| --- | --- |
| `Livox-SDK2/` | Livox SDK2；来源由该目录 README 记录，许可见其 LICENSE 文件 |
| `unitree_sdk2_python/` | Unitree 官方 Python SDK；安全控制器为本项目适配，保留官方 SDK 原许可 |
| `G1Nav2D/src/livox_ros_driver2-master/` | Livox ROS Driver2，使用 ROS1 分支；许可见 LICENSE.txt |
| `G1Nav2D/src/fastlio2/` | 基于现有 FastLIO 定位工程，README 列 FAST_LIO/FASTLIO-SAM/FAST_LIO_LOCALIZATION 等参考；不能把参考链接当作精确 fork/commit 证明 |
| `pointcloud_to_laserscan`、`tool`、`ros_map_edit` | ROS 转换/工具/地图编辑组件，保留各自已有许可 |
| `movebase`、`velocity_smoother_ema` | 现有导航配置和平滑器及项目适配；ROS navigation/TEB/costmap 等通过系统依赖提供 |
| `scripts/` | DaoLan 网站、任务调度、安全适配和测试；历史辅助文件保留，新增文档未授予统一开源许可 |
| `PythonProject/` | 历史小智/语音实验、缓存/模型/环境与可能私有配置，保留本地，不进入默认源码导出 |

原工作目录无有效 Git 历史，不能可靠复原所有上游版本号和修改作者。此次生成的是带快照清单的新仓库基线，不伪造旧历史。正式公开前补充上游版本锁定、许可审查和必要的署名。
