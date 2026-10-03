# 故障诊断

先停止并保持运动禁用，再做只读检查；不要以扩大容差或无限重试消除报错。

| 现象 | 原因线索与处理 |
| --- | --- |
| 本机找不到 rostopic | ROS1 命令应在 PC2 且 source Noetic/workspace；新电脑 ROS2 配置不能代替 PC2 Noetic |
| ament_cmake_auto 缺失 | Livox 驱动进入 ROS2 分支；确认 `-DROS_EDITION=ROS1`，不是盲装 ROS2 |
| 编译找不到 cpp | 检查源文件是否完整；当前仓库含项目适配，不能只改 CMake 删除节点 |
| ROS 源 EXPKEYSIG | 查真实 apt 源文件与当前官方签名配置；不关闭签名、不盲移不存在的文件 |
| RViz/VTK 黑屏 | 区分图形环境、Fixed Frame、PointCloud2 topic 和数据；PCD 转 PLY 后本机 CloudCompare 是已验证替代 |
| no map received | Map 显示接收二维 OccupancyGrid；三维点云用 PointCloud2，确认类型/话题而非文件名 |
| costmap no new messages | 查节点是否存活、TF、更新/发布频率、costmap_updates、always_send_full_costmap；话题注册不是实时发布证明 |
| 重复节点 shutdown | 相同名字重复启动；核实唯一归属，不反复 launch 或杀全部 ROS |
| TF 过期 / 时间异常 | 检查共同 TF 时间、系统时间、CPU 争用、定位输出和原始消息；保留 .5 s 代价地图上限，不重打时间戳 |
| independent_scan_timeout | 独立扫描核验未取得合格新扫描；失败保持禁用，诊断雷达/scan/时间链 |
| 定位 True 但箭头错误 | 可能相似解或初始朝向不合适；人工核对墙体/朝向，不能凭布尔值放行 |
| 机器人或定位仍在移动 | 可能实际移动、支撑变化或定位漂移；停稳后检查短时位姿，不直接重新保存覆盖点位 |
| 未收到本目标有效路径 | 看 planner、TF、目标/map 指纹、障碍与目标可达性；拒绝使能是安全行为 |
| 只旋转或速度慢 | 对照 cmd_vel→smooth→安全控制→navigation_odom，查 TEB、EMA、反馈/限幅/余距；.6/.7 是上限不是 SDK 高速模式 |
| smooth 为零 | 可能未发目标、原始指令过期、平滑器配置或任务完成；先看上游，不用直接 pub 速度绕过导航 |
| 没声音且录音峰值为零 | 录到静音；机器人播放成功不能修复输入。采用文字/手机输入，Unitree DDS TTS 而非 PC2 PulseAudio 输出 |
| Omni 轮次超限/图片整理失败 | 查看工具校验、模型/业务空间、Key 配置和分块任务状态；不把失败输出当可信讲解 |
| 网页运动未开放 | 区分 motion 门、PIN、named-navigation 门；开放网站权限不等于控制器已 armed |

只读快速检查（PC2）：

```bash
source /opt/ros/noetic/setup.bash
source ~/robot/DaoLan/G1Nav2D/devel/setup.bash
rosnode list
rosservice call /slam_reloc_check 'code: true'
timeout 5 rostopic hz /scan
timeout 5 rosrun tf tf_echo map base_link
rostopic info /cmd_vel_smooth
ps -C move_base -o pid,%cpu,%mem,etime,cmd
```

`timeout` 正常结束通常返回非零，不代表此前没有有效数据。保留终端输出与对应任务 ID；日志在 `run/logs/` 和 `run/tasks/`。可以使用 `diagnose_navigation_latency.py` 做有界采样；先看 `--help`，不把诊断脚本当运动指令。已保存地图查看时 `pcl_pcd2ply` 转换与 `scp` 传输是两个不同步骤，哈希校验一致后再判断显示问题。
