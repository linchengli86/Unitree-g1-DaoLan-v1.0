# 来源与许可审查

## 当前结论（2026-10-04）

**许可审查尚未完全通过** 功能实机验证、软件测试和许可合规是不同的验收项目。

本次核查对象为 `dist/DaoLan` 中准备交付的源码、声明的直接依赖及相关上游许可。不包含 PC2 全部已安装包的版本和传递依赖清单；“找到许可证”不等于已完成来源、修改和分发条件核验。本文是工程审查记录，不是法律意见。

## 随仓库分发的组件

路径相对仓库根目录。下表“有许可”仅说明发现明确许可文本，仍须保留原声明并核实来源及适用范围。

| 组件 / 路径 | 已发现的许可证据 | 状态与待办 |
| --- | --- | --- |
| `unitree_sdk2_python/` | `LICENSE`：BSD-3-Clause | 原 SDK 有许可；本项目安全控制器和其他新增适配的授权需另行确认 |
| `Livox-SDK2/` | `LICENSE.txt`：MIT | 保留全文与版权声明，补记快照来源 |
| `G1Nav2D/src/livox_ros_driver2-master/` | `LICENSE.txt`：MIT | 保留全文与版权声明，补记所用 ROS1 源码版本 |
| Livox 内嵌 FastCRC、spdlog | 各自 `LICENSE.md` / `LICENSE`：MIT | 不应仅保留外层 SDK 许可 |
| spdlog 内嵌 fmt | `Livox-SDK2/3rdparty/spdlog/spdlog/fmt/bundled/LICENSE.rst`：BSD-2-Clause | 按本地副本处理，不以最新上游许可替代旧版证据 |
| 两处内嵌 RapidJSON | 各自 `3rdparty/rapidjson/license.txt`：MIT 及列出的第三方例外 | 核对实际纳入的文件与例外范围；不能把全部文件一概标为 MIT |
| `G1Nav2D/src/pointcloud_to_laserscan/` | `package.xml` 声明 BSD；源码头部有 BSD-3-Clause 全文 | 有明确许可，不因缺少独立 LICENSE 就判为无许可 |
| `G1Nav2D/src/tool/` | `LICENSE`：MIT，版权 2024 liangheming；`package.xml` 为 TODO | 核实继承文件的覆盖范围后，再同步 manifest |
| `G1Nav2D/src/fastlio2/` | 外层 `LICENSE`：MIT，版权 2024 liangheming；manifest 为 TODO | **存在嵌套许可与来源疑点，不能认定全目录为 MIT** |
| `fastlio2/include/ikd-Tree/` | 源码注明 Yixi Cai；未发现独立本地许可；对应上游为 GPLv2 | **公开发布阻断项：追溯实际版本和授权，补齐适用许可及分发义务** |
| `fastlio2/include/IKFoM_toolkit/` | 多处源码头部有 BSD-3-Clause 声明 | 保留逐文件声明；与 ikd-Tree 分开核查 |
| `G1Nav2D/src/velocity_smoother_ema/` | manifest 为 TODO，未找到本地许可文件 | **公开发布阻断项：取得确切来源的许可或作者授权** |
| `G1Nav2D/src/ros_map_edit/` | manifest 为 TODO，未找到本地许可文件 | **公开发布阻断项：取得确切来源的许可或作者授权** |
| `G1Nav2D/src/movebase/`（包名 xju_pnc） | manifest 为 TODO，未找到本地许可文件 | **公开发布阻断项：核实这个本地包，而非以官方 move_base 的许可代替** |
| 网站、任务调度、安全适配、测试与文档 | 没有项目根 LICENSE | 确认作者 / 所属机构的授权权利后，确定自有部分的许可与范围 |

`scripts/license.txt` 仅含一个哈希样式字符串，不构成开源许可，不应作为项目授权证据。许可字段 TODO 也不是授权；反过来，有完整源码许可时，TODO 首先是声明不一致，不能一概当作完全无许可。

## FastLIO / ikd-Tree：重点核查

本地 `fastlio2/CMakeLists.txt` 把 `include/ikd-Tree/ikd_Tree.cpp` 纳入建图、定位等可执行目标，属于实际使用的代码，不是闲置参考材料。

[ikd-Tree 官方 LICENSE](https://github.com/hku-mars/ikd-Tree/blob/main/LICENSE) 和 [FAST_LIO 官方 LICENSE](https://github.com/hku-mars/FAST_LIO/blob/main/LICENSE) 都提供 GPLv2 文本。外层 MIT 文件并不能自动重新授权继承的 GPL 代码。需要确认具体来源版本、有无额外授权，以及修改、链接和分发的适用范围；目前没有证据认定整个 DaoLan 都是 GPL，也没有证据认定整个 FastLIO 目录都是 MIT。

GPL 不意味着不能公开，而是要履行其适用条件。不能只添加一个 MIT 文件来“解决”这个问题；也不能仅凭参考链接伪造 fork / commit 来源。

## 安装依赖与模型接口

- apt 直接依赖见 `scripts/setup_pc2.py` 的 `PACKAGES`；涉及 ROS、Qt、PCL、OpenCV、GTSAM 等，许可不统一，版本及模块会影响结论。
- Python / DDS 依赖见 SDK 安装声明、安装器和 `scripts/omni-requirements.txt`。CycloneDDS 原生版本选择 0.10.2；上游提供 [EPL-2.0 / EDL-1.0 双许可](https://github.com/eclipse-cyclonedds/cyclonedds/blob/0.10.2/LICENSE)。Python 绑定须独立核查，不能直接套用原生库许可。
- [websocket-client 上游 LICENSE](https://github.com/websocket-client/websocket-client/blob/master/LICENSE) 提供 Apache-2.0；仍需核对实际安装版本。NumPy、Pillow、PyYAML 等亦需按实际版本保存证据。
- 保存 PC2 实际安装报告（如 `run/setup_versions.json`）、Python 包元数据及 apt 包的 `/usr/share/doc/<包名>/copyright`，检查传递依赖与源码 / 二进制分发差异。当前源码审查不替代这一步。
- Omni 使用云接口；仓库代码的许可不包含模型权重或服务使用权。使用者须自行取得账号、Key 并遵守服务商条款；密钥不能入库。

## 资料与现场数据

展板图片、文献、录音和地图分别需要版权、隐私及场地披露授权。默认排除根级运行资产不意味着所有历史资料都已清理：还需人工检查已跟踪文件，例如 `G1Nav2D/src/ros_map_edit/maps/` 和 `G1Nav2D/src/tool/path/`。不要把包内历史样例默认当作可公开的现场资产。

## 公开发布检查表

以下项目尚未全部完成，未完成前不发布“许可已清理”的正式开源版本：

- [ ] 追溯 velocity_smoother_ema、ros_map_edit、xju_pnc 的确切来源和版本，获得适用许可 / 授权；无法确认时，决定取得许可或以独立实现替换。
- [ ] 核实 FastLIO / ikd-Tree 来源、修改与许可范围，保留适用的 GPL 和其他声明；必要时寻求专业审查。
- [ ] 核对 tool、FastLIO 等 manifest 与逐文件许可；未经证据确认不把 TODO 自动改成 MIT。
- [ ] 确认自有代码的权利归属，确定项目根 LICENSE 的适用范围；不覆盖第三方授权。
- [ ] 补全组件来源 URL、可验证 commit / 版本、修改说明及第三方版权与许可汇总。
- [ ] 核查 PC2 实际版本和传递依赖，记录源码及二进制分发需要满足的条件。
- [ ] 完成人工资料、隐私、秘密与场地数据审查。
- [ ] 将完成的修复提交并重新验证交付物；旧 bundle 不会自动包含新的许可记录。

[GitHub 官方许可说明](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository)：公开可见和可 fork 不等于获得一般的使用、修改与再分发许可。

## 来源记录与安装适配

原工作目录无有效 Git 历史，目前不能可靠复原所有上游版本号和修改作者。交付仓库是新的源码快照基线，不伪造旧历史。`PythonProject/` 历史语音实验不进入默认源码导出，不在本次源码包审查范围内。

`unitree_sdk2_python/setup.py` 是本地包装；`1.0.1+daolan.snapshot` 标记本地快照，不是上游发行承诺。安装器参考 [Unitree 官方 SDK 环境要求](https://github.com/unitreerobotics/unitree_sdk2_python)，外部下载只在安装时发生，不把外部源码归为本项目许可。
