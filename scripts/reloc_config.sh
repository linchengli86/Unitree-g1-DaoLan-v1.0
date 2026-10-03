#!/usr/bin/env bash
# 自动重定位配置（可被环境变量覆盖）
# 引入顺序：env.sh -> reloc_config.sh
# 供 auto_reloc.sh / reloc_from_point.sh / start_all.sh 使用

# 是否启用自动重定位 (1 启用 / 0 关闭)
# 默认已开启；如需关闭可在命令行使用 start_all.sh --no-auto-reloc 或 export AUTO_RELOC=0
export AUTO_RELOC=${AUTO_RELOC:-1}

# 地图 PCD 路径（建议使用绝对路径或相对 NAV_WS 的路径）
export RELOC_PCD_PATH=${RELOC_PCD_PATH:-$NAV_WS/src/fastlio2/PCD/map.pcd}

# 初始粗定位位姿 (单位: 米 / 弧度)；朝向固定 0，由 ICP yaw_offset 自动搜
export RELOC_X=${RELOC_X:-0.0}
export RELOC_Y=${RELOC_Y:-0.0}
export RELOC_Z=${RELOC_Z:-0.0}
export RELOC_ROLL=${RELOC_ROLL:-0.0}
export RELOC_PITCH=${RELOC_PITCH:-0.0}
export RELOC_YAW=${RELOC_YAW:-0.0}

# 重定位等待导航节点与服务的最长秒数
export RELOC_WAIT_TIMEOUT=${RELOC_WAIT_TIMEOUT:-60}

# 调用 /slam_reloc 后轮询 /slam_reloc_check 成功的最长秒数
export RELOC_CHECK_TIMEOUT=${RELOC_CHECK_TIMEOUT:-30}

# 检查间隔
export RELOC_CHECK_INTERVAL=${RELOC_CHECK_INTERVAL:-1}

# 失败是否退出 (1: auto_reloc.sh 退出码非0; 0: 打印警告继续)
export RELOC_FAIL_STRICT=${RELOC_FAIL_STRICT:-0}

# ---------------- 启动自动：从原点逐步扩大，超过 max 停止 ----------------
export RELOC_SWEEP_ENABLE=${RELOC_SWEEP_ENABLE:-1}

# 最大搜索半径（米）；超过则停止（可网页点选继续）
export RELOC_MAX_RADIUS=${RELOC_MAX_RADIUS:-5.0}

# 半径阶梯（米），会过滤掉 > RELOC_MAX_RADIUS 的值
# G1 原候选到 3m，现扩展到 5m 上限
export RELOC_RADIUS_CANDIDATES="${RELOC_RADIUS_CANDIDATES:-0 0.5 1.0 1.5 2.0 3.0 4.0 5.0}"

# 脚本层不再扫多 yaw（ICP 已开 yaw_offset）；保留变量兼容旧调用
export RELOC_YAW_CANDIDATES="${RELOC_YAW_CANDIDATES:-0}"

# 单次 /slam_reloc 后等待校验超时（秒）
export RELOC_PER_TRY_TIMEOUT=${RELOC_PER_TRY_TIMEOUT:-22}

# 单次尝试间隔（秒）：给上一轮 ICP/服务收尾留余量，又不过分拉长整轮扫描
export RELOC_TRY_INTERVAL=${RELOC_TRY_INTERVAL:-2}

