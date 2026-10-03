# DaoLan 导览能力契约与 Omni 接入

此文档记录 DaoLan 的能力输入、输出、运行产物与验证边界。网页是入口，Omni 负责理解意图并生成白名单动作计划；PC2 顺序执行器负责调度，机器人运动仍由现有 ROS 导航和 `g1_control_safe.py` 执行。软件接入不等于现场动作验证通过。

当前链路：文字/照片/录音 → Omni → 结构化计划 → 网页预览与确认 → PC2 顺序执行 → 每步结果。模型不能执行任意代码、发布速度或越过确认。未来麦克风阵列可接“采音 → STT → `/api/intent`”；下游动作契约无需改变。暂不实现连续双工语音或语音打断。

## 能力清单

| 能力 | 输入 | 输出 / 成功判据 | 产物或接口 | 网页/Omni 接入 |
| --- | --- | --- | --- | --- |
| 建图 | Livox MID360 点云与 IMU、人工带机器人走全场 | 三维 PCD、地面 PCD、关键位姿，随后生成二维栅格 | `G1Nav2D/src/fastlio2/PCD/map.pcd`、`ground_map.pcd`、`path/key_poses.txt`、`map/map.pgm` 与 `map/map.yaml` | 离线准备；`GET /api/assets` 只检查文件是否存在 |
| 重定位 | 既有 PCD + 初始位置 `(x,y,z,roll,pitch,yaw)` + 实时点云 | `/slam_reloc_check` 为 True，`map→base_link` TF 连续且位置与实物相符 | `scripts/auto_reloc.sh`、`/slam_reloc`、`/slam_reloc_check` | `GET /api/status` 只读状态；不允许 Omni 自行重定位 |
| 激光与动态障碍物 | `/scan`、点云、机器人当前姿态 | `/scan` 约 10 Hz、局部代价地图发布；纸箱实验中前方距离减少、致命/膨胀栅格增加 | `/move_base/local_costmap/costmap`、`costmap_updates` | 路线脚本预检 `/scan`；代价地图由 `move_base` 使用 |
| 路径规划 | 二维地图、当前定位、目标/录制路线 | 非空全局路径和有效 `/cmd_vel` | `/move_base/GlobalPlanner/plan`、`/cmd_vel` | 路线回放脚本在运动使能前检查 |
| 速度平滑与运动安全 | `/cmd_vel`、非空路径、人工使能 | `/cmd_vel_smooth`；当前部署上限 `vx≤0.60m/s, vy=0, wz≤0.70rad/s`；超时或零速执行 StopMove | `g1_control_safe.py`、`/unitree_motion_enable`、`/unitree_emergency_stop` | 网页确认后由导航脚本控制；网页不直接发布速度 |
| 录制路线回放 | `routes/demo_route.txt`，方向 `start/end`，当前 TF | 全部导航目标 SUCCEEDED 且回放进程退出 0；完成、失败或超时后禁用运动 | `scripts/route_demo.sh`、`scripts/replay_teaching_route_safe.py`、`run/tasks/<id>.log` | `navigate_route` 动作；网页 PIN 确认后运行 |
| 停止导航 | 操作员点击停止，或 Omni 调用 `stop_navigation` | 急停、禁用运动、取消 `move_base` 目标 | `scripts/guide_stop_motion.sh` | `POST /api/stop`；停止操作不需要运动开关 |
| 机器人朗读 | UTF-8 文字，最多 500 字 | 机器人扬声器播报 | Unitree DDS AudioClient、`POST /api/speak` | Omni 文字回答自动进入同一播报队列 |
| 文字导览 | 手机输入的文字 | 小智助手受理并通过机器人朗读 | `/api/assistant`、`/tmp/dialogue_trigger.sock` | 保留原功能，和 Omni 并列 |
| Omni 多模态问答 | 文字、JPEG 照片或 16 kHz 单声道 PCM16 音频 | 回答文字与工具调用；回答送入机器人朗读队列 | `POST /api/omni`、`scripts/omni_client.py` | PC2 持有 API Key，手机不持有 Key |
| 上层动作编排 | 意图或版本 1 动作计划 | 顺序执行、逐步状态；失败/取消跳过后续步骤；重启不续跑 | `scripts/guide_task_executor.py`、`run/tasks/current.json`、`run/tasks/<id>.json` | 预览、模拟、确认执行、查询进度 |
| 官方手臂预设 | 动作名及保持秒数 | SDK 接受、计时保持、release-arm 接受；没有真实结束反馈 | `scripts/guide_arm_action.py`、官方 G1ArmActionClient | 默认关闭；逐项实机验证后登记白名单 |

已验证的 3D 地图查看方式是将 PCD 转为 PLY 后用 CloudCompare 检查；RViz 黑屏不能作为地图不存在的证据。本次恢复并校验的旧地图是导航基线。`/slam_reloc_check=True` 也不能单独证明物理位置正确，路线执行前仍需操作员目测地图位置与真实位置，误差按现场约 20 cm 要求判断。

PC2 的 PulseAudio 输入曾录到全零音频，故手机提供音频输入。当前 HTTP 页面可用手机输入法的语音转文字或上传录音；网页实时麦克风需要可信 HTTPS。Omni 的回复目前采用已验证的 Unitree TTS 朗读，不使用 PC2 的 PulseAudio 扬声器。

## 网页 API 契约

所有 POST 请求使用 JSON。成功返回 `{"status":"success", ...}`，失败返回 `{"status":"error","message":"..."}`。默认端口 8765。

| API | 输入 | 输出 | 副作用 |
| --- | --- | --- | --- |
| `GET /api/health` | 无 | 服务、助手、Omni、运动开关状态 | 无 |
| `GET /api/skills` | 无 | Omni 可调用工具及参数 schema | 无 |
| `GET /api/assets` | 无 | 地图和路线文件存在状态 | 无 |
| `GET /api/status` | 无 | 定位、规划、安全控制器状态 | 无 |
| `GET /api/init` | 无 | 软件初始化阶段、是否需要人工定位初值、PIN 要求 | 无 |
| `POST /api/init/start` | `robot_ready: true`、操作员 `pin` | 后台初始化状态 | 检查/启动固定 ROS 服务和安全控制器；禁用自动运动，不导航、不播报 |
| `GET /api/map`、`GET /api/map/image` | 无 | 地图坐标、联合指纹 / PNG | 只读既有地图 |
| `GET /api/map/live` | 无 | 约 2 Hz 缓存的当前位置、扫描点与局部障碍状态 | 懒启动共享只读 ROS 订阅器；不初始化机器人、不发布控制指令 |
| `POST /api/relocation/manual` | `x,y,yaw,map_fingerprint,confirmed_position:true,pin` | 后台重定位状态及最终精配位姿 | 初始化完成后人工给出固定 1 m、±45° 粗先验；停止并禁用运动，一轮有界候选匹配及独立扫描核验，不导航；不接受客户端扩大范围 |
| `GET /api/actions` | 无 | 标准动作参数、完成判据、手臂验证状态 | 无 |
| `GET /api/tasks/current` | 无 | 最新任务和逐步状态 | 无 |
| `POST /api/omni` 或 `/api/intent` | `text`、可选 `image_jpeg_base64`、`audio_pcm16_base64` | `text`、`actions`、`tool_calls`，可选 `confirmation` 和 `plan` | 空闲时普通回答入朗读队列；任务预览不自动朗读；仅停止工具可立即停止任务 |
| `POST /api/speak` | `text` | 排队确认 | 机器人朗读 |
| `POST /api/assistant` | `text` | 小智受理确认 | 小智回复可朗读 |
| `POST /api/route/prepare` | `destination: start/end` | 90 秒有效的 `token` | 无运动 |
| `POST /api/route/confirm` | `token`、操作员 `pin` | 单步路线任务 | 兼容旧接口，转为顺序任务执行 |
| `POST /api/navigation/prepare` | 仅已登记的 `point_id` | 单步移动计划、90 秒有效的 `confirmation`、警告 | 仅预览；不调用 Omni、ROS 运动或语音 |
| `POST /api/plans/prepare` | `plan` | 90 秒有效的 `confirmation`、规范化 `plan`、警告 | 只预览 |
| `POST /api/plans/template` | `name: speech_demo/round_trip/wave_preview` | 同上 | 只预览 |
| `POST /api/plans/confirm` | `token`、运动任务需 `pin` | `task` | 确认后开始顺序执行；纯讲解不需 PIN |
| `POST /api/plans/dry-run` | `token` | 模拟 `task` | 不调用 ROS、DDS、TTS，不能证明实机可执行 |
| `POST /api/stop` | `{}` | `task_cancelled`、`stopped` 与详细结果 | 清除待确认计划、取消任务、急停、禁用运动、取消导航目标 |

Omni 可调用的工具严格为 `get_action_catalog`、`get_task_status`、`request_action_plan`、`get_robot_status`、`get_guide_assets`、`list_guide_points`、`get_point_knowledge`、`get_observation_policy`、兼容的 `request_route`、`stop_navigation`。提出计划与执行计划分离。网页运动开关默认关闭；即使开启，也需要一次性 token 和操作员 PIN。现场应有人监护、手持遥控器，并先确认定位与周围空间。

Realtime 模型的 `request_action_plan` 工具入参采用浅层传输：`title` 和 `steps_json` 两个字符串，后者是完整步骤数组的 JSON 文本。PC2 用 `json.loads` 解码后仍执行同一版本 1 白名单校验，返回结构化 `plan`。网页 `/api/plans/prepare` 仍直接接收 `plan` 对象，不改变网站/执行器契约；旧工具调用的 `title + steps` 也保留兼容。不能把标题或自然语言列表当作执行计划。

## 标准动作契约（版本 1）

| action | parameters 输入 | 输出 / 完成条件 |
| --- | --- | --- |
| `check_status` | `require_localized`，默认 false | 新鲜 ROS 状态；要求定位时检查为 True，否则任务失败 |
| `speak` | `text`，1–150 字 | SDK 接受后按文字长度估算等待；不是扬声器真实结束反馈 |
| `navigate_route` | `destination: start/end` | 路线预检、规划、安全控制器及全部导航目标完成；进程退出 0 |
| `navigate_to_point` | 已登记 `point_id` | 当前地图匹配、规划到该位姿；单个受控 move_base 目标成功，停止后到达验证通过；不依赖示教路线 |
| `verify_arrival` | 已登记 `point_id` | 新鲜且稳定的定位；位置误差 ≤0.20 m、朝向误差 ≤0.20 rad |
| `present_point` | `point_id`、可选 `topic`（≤200 字） | 到达核验 → 动态 conception → 再核验 → 分段 TTS；禁止输入固定解说 text |
| `wait` | `seconds: 0–30` | 计时结束 |
| `arm_gesture` | `gesture: wave/handshake/clap/heart/raise_hand`，`hold_seconds: 1–5`，默认 3 | 底盘停止确认、官方预设请求接受、计时保持、释放手臂接受；需已登记的验证白名单 |
| `stop` | `{}` | 安全控制器急停或禁用服务确认 |

每步可指定 `timeout`（秒）；最多 10 步、总超时预算不超过 900 秒。参数/动作白名单拒绝任意脚本、任意坐标和未知字段。单个任务运行时不接受第二个任务，步骤超时或失败不继续下一步；含运动任务结束后再次停止。已开始的 TTS 可能继续播完，未承诺语音打断。

任务状态：`running/succeeded/failed/cancelled/simulated/interrupted`；步骤另有 `pending/skipped`。返回“已提交”只表示执行线程启动，必须查看最终任务结果。任务记录与子进程日志保留在 `run/tasks/`。

```json
{"title":"欢迎讲解","steps":[
  {"action":"speak","parameters":{"text":"欢迎参加导览。"}},
  {"action":"wait","parameters":{"seconds":2}},
  {"action":"speak","parameters":{"text":"我们准备出发。"}}
]}
```

## 网页记录导览点和展板资料

网页使用同一个 `8765` 服务端口，通过独立子页面组织功能，现有 `/api/*` 接口不变：

- `/`：工作台入口、点位数量、连接状态与最近任务。
- `/initialize`：首页“一键初始化”入口；检查固定软件服务，在未知位置时点选当前大致站位和朝向，提交固定 1 m、±45° 粗定位先验。
- `/map`：只读实时 2D 地图，查看简化墙体、当前位置与朝向、附近实时障碍物。
- `/assistant`：Omni 多模态问答、文字朗读和计划预览。
- `/tasks`：任务模板、示教路线任务、模拟/确认与执行状态。
- `/navigation`：独立点位移动；选择导览点、确认现场准备状态、预览并输入 PIN 后执行。不附带讲解。
- `/agent`：选择已登记展点和主题，动态讲解预览，点位自主导航任务预览与确认。
- `/knowledge`：展点先验、资料来源和绑定文献；不触发机器人动作。
- `/points`：已保存点位列表，可按名称或讲解关键词搜索，展开查看资料。
- `/points/new`：独立添加表单，填写名称、整理展板、核对讲解与采集位姿。
- `/localization`：实时位姿读取与一键重定位。
- `/system`：只读连接、权限、ROS 模块与地图/路线文件状态检查。

桌面侧边栏、手机导航栏均可切换页面；顶部始终保留“停止全部任务”。页面内部跳转和浏览器返回使用 History API，不刷新整页，因此保留未保存表单、已选择照片和待确认计划。直接访问或刷新任意上述路径也可以正确打开子页面，但整页刷新后未保存表单/照片和计划预览会丢失；确认 token 的后端有效期仍为 90 秒。页面切换本身不会保存点位、播报、发起运动或重定位。前端静态内容集中在 `scripts/guide_web.py`，通过 `/assets/guide.css` 与 `/assets/guide.js` 提供；页面不嵌入密钥或 PIN。界面测试：`python3 scripts/test_guide_web.py`（可选 Node 检查真实脚本的只读初始化、路由返回与输入保留）。

### 独立点位导航（不依赖讲解）

现场已验证的单点导航通过 `/navigation` 接入同一任务调度器：选择已登记点位 → 勾选已落地、官方运动模式、拆除吊绳、停稳并现场监护 → 准备仅移动计划 → 输入操作员 PIN 确认。导览点卡片的“前往此点（仅移动）”只打开此页面并选点，不直接执行。可先模拟；模拟不调用 ROS、DDS 或 TTS。

后端只接收登记点 ID，不接受任意坐标、速度、脚本或绕过验收参数。实际执行复用 `navigate_to_point_safe.py`，保留当前地图指纹、实时定位、TF 时效、扫描、有效目标路径、控制器看门狗和到达核验；执行器还核对返回的目标 ID、`arrived/verified/motion_disabled` 及位置误差 ≤0.20 m、朝向误差 ≤0.20 rad。任务失败不会继续其他动作。

当前 PC2 沿用刚才实机测试的 0.60 m/s、0.70 rad/s 上限。它是提速后的导航配置，不是 SDK 高速模式，不保证始终以该速度走：避障、转向和接近终点会减速。该页面不调用 Omni、不预取展板、不构思或朗读；地图与已保存点位/照片保持不变。其他点位首次路线仍须现场监护，不能把一次成功当作全场验收。

部署门为 `GUIDE_MOTION_ENABLED=1`、已设置操作员 PIN 和 `GUIDE_NAMED_NAV_VERIFIED=1`；后者开放已验收的导航模块供网站调度，并不自动使能控制器，也不代表每个点位都已单独验收。示例配置保持关闭默认值，手臂动作门不变。更新页面和调度器时仅在无活动任务、初始化或重定位的状态下重载网站；不重启雷达、定位、导航和安全控制器，不自动恢复或执行任务。接口与浏览器行为测试见 `scripts/test_navigation_web.py`。

网页下方的“导览点”区提供点位记录，不执行导航、播报或手臂动作：

1. 遥控机器人到点位，调好朝向，停止遥控并保持站稳。
2. 填写名称；可上传展板照片，点击“Omni 整理展板导览词”。照片会发送到已配置的百炼 Omni 服务，生成草稿，不自动播报。
3. 对照展板核对并编辑讲解（最多 150 字），有照片时必须勾选人工核对确认；可选择已实机验证的动作。
4. 可先点“读取当前位置（不运动）”查看地图坐标、朝向和 TF 数据年龄。再点击“定位并添加导览点”，按提示输入操作员 PIN；保存时会重新采集，不复用旧预览。系统读取 `map → base_link` 位姿，保存当前位置和朝向；不是填写或猜测坐标。

无需先保存名称为“起点”的点位才能记录其他点：起点只是导览点的名称和重定位初值，不是持续定位的前提。换电后成功重定位一次，移动过程中由现有定位器持续跟踪；不是每到一个展板都用旧起点重新定位。真正失去定位时应在已确认位置重定位，不能在未知新位置套用旧起点初值。

保存必须满足：重定位成功、实机时间、TF 最近更新并在连续 5 次采样中保持稳定（平移变化不超过 3 cm，朝向变化不超过约 2.9°）。这些检查不能代替现场对定位正确性的确认。坐标是二维导航目标，z 记为 0。同时记录配置地图的 YAML、PGM 和定位 PCD 联合指纹，后续导航必须检查地图匹配。

数据持久化在 `config/guide_points.json`，展板照片在 `config/guide_point_images/<id>.jpg`；应一起备份，不覆盖地图、示教路线或私密配置。名称重复拒绝保存，不覆盖原点位。网页上传照片按最长边 2200 像素和 1 MB 上限压缩，并保留这份照片供人工复核。

展板整理采用后台任务：大图生成整图概览＋四块有 10% 重叠的切片；已满足模型尺寸的小图只处理概览。每张送往 Omni 的 JPEG 不超过 190000 字节，Base64 不超过 256 KiB，尺寸适配 1280×720（竖图为 720×1280）。最多两路并发识别，提取区域事实后再用独立、无工具的 Omni 会话去重汇总为最多 150 字的讲解；总超时 150 秒，同时只允许一个展板任务。网页显示识别进度和人工核对提示。切片失败、模糊、冲突不能被汇总阶段消除为“确定”：部分失败仍可生成带缺失区域提示的草稿，全部失败不生成草稿。草稿不会自动保存、播报或运动。后台任务仅保留在内存，服务重启后需重新整理。

图片预处理由 `/usr/bin/python3 scripts/prepare_board_tiles.py` 执行，依赖系统 `python3-pil`（PC2 已有），不要求 SDK 或 Omni 虚拟环境安装 Pillow。照片会发送到已配置的百炼 Omni 服务；一次大图最多使用 5 次识别＋1 次汇总请求。切片不能恢复原照片已经模糊或压缩丢失的细节；字太小时仍应靠近、裁剪或分开拍摄。Omni 无法保证 OCR 零误差，所有草稿都应对照展板人工核对。

接口：`GET /api/points` 列出点位；`GET /api/points/pose` 只读采集当前坐标/朝向、TF 年龄和定位状态，不保存也不运动；`POST /api/points` 接收 name、speech、gesture、可选 image_jpeg_base64/board_reviewed、pin；`POST /api/points/draft` 只接收展板 JPEG 的 base64，立即返回 `job.id`；`GET /api/points/draft/<job_id>` 查询进度及最终 `job.draft`；`GET /api/points/<id>/image` 查看已保存照片。识别与汇总专用 Omni 会话都不提供任何机器人工具，即使收到工具调用也拒绝执行。

2026-10-03 修复位置读取时延：实测 TF 持续更新，但等待 ROS 临时节点退出会额外延迟约 0.8 秒，令应用侧误判过期。辅助程序立即 flush JSON，父进程收到结果就校验，后台有界回收临时节点；一键重定位使用相同传输。仍拒绝最新 TF 超过 1.5 秒、被冻结/乱序的时间戳、移动中的采样和未成功重定位。不是放宽过期门槛，也没有自动执行重定位或运动。

部署后在机器人移动至新位置、尚未保存起点时，连续三次实机 HTTP 位姿读取通过，位置约 `(9.81, 0.05)`、朝向约 `-141.4°`，最新 TF 年龄约 0.41/0.95/0.59 秒。没有自动保存正式点位、重定位或运动。新增传输回归覆盖先返回 JSON 再回收 ROS 小进程、重定位输入与错误返回；HTTP 回归覆盖没有起点也能读取、真正过期或执行任务时仍拒绝。

这一阶段只记录点位，尚未把导览点接入 `navigate_to` 自主导航；原有 `navigate_route` 回放接口不变。测试：`python3 scripts/test_guide_points.py`、`python3 scripts/test_board_processing.py` 和 `python3 scripts/test_omni_planning.py`（含展板模式隔离测试）。

## 导航速度反馈与 TF 防护（2026-10-03）

仅提高速度上限不等于实际提速。本次离线审计发现原 `/slam_odom` 只填写位姿，twist 全零；TEB 每轮把它当作静止起步速度。同时原 EMA 将平滑输出写回 raw，且用固定计数判定输入失鲜，实际规划间隔变长时会伪衰减。

现在保留原 `/slam_odom` 与 TF 的几何、时间戳、消费者不变，新增 `/navigation_odom`（`local` → `base_link`）：位姿及 KF 实测速率、同步 IMU 的去偏置角速度，按现有静态 body/base 外参转换，含非零杠杆臂。缺失外参/IMU、超过 0.5 秒的源数据、非有限值、异常跳变或不连续时间均不发布；重置后要求连续两帧。仅 TEB 的 `odom_topic` 改接此反馈，不能拿 `/cmd_vel` 充当实速。

EMA 输入和输出分离，30 Hz 用 steady clock；新命令超过 1/3 秒、时钟异常、定时长间断或非有限输入立即清零，收到全零指令不等待渐减。线/角加速度仍不超过 0.3 m/s²、0.5 rad/s²，速度上限仍为用户指定的 0.6 m/s、0.7 rad/s，控制器仍 DISARMED 启动、0.30 秒 watchdog。没有切换官方速度模式，也没有将 SDK 长有效期当作提速手段。

实际导航与 `--check-only` 按两个 costmap 的 `transform_tolerance` 及原 1.5 秒上限取最小值（当前 0.5 秒），不再把 0.7 秒延迟误判为可导航；保存点位和 `--verify-only` 保留既有采样政策。scan/map/make_plan 读取后、等待本目标路径时、启用运动前后都复核最新 TF 与新速度反馈，失鲜必须拒绝或禁用运动，禁用清理本身不能被失鲜阻挡。

2026-10-03 一次未启用运动的测试记录到 costmap 位姿延迟 0.7–2.3 秒，因无法取起始位姿而无新路径；取消后独立采样的 TF/odom/scan 又恢复约 10 Hz、消息年龄约 0.05 秒。这只能证明故障间歇出现，不能单凭空闲正常数据认定是源头 FIFO、TF 传输还是 move_base 缓冲。`scripts/diagnose_navigation_latency.py` 仅订阅、有限采样，将源消息年龄、独立 TF 监听年龄及 move_base 警告并列记录；无目标、使能、SDK 调用或自动播报。ACTIVE 仅表示导航尝试，非零指令也不证明物理运动；最终速度与故障归因仍需操作员实机测试。

进一步只读对照定位到一种可复现的资源争用：空闲定位 TF 输出年龄 p95 约 0.062 秒；连续 6 次默认 Python 预检期间，雷达原始扫描末点和 IMU 输入仍新鲜，但定位输出最高约 1.15 秒，只有 1 次预检通过。仅在这些 Python 辅助进程中将数值库工作线程限制为 1 后，连续 6 次预检全部通过，定位 TF 输出最高约 0.066 秒。单独导入 tf/NumPy 的对照没有复现超时，不能将问题简化成“import 本身”或据此断言所有 TF 故障已解决。`navigation_thread_budget.py` 在导航、位姿采集、网页重定位、被动诊断的 ROS/NumPy 导入前设置四项数值库线程环境变量；网页调用位姿辅助进程时仅复制并限制子进程环境，模块导入不修改父进程。初始化器和 FastLIO/C++ 进程的线程配置、TF 时间戳、0.5 秒新鲜度以及停稳/运动安全门槛均不改变。本修复不需要重启定位，也不清除当前重定位结果。

被动诊断在等待 ACTIVE 时也保留最近 20 秒的前置采样；未进入导航的失败也能记录。Livox 使用 AnyMsg，只按已验证的 ROS1 布局读取头部和首末点时间偏移，不反序列化点数组；分别标注扫描起点和原始末点（可能不同于 LIO 过滤后末点）。前置和导航阶段分别有界保留元数据，报告尾部静默及关键启停日志；不把独立监听器缓冲等同于 move_base 内部缓存。

部署后使用默认 shell（不手动设置线程变量）的 6 次只读预检没有 TF 超时：5 次通过，1 次被原停稳采样门拒绝。同期定位 TF 源输出最高约 0.078 秒，独立监听器 map/base_link 最高约 0.138 秒；没有 ACTIVE 目标或非零运动指令。停稳仍要求 5 帧采样中位置变化 ≤3 cm、朝向变化 ≤0.05 rad，定位抖动也可能触发“仍在移动”，不能用该提示认定机器人真的走动，更不需要重新添加原点位。完整隔离回归本机 488 项通过，PC2 488 项运行通过（4 项环境相关测试跳过）。更新前五个辅助程序已备份到 PC2 `run/backups/thread_budget_20261003_TpZZ7R/`；定位进程未重启，地图/导览点哈希未变化。只读通过不是路径可达、实际速度或实机导航验收证明。

部署需在任务空闲且运动禁用时编译 `localizer_node velocity_smoother_ema_node`，按真实 UID/argv/PID start_ticks 核验后正常关闭本项目服务并完整初始化。不能热替换二进制后声称旧进程已加载，也不能盲目 kill 裸 PID。导航重启会清除重定位，操作员必须重新提供真实粗先验并核对精配箭头。地图、导览点、照片保持原件。

## 一键重定位

网页“导览点”区域新增“一键重定位（不运动）”。默认初值优先使用名称为“起点”的已保存点；没有起点时使用 `config/relocalization_seed.json` 中最近确认的位置。也可以在下拉框中选择另一个已保存点。必须将机器人放在显示位置附近、朝向正确并停稳，再确认并输入操作员 PIN。

既有已保存点的流程保持原契约：取消待确认计划和导航任务 → 安全控制器确认停止/禁用运动 → 等待旧任务退出 → 检查地图指纹与定位数据稳定性 → 单次调用旧 `/slam_reloc` → 等待 `/slam_reloc_check` → 校验新鲜、稳定的 TF 和距初值不超过 20 cm → 显示结果。旧接口的历史姿态/重力约定不变；不能将地图点击的标准朝向直接送入旧接口。新地图粗定位成功后保存的最近确认位置会标记为 `precise_confirmed`；若之后使用这份缓存，则走新 `/slam_reloc_base`，以标准 `map→base_link` 初值精定位。新接口复用同一有界候选匹配与独立扫描核验，但缓存模式最终位置须距已确认初值不超过 20 cm、朝向差不超过 0.45 rad，不把验收范围放宽到 1 m。成功也不会重新使能运动。没有停止确认、地图不匹配、初值错误、数据过期或超时都会失败，不能靠扩大搜索范围绕过校验。按钮不做脚本层反复重试或全场搜索。

定位结果不能代替现场核对。该按钮不会寻找未知房间里的机器人；机器人换位置后，应选择实际所在的导览点，或在“一键初始化”页地图上给出当前位置的 1 m 范围和大致朝向，不要沿用错误起点。

接口：`GET /api/relocation` 返回可选初值和执行状态；`POST /api/relocation/start` 接收 point_id（默认 auto）、confirmed_position=true、pin，立即返回后台任务状态；重复请求拒绝。重定位进行中不能准备或确认新任务，也不能记录导览点。“停止全部任务”会取消等待和重定位客户端，但无法撤回已提交给本地 ICP 的计算；自动运动不会启用。网页进程重启不续跑重定位。

测试：`python3 scripts/test_guide_relocalization.py`，无 ROS 调用或实际运动；包括初值优先级、停止确认、重复/取消、地图/定位/20 cm 边界、PIN 和任务互斥。单项实机重定位由操作员按网页按钮验证。

## 一键软件初始化与人工地图初值

首页“一键初始化”打开 `/initialize`，打开页面只读取状态，不启动任何模块。操作员先确认双脚落地、官方运动模式、停稳，再输入 PIN 点击启动。网页不会给硬件通电、切换官方模式、使能自动运动、发导航目标或播报。

固定流程：检查原有地图、8 个点位及展板文件 → 检查 eth0 和雷达连接 → 复用或启动 `roslaunch fastlio navigation.launch rviz:=false` → 复用或启动 `g1_control_safe.py eth0` 并保持 DISARMED → 读取新鲜点云、IMU、`/scan`、`/slam_odom` → 检查重定位状态；已定位时读取连续稳定的 `map→base_link`。初始化不能替代传感器硬件供电和定位正确性的现场核对。

未定位也可完成软件初始化，但状态明确显示 `needs_initial_pose: true`，不会把零点 TF 当成当前位置。地图 PNG 来自既有 PGM，按 YAML 的分辨率和原点转换坐标；支持缩放、平移和已保存展点标记。在大致站位拖出朝向箭头，淡橙色圈表示固定半径 1 m 的位置先验，箭头允许实际朝向相差 ±45°。操作员确认真实位置在圈内、朝向在范围内且机器人停稳后，点击“在1米范围匹配定位（不行走）”；无需用鼠标点到 20 cm 内。所选圆心须在当前地图已知自由栅格内、联合指纹一致；这是当前位置的范围，不是导航目标，不能将“音圈”等目的展点冒充当前位置。

人工地图初值走新增 `/slam_reloc_coarse`：输入 `(x,y,z,roll,pitch,yaw)` 按标准 `map→base_link` 解释，由定位模块处理 body/base_frame 的变换，不用固定加减 π 猜测旧接口约定。搜索范围由服务器固定为 XY 半径 1 m、yaw ±45°，网页请求没有可自定义 `radius` 字段。模块评估 13 个圆内 XY 种子 × 5 个朝向种子，共 65 个候选，再对至多 4 个不同解精配准。搜索不完整、匹配有效点不足、重叠率/残差不达标，或不同位置解无法显著区分时，一律失败，不扩大范围或接受旧定位结果。

质量门槛为至少 80 个有效匹配点、内点比例不低于 0.55、内点残差 RMSE 不超过 0.18 m；这些是匹配一致性的门槛，不是物理定位误差或“保证 20 cm 准确率”。候选搜索后须用随后独立采集的一帧扫描复核，并验证本次唯一请求编号、报告时间、地图路径及最终位姿。最后网页读取连续 5 帧新鲜 TF，检查平移变化不超过 3 cm、朝向变化不超过约 2.9°、与该次独立核验位姿一致，并确认最终结果仍在 1 m/±45° 先验内。静止稳定不等于位置正确，仍须操作员对照现场墙体、障碍物及实际朝向验收。

成功后黄色箭头保留粗先验，绿色箭头显示最终精配位姿，并展示两者的位置/朝向修正量；修正量不是定位精度。初始化快照不是实时位置，人工成功记录只在同一地图、本次初始化之后且 5 分钟内恢复显示；实时坐标使用“实时地图”读取。仅成功的精配位姿会写入 `config/relocalization_seed.json`，标记 `localization_mode: precise_confirmed`，不把粗略点击位置当作已确认坐标。失败不更新缓存，不自动行走或使能；原有导航到点约 20 cm 的验收标准不变。

初始化与任务、点位采集、两种重定位互斥，重复请求拒绝。原有部分服务、重复进程、旧控制器、活动或未知导航状态不会被强制重启/终止。新启动 PID 记录实际脚本、用户、网卡和进程启动时间；禁用运动前核对 ROS 控制节点的真实 PID。初始化总等待有界，“停止全部任务”取消初始化检查及重定位客户端，但不关闭已启动的持久服务；不承诺撤回已经提交的 ICP。日志在 `run/logs/robot_initialization.log`，新服务日志为 `init_navigation.log`、`init_safe_controller.log`。

软件流程及 HTTP/地图/界面回归分别见 `test_guide_initialization.py`、`test_initialization_web.py`、`test_guide_map.py`、`test_guide_web.py`；有界匹配与请求报告校验见 `test_coarse_registration_policy.py`、`test_coarse_relocalization.py`、`test_guide_relocalization.py`。测试不启动 ROS/DDS、不使能、不播报；任意新位置的地图点选配准仍需操作员实机验证。当前算法是给定 1 m/±45° 先验内的有界多候选 ICP，不是全场自动定位。

部署与首次验收：PC2 上须先编译更新后的 `localizer_node`，再在自动运动已禁用、机器人停稳的条件下重新加载定位模块，才会提供 `/slam_reloc_coarse`、`/slam_reloc_base`。只有复制网页文件或重启网页服务器不会加载新 C++ 服务；缺少新服务时网页会失败并提示重启，不回退到旧 `/slam_reloc`。新接口保留旧接口契约，同时明确标准 `map→base_link` 朝向。模拟与回归测试不能证明首次实机配准正确；新服务的朝向、候选匹配和独立扫描流程仍需操作员现场验收。全程不自动使能、行走、播报，失败不放宽门槛。

## 网页实时 2D 地图

首页“实时地图”进入 `/map`。底图按原地图的占用阈值简化为墙体、自由区域和未知区域，保留真实栅格坐标，不生成或修改地图文件。叠加已保存导览点、机器人当前位置与朝向、实时激光扫描点及局部代价地图的致命障碍栅格；不把膨胀区域画成墙。支持缩放、平移、机器人居中和跟随。页面点击/拖动只改变视图，不是导航或重定位指令。

局部代价地图仅绘制 OccupancyGrid 值 `100`；`99` 是 Noetic 的内切膨胀成本，不表示物理墙体。映射依据 [ROS Navigation 官方发布器源码](https://github.com/ros-planning/navigation/blob/noetic-devel/costmap_2d/src/costmap_2d_publisher.cpp)。

地图位置读取允许机器人移动，不使用“采集导览点须静止”的检查。只有 `/slam_reloc_check` 为 True 且检查结果及 TF 新鲜才绘制当前位置；未定位、过期、地图指纹不匹配或断线时隐藏位置与动态障碍，明确显示状态，不能将原点或上次成功记录冒充实时位置。扫描使用采样时刻的完整 TF 投影，代价地图使用其帧和原点姿态；均按地图 YAML 原点/旋转和 PGM 纵轴方向绘制。

多个浏览器共享一个 PC2 系统 Python 只读 ROS 订阅器，网页以约 500 ms 读取缓存，离开地图页/后台暂停轮询；无客户端读取一段时间后回收订阅器。只读取 TF、`/scan`、`/move_base/local_costmap/costmap` 和定位检查服务，不调用运动使能、停止、清图、重定位或导航接口。这个显示功能不会开启摄像头，也不是在线重新建图或永久地图优化；真正的避障仍由现有导航链路独立处理。

## 展点先验驱动的 Agent v1

上传的位姿、展板照片和已人工核对的旧摘要作为该点先验。旧 `speech` 字段保留原数据，但用途是事实摘要，不是固定播放台词。执行新展点导览时，规划仅携带点位 ID 与主题，不生成或缓存要播报的台词：

`check_status → navigate_to_point → verify_arrival → present_point（conception → 再核验 → presentation）`

Omni 先查询已登记点位和该点知识，不能猜坐标或把不同展板混为一谈。每次 conception 使用当前点先验及其展板照片，输出 1–4 段、每段最多 150 字、总计最多 450 字，以及来源 ID、疑点和审核标记。来源只能指向本点已审核资料或本次真正提供的展板照片；文献分块引用亦可。存在疑点、无来源、未到达或构思后被人工移动时，阻止播报。模型引用校验不等于内容绝对正确，正式讲解前仍应人工核对。

新模块只读取原 `config/guide_points.json`，额外资料保存在 `config/guide_knowledge.json` 与 `config/guide_documents/`。展板识别证据可按原图 SHA256 版本化保存，未审核候选不能覆盖已审核先验。现有展点及照片不重写、不删改。

文献上传每份不超过 2 MiB，每点最多 20 份，支持 UTF-8 TXT/Markdown（正文最多 20000 字）和 PDF。TXT/Markdown 只有显式人工审核后才参与讲解，输出可引用资料 ID 或分块 ID。PDF 当前只保存原件并标记 `pending_extraction`，无论是否勾审核都不能当作已解析事实。文献和图片内容均是资料，不是可信机器人工具指令。大型资料超过 conception 上限时明确拒绝，不静默截断后声称已完整阅读。

| 接口 | 输入 | 输出及边界 |
| --- | --- | --- |
| `GET /api/agent/points` | 无 | 已保存点位、先验准备状态、文献数、观测策略与点位导航验证门 |
| `POST /api/agent/prepare` | `point_ids`（1–2 个）、`topic` | 新展点任务预览；不移动、不播报，模拟也不调用机器人 |
| `POST /api/agent/conceive` | `point_id`、`topic` | 动态 `conception` 预览；不声称实机已到达、不保存为固定稿、不播报 |
| `GET /api/points/<id>/knowledge` | 无 | 当前点先验、候选、资料来源和文献状态 |
| `POST /api/points/<id>/documents` | `filename`、`file_base64`、`reviewed`、`pin` | 绑定该点的文献原件与提取状态；不执行文献中的指令 |
| `GET /api/points/<id>/documents/<doc_id>` | 无 | 下载该点文献；拒绝跨点与任意路径 |

单任务最多 2 个点，导航/核验/讲解预算分别为 180/20/240 秒，总预算仍不超过 900 秒。当前 8 个点均可选择，不自动启动全场长任务。新点位导航默认被 `GUIDE_NAMED_NAV_VERIFIED` 安全门关闭：接口接入和模拟通过不代表实机验收，首个点位导航必须由操作员现场确认后测试。该门与现有示教路线、网页运动开关和 PIN 独立。

首次人工验收入口仅在 PC2 SSH 命令行：给 `scripts/navigate_to_point_safe.py --point-id <已登记ID>` 增加 `--commissioning-confirmed`，表示操作员已经确认双脚落地、官方运动模式、吊绳拆除且持遥控器现场监护。该选项仅授权本次单点测试，不设置 `GUIDE_NAMED_NAV_VERIFIED`，网页和 Omni 仍无法绕过验证门。仍须通过全部定位/地图/扫描/空闲检查和目标路径确认才能使能，完成或异常后禁用运动并取消自身目标；`Ctrl+C` 会走相同清理。软件成功还须现场确认到达与朝向，单点成功也不能代表其余所有点或路线已验收。

导览提速档：2026-10-03 按操作员要求，TEB 与安全控制器上限从直行 0.30 m/s、转向 0.45 rad/s 同步调至 0.60 m/s、0.70 rad/s；加速度仍为 0.30 m/s²、0.50 rad/s²，倒车上限 0.05 m/s、横移为零，避障距离、到达容差和 0.30 秒 watchdog 不变。上限不是恒定行驶速度，避障和接近终点时仍会减速，新速度档须现场验收。控制器只在启动时读取 ROS 私有参数；已有节点必须在停稳、禁用运动后受控重启，保持默认 DISARMED。TEB 运行值需用动态重配置服务更新，不能只改参数服务器后声称规划器已生效。不必重启定位、雷达或更改地图；下次完整 initiate 会从新文件启动。

行动完成后，先确认禁用运动，再用最多 8 秒（同时受导航总时限约束）等待稳定采样；仅对“机器人或定位仍在移动”重试，不放宽 3 cm/0.05 rad 的采样稳定门、0.20 m/0.20 rad 的到达门。定位失效、TF 过期或地图变化立即失败。该等待不能修复错误定位，未通过核验也不能记为已到达。

TF 接收维护：固定 `body→base_link` 保留原有平移与 yaw/pitch/roll（0,0,0; 3.1416,3.1416,0），改为 `tf2_ros` 的 `/tf_static` 锁存发布，不再每 1 ms 重复发送动态 TF。导航稳定采样复用自身监听器；定位服务仅缓存类型/连接对象，不缓存状态布尔值。扫描读取前后均核验实时定位，读取后重新获取最新 TF，避免用读取前的旧快照误判；TF 真正停更仍按 1.5 秒门限拒绝。仅修改 launch 文件不会改变已经运行的固定发布器。

从动态 TF 切换到静态 TF 时，不能只替换发布器：Noetic 的已有监听器可能保留原来的动态缓存类型（参见 [BufferCore 实现](https://github.com/ros/geometry2/blob/noetic-devel/tf2/src/buffer_core.cpp)）。2026-10-03 现场热替换后，旧点云转换和 costmap 节点持续出现外推错误；点云转换忽略失败返回值而发出空消息，扫描节点随后因缺少 `x` 字段退出。`body2any_pointcloud` 现检查等待 TF 和 PCL 转换的布尔返回值，失败时限频告警并丢弃该帧，不发布空结果或重写消息时间掩盖失效。

这类切换须在操作员确认停稳、无活动任务、自动运动已禁用时，核验服务归属后完整重启本项目 ROS 导航、安全控制器和网站的只读监听器；同时检查旧子节点已退出，不使用旧 `nav_start.sh`，不强杀身份未知的进程。随后用固定初始化器检查真实传感器消息，而不是只看 ROS 节点登记。PC2 已编译加载修复并通过初始化，`/scan` 样本年龄约 0.08 秒；软件重启清除了定位状态，需要人工重新给出粗先验并核对精配箭头，自动运动仍禁用。该恢复不覆盖地图或导览点，也不能据此声称所有独立扫描超时已永久消除。

单点人工验收可另加 `--present-after-arrival`：导航前只读确认本机网页和 Omni 已配置、没有执行中的任务；随后通过 `POST /api/agent/prefetch` 按目标点和主题为本次行程异步构思，生成期间不播报、不调用 ROS 或运动接口。每次行程重新调用 Omni，不保存为固定台词。只有导航到达核验通过、运动禁用且自身目标清理完成后，才向本机网页提交一个 `present_point` 任务，网页导航验证门不被开放。

预构思的一次性票据在服务器内存中绑定点位、主题、地图指纹和完整先验上下文 SHA256（位姿、照片、文献、审核版本），最多 4 个、同时仅 1 个模型请求、240 秒有效。票据通过 `/api/plans/prepare` 的内部 `presentation_ticket` 元数据绑定唯一单步讲解计划，不扩展 `present_point` 的 `point_id/topic` 动作参数，也不接收客户端替代台词。执行保留“到达核验 → 消费本次预构思并复核资料 → 再核验 → 分段 TTS”；未准备完只在剩余预算内等待。生成前后、消费时以及每段播报前均检查资料版本，失效、待审核或引用非法结果不播报。导航失败/取消会取消票据；初始化、重定位、停止和网页重启均使旧票据失效。`POST /api/agent/prefetch/cancel` 仅取消指定票据的计算。

返回的 `presentation.status=submitted` 只代表已提交，不代表构思或朗读成功；最终结果请在网页任务状态查看。导航失败不会提交讲解；讲解提交失败会单独报告，并保持运动禁用。该选项不可与 `--check-only` / `--verify-only` 混用。普通网页无票据的讲解仍在到点后构思，尚未自动为所有多点任务预取。

延迟诊断记录到达核验、构思等待、播报前核验，以及音频排队时间/API 请求时间。`len(text)*0.30+1.5` 是异步 SDK 播放后的保守串行等待，不是声音语速；本次优化缩短的是到点至首句的空白，不添加未经官方接口验收的语速字段，也不以缩短播放等待制造语音重叠。

只读预检：在 PC2 source ROS 和工作空间后，运行 `/usr/bin/python3 scripts/navigate_to_point_safe.py --point-id <已登记ID> --check-only`。不发布目标、不取消、不改变运动使能；不会调用 Noetic 默认会清除部分 costmap 的 `make_plan`，因此不能凭该预检声称路径可达。`--verify-only` 只核验在点停稳，不运动。

未来的现场识别/观测调整保留独立策略入口 `get_observation_policy`，当前 `enabled=false`。摄像头接入、展板关联、定位新鲜、碰撞检查和操作员任务授权完成验收前，不自动转身或靠近。拟议范围为平移 ≤0.30 m、转角 ≤0.35 rad、最多 2 次，尚不是已运行能力。现场识别仅作为当前点的候选补充；与先验矛盾时提示核对，不覆盖先验或修改导航基图。

无硬件回归：`test_guide_knowledge.py`、`test_guide_conception.py`、`test_point_navigation.py`、`test_agent_web.py`，以及现有 task/omni/web 测试。测试使用临时文件、模拟模型与 ROS，不触发实机运动或朗读。

## PC2 安装

在 PC2 的 `~/robot/DaoLan` 目录运行：

```bash
bash scripts/omni_setup.sh
chmod 600 run/config/omni.env
```

运行 `python3 scripts/omni_configure.py` 隐藏输入百炼 `DASHSCOPE_API_KEY`，不需要 nano；已有 Key 无需重填。默认模型是 `qwen3.5-omni-flash-realtime`，默认 WebSocket 地址为 `wss://dashscope.aliyuncs.com/api-ws/v1/realtime`。若模型要求绑定业务空间，须把 `OMNI_WS_URL` 改成相应地址。密钥只留在 PC2 的 0600 文件里，不提交或发到聊天中。

```bash
bash scripts/mobile_guide_stop.sh
bash scripts/mobile_guide_start.sh
curl -s http://127.0.0.1:8765/api/health
```

手机打开 `http://192.168.3.64:8765`。先测试“讲解任务演示”和“往返导览 → 模拟执行”；模拟不会让机器人动。确认语音、定位、避障和路线都就绪后，操作员用 `python3 scripts/omni_configure.py --motion on` 隐藏设置 PIN，再重启网页服务。`--motion off` 关闭网页运动及手臂权限。不要在公网或不受控网络开放此端口；目前是可信局域网联调版本，不是公网认证服务。

手臂只查询动作列表，不运动：

```bash
source scripts/env.sh
"$CONTROL_PYTHON" scripts/guide_arm_action.py --list --interface "$CONTROL_IFACE"
```

只有操作员现场确认机器人姿态、人员与手臂空间并准备好遥控器，才执行单项实机测试；示例会实际挥手并释放手臂：

```bash
"$CONTROL_PYTHON" scripts/guide_arm_action.py wave --test --interface "$CONTROL_IFACE"
# 现场确认成功后登记；登记本身不证明验证通过，也不会执行动作
python3 scripts/omni_configure.py --verified-arm wave
bash scripts/mobile_guide_stop.sh
bash scripts/mobile_guide_start.sh
```

未验证的握手、拍手、比心等不能因接口存在就默认启用。软件回归（不运动）运行 `python3 scripts/test_guide_tasks.py`。

Omni 工具循环回归（无网络、无运动）运行 `python3 scripts/test_omni_planning.py`。规划最多 8 轮，保留 55 秒响应预算；合法完整计划立即返回待确认预览，不再额外调用模型生成总结。达到边界仍不合法时明确失败，失败工具名称和校验原因写入网页服务日志，不打印密钥。

当前 `omni_client.py` 每次请求建立一次 Omni 会话，适合按次问答和功能联调；连续双工语音和长期对话历史需后续改为持久连接。照片采用 JPEG 单帧，录音文件最长 30 秒。没有密钥时网页、原有小智和直接朗读继续可用，Omni 返回明确的未配置状态。

## 本轮联调记录（2026-10-02）

- 本机及 PC2 Python 3.8 的 9 项无硬件回归通过：参数与权限边界、模拟隔离、顺序/持久化、失败跳过、取消/互斥、重启不续跑、子进程超时。
- PC2 HTTP 验证通过：七步往返模拟、一次性确认 token、运动关闭时拒绝执行。
- 真实 Omni 已返回标准讲解计划；不合法参数先拒绝，模型按错误提示重试通过。工具调用及参数随响应返回，可检查是否真的调用了工具。
- 三步“欢迎播报 → 等待 2 秒 → 结束播报”任务已在 PC2 成功；两次 TTS 请求得到 SDK 接受。完成判据是估算播放等待，不是麦克风回采或实际听感确认。
- 只读查询官方手臂动作列表成功（SDK code 0），没有执行手臂动作。网页运动、手臂动作仍关闭；这轮换电后没有重新验证定位与运动。
- 后续落地联调：重定位成功，位置约 `(4.94,-1.10)`，初始配准与新种子相差约 6 cm，保留 20 cm 限制；激光约 10 Hz、局部代价地图新鲜。
- 修复网页子进程在无 ROS shell 环境时的 `ROS_DISTRO: unbound variable`；真实 `/api/stop` 已得到控制器停止服务确认。路线 TF 预检改为有限查询，避免正常 `tf_echo` 被超时命令误判失败。
- `bash scripts/route_demo.sh end --check-only` 在清除 ROS 环境变量后通过，只检查节点、定位、TF、激光并禁用运动，不发送目标；不是实际行走验证。网页运动权限随后由操作员配置开启，手臂白名单仍关闭。
- 工具轮次边界回归新增 5 项：有效计划无需总结轮、最后一轮合法计划不会丢失、超限保留诊断且不假成功、普通问答兼容、非法 shell 动作仍拒绝。
- 原嵌套工具参数在真实请求中出现过反复只有标题、遗漏步骤；改用浅层 JSON 字符串传输，新增 6 项传输契约回归，验证旧格式兼容、严格解析、长度和动作白名单不能绕过。
- 浅层传输部署后，本机与 PC2 共 20 项无硬件回归通过；3 种真实往返规划请求均直接生成完整五步预览（含 end→start），无参数校验重试。全部保持未确认，没有启动路线。
- 导览点记录与展板资料模块部署：新增 16 项点位/HTTP 回归及 2 项展板工具隔离回归，总计 38 项在本机和 PC2 通过。实机只读 `map → base_link` 位姿采集通过；真实 Omni 接受测试 JPEG 并返回草稿和人工核对提示。测试未保存正式导览点，没有触发运动或播报。真实展板的识别准确度仍需操作员现场核对。

## Agent v1 联调记录（2026-10-03）

- 本机 185 项隔离回归全部通过；PC2 Python 3.8 同一套回归通过，其中 2 项可选 Node 浏览器测试因 PC2 无 Node 跳过，本机已通过。测试未调用真实运动或朗读。
- `/agent` 与 `/knowledge` 已部署；8 个登记点位均可读取。真实 Omni 已基于一个实际展点的旧摘要和原展板生成 3 段动态讲解，返回本点摘要/照片来源 ID。这次只预览，不播报；内容准确度仍需人工对照展板核对。
- 真实 HTTP 新展点预览返回 `check_status → navigate_to_point → verify_arrival → present_point`，不包含固定解说词。没有确认或执行机器人任务。
- 点位自主导航验证门与摄像头观测策略均保持关闭，未声称新点间实机导航或主动观察已验收。已有示教路线不改动。
- 点位登记、7 张展板原件、二维/三维地图、关键位姿和示教路线 SHA256 全部保持一致。更新前备份位于 PC2 `run/backups/agent_prior_v1_20261003_yp4mPg/`。

## 官方依据

- [阿里云 Omni Realtime 接入与音频/图像协议](https://help.aliyun.com/zh/model-studio/realtime)
- [阿里云 Omni 示例项目](https://github.com/aliyun/alibabacloud-bailian-speech-demo)
