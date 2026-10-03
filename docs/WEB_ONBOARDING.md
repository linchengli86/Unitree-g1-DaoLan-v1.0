# 网页建图与首次设置：实现、输入输出和交接

用户只读 [Quickstart](QUICKSTART.md)。本文解释后台操作和正式发行前待验收项。

## 入口与职责

| 模块 | 输入 | 输出 / 产物 | 限制 |
| --- | --- | --- | --- |
| `quickstart.sh` / `setup_pc2.py` | 启动；`--check`；显式 `--prepare` | 环境检查、构建、网站；`run/setup_versions.json` | 不发目标或使能；只支持 Ubuntu 20.04 aarch64 |
| `/setup` | 物理确认、PIN、建图/保存/审核 | 会话进度、地图预览、权限设置 | 不能切官方运动模式或给硬件通电 |
| `MappingManager` | 严格字段与当前 session_id | 状态、哈希、独立输出、备份版本 | 与任务/初始化/重定位互斥；不接收任意路径/命令 |
| `web_mapping_worker.py` | 后台固定会话目录 | ROS 建图、二维 PGM/YAML、日志 | 只管理本机 Master；不发速度，只退出归属明确的旧服务 |
| `map_builder_node` | Livox 点云与 IMU | 三维/地面 PCD、关键位姿 | 回收线程后保存，不在信号处理函数写 PCD |
| `guide_operator_setup.py` | 6–32 位数字 PIN、模块验收声明、导航权限 | 权限为 600 的私有配置 | 保留 Key；手臂关闭；设置权限不使能运动 |
| 现有初始化/点位/导航 | 地图、人工定位先验、机器人采集点位 | fresh TF / 登记点 / 路径 / 到达结果 | 延续时效、运动门和停止保护，不放宽安全阈值 |

## 环境准备

默认不安装：检查系统、ROS 环境、导航构建和控制 SDK 后启动网站。`--check` 只读，不启动服务；`--prepare` 明确触发 sudo apt、构建和 Python 安装。有导航/控制/建图服务运行时拒绝安装构建，不自动关闭它们。

依赖列在 `setup_pc2.PACKAGES`。使用仓库内 Livox SDK2；优先保留厂商 Python/DDS 环境，缺失时创建 `~/robot_dev/envs/unitree-core`，构建 CycloneDDS tag `0.10.2` 并安装相应 Python 包。仅缺失 NumPy 时安装 `1.24.4`。Unitree SDK 通过新增 `setup.py` 本地包装安装，版本标记 `1.0.1+daolan.snapshot` 指本地快照，不是新的上游发布。

记录 apt 与控制包版本，不输出 Key 或带凭证的 pip URL。DDS 路径持久化到私有配置；重复检查只解析选定字面路径，不执行配置。可信 Noetic apt 源、网络和 sudo 是前提；不绕过签名、修改源或升级 OS。

可选模型依赖：`bash scripts/quickstart.sh --prepare --with-omni`。Key 和音频启用另行配置，参见 [部署](DEPLOYMENT.md)。新模板 `GUIDE_AUDIO_ENABLED=0`；历史配置没有此项仍保持原音频行为，避免无意改变现有部署。

## 建图生命周期

```text
idle → starting → mapping → saving → review → activating → succeeded
                  └──────── 取消 → stopping → cancelled
失败 → failed（不激活本次产物）
```

1. 开始必须确认落地、官方运动模式、拆绳、停稳与遥控器监护。只退出本项目初始化器明确启动的空闲导航/安全控制服务；先对控制器请求禁用运动，再退出。发现旧控制器、未知归属或活动目标就拒绝，不强杀。
2. 启动独立 mapping.launch，RViz 关闭，输出指向会话；收到新鲜 IMU 与点云后才显示正在建图。用户遥控走场地，不自动行走。
3. 停稳后保存：从 `map` 坐标 `/projected_map` 保存 PGM/YAML，向本次 ROS launch 进程组发 SIGINT。C++ 主线程回收发布/提取/闭环线程后保存三维图、地面图和关键位姿；不对其他节点发退出信号。
4. 保存未结束不允许激活。45 秒退出预算超出后保持占用，等实际退出并将本次判为未验收；不强杀制造“完成”。
5. 五个文件结构/哈希和元数据检查后进入 review。网页显示二维预览，必须人工核对墙体、通道、自由区域与方向；不等于几何精度验收。
6. 审核后，仅在导航/控制/定位完全退出时备份并激活。版本记录最后写入。激活后初始化状态失效，旧预览 token 清空，需重新定位。

网页建图使用 `web_mapping:=true`：全局 `/ground_cloud` 投影到 `map`，原命令行分支保留。该投影与三维图真实几何一致性、闭环修正及新场地导航仍待实机验证，不能仅凭软件测试认定地图质量。

## 文件与恢复边界

会话：`run/mapping/<session_id>/`；日志 `worker.log` / `roslaunch.log`，状态 `status.json`。产物采用与部署一致的布局：

```text
map/map.pgm + map.yaml
G1Nav2D/src/fastlio2/PCD/map.pcd + ground_map.pcd
G1Nav2D/src/fastlio2/path/key_poses.txt
```

激活备份：`run/backups/mapping_<session_id>_<attempt>/`，保存旧五项文件及存在的 `map.json`、`map_region.json`、`config/guide_points.json`。新图启动空点位登记；照片/文献不删除，登记备份保留原关联。旧编辑标注不能套在新坐标系，归档后不作为新图配置使用。

失败回滚已替换文件；重试创建新备份，不覆盖前次备份。取消不覆盖部署地图，隔离产物保留。网站重启若 worker 会话锁仍占用，仅提供取消并等待退出，不续跑导航或认可地图。没有持锁的未审核会话仍在磁盘，但不自动恢复为可激活状态。

离线替换是受控逐文件操作，不是断电原子事务。禁止切图期间换电。异常断电恢复时，先停服务，由维护者核对同一备份完整五项文件、YAML image 与点位指纹，再恢复；不能混用不同会话。

## HTTP 契约

| 接口 | JSON / 返回 |
| --- | --- |
| `GET /api/mapping` | 状态、已有地图、是否需 PIN；只读 |
| `POST /api/mapping/start` | `robot_ready:true, manual_only:true`，已有 PIN 时带 `pin` |
| `POST /api/mapping/finish` | 当前 `session_id, stationary:true`，已有 PIN 时带 `pin` |
| `GET /api/mapping/preview` | 当前 review 的 PNG，不接收任意文件路径 |
| `POST /api/mapping/activate` | 当前 `session_id, map_reviewed:true`，已有 PIN 时带 `pin` |
| `POST /api/mapping/cancel` | 空对象；停止类操作不要求 PIN |
| `POST /api/setup/operator` | `new_pin, enable_navigation:boolean, module_reviewed:true`；改已有 PIN 需原 `pin` |

不是公网认证：首次无 PIN 时仅可在受控可信局域网设置，不暴露公网。页面访问、浏览器前进/后退不会自动 POST 初始化或建图。

## 正式发布验收

软件测试覆盖互斥、字段/PIN、地图结构、PGM 翻转、归属/PID 复用、备份回滚、旧登记归档和浏览器只读启动。新增 C++ 尚未在 PC2 编译，安装器尚未在干净 PC2 执行，网页新图尚未实机走完。必须完成 [复现契约](REPRODUCIBILITY.md) 后才发布“零调试复现”版本。
