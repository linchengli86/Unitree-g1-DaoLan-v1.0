# Quickstart · 打开和调用 DaoLan 网站

## 版本能复现什么？

当前是可追溯的源码与测试基线，不是完整机器人系统镜像。

- Git 固定源码版本；初始基线提交为 `a6995b3`，后续文档更新以 `git rev-parse HEAD` 为准。
- 隔离软件测试曾在独立克隆中通过 500 项；PC2 的可选环境测试有跳过项。
- 完整实机复现还需 Ubuntu 20.04/ROS Noetic、对应架构 SDK/依赖、相同地图和登记点、私有配置及人工验收。
- 尚未锁定全部 apt/pip 包、系统镜像和每个上游版本；不能承诺任意新电脑 clone 后一键硬件复现。
- `EXPORT_MANIFEST.json` 记录初始源码导出快照，后续改动以 Git 历史为准；它不是当前 PC2 环境清单。

## A. 已有 PC2：最快打开网站

以下命令在操作电脑执行，替换为当前可达的 PC2 地址。不要求操作电脑安装 ROS。

```bash
ssh unitree@192.168.3.64 \
  'cd ~/robot/DaoLan && bash scripts/mobile_guide_start.sh'
```

网站已运行时不会重复启动。该命令启动软件网站/音频客户端，不发送导航目标、不播报，也不替你给传感器通电或切硬件模式。

浏览器打开：

```text
http://192.168.3.64:8765/
http://192.168.3.64:8765/navigation
```

若直连端口不通，但 SSH 可达，可另开终端保持 SSH 转发：

```bash
ssh -N -L 127.0.0.1:18765:127.0.0.1:8765 unitree@192.168.3.64
```

再打开 `http://127.0.0.1:18765/navigation`。按 Ctrl+C 关闭转发，**只关闭连接，不代表机器人任务停止**；有活动任务先在网页停止并确认。转发仅绑定本机，不要把 8765 直接暴露公网。

## B. 网站使用：仅移动

1. 换电后先“一键初始化”，等待资产、网络、控制器、导航和传感器检查完成。
2. 未定位时给大致位置/朝向；人工对照墙体和机器人朝向确认绿色配准箭头。
3. 打开“点位导航”，选择登记点。确认机器人已落地、官方运动模式、吊绳拆除、停稳且有人持遥控器监护。
4. 点击“准备前往此点”，核对预览；输入操作员 PIN 确认才会启动实机导航。

点位移动不调用 Omni、不排入语音队列；沿用 .60 m/s、.70 rad/s 上限，避障和接近终点会减速。到达/失败后禁用运动。首次前往其他点位仍须实机验收，紧急情况使用官方物理急停。

## C. API 调用：只读检查、预览与模拟

使用直连时设置 `http://192.168.3.64:8765`；使用上述转发时：

```bash
export DAOLAN_URL=http://127.0.0.1:18765
curl --noproxy '*' --fail --max-time 5 "$DAOLAN_URL/api/health"
curl --noproxy '*' --fail --max-time 5 "$DAOLAN_URL/api/actions"
curl --noproxy '*' --fail --max-time 5 "$DAOLAN_URL/api/points"
```

预期返回 JSON 的 `status: success`。health 仅证明网站响应；其中 `motion_enabled` 是网站权限，**不是控制器 armed 或实机就绪**。actions 的 `named_navigation_verified` 是点位导航模块门，`arm_enabled` 应保持 false。源码空部署可以没有点位。

下面 Python 示例只预览与模拟，不输入 PIN、不调用实机确认。先从 points 选取登记 ID，替换示例值。仅在无人使用网站、无任务/初始化/重定位时执行：准备新计划会替换网站当前待确认计划；模拟会留下任务记录。

```bash
# 该 ID 仅适用于原现场数据；新场地换成自己的登记 ID
export DAOLAN_POINT_ID=401bfe76daab47efbdd6996267b8e793
python3 - <<'PY'
import json, os, time, urllib.request
base = os.environ["DAOLAN_URL"].rstrip("/")
# Direct trusted-LAN/loopback access; do not send robot API calls through a proxy.
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
def api(path, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(base + path, data=data,
                                     headers={"Content-Type": "application/json"})
    with opener.open(request, timeout=10) as response:
        result = json.load(response)
    if result.get("status") != "success":
        raise RuntimeError(result.get("message", "API failed"))
    return result

for endpoint, key in (("/api/tasks/current", "task"),
                      ("/api/init", "initialization"),
                      ("/api/relocation", "relocation")):
    state = api(endpoint).get(key)
    if state and state.get("state") not in (
            "idle", "succeeded", "failed", "cancelled", "interrupted", "simulated"):
        raise RuntimeError("Website is busy; do not replace another operator's plan")
prepared = api("/api/navigation/prepare",
               {"point_id": os.environ["DAOLAN_POINT_ID"]})
print("PREVIEW:", json.dumps(prepared["plan"], ensure_ascii=False))
print("WARNINGS:", prepared.get("warnings", []))
submitted = api("/api/plans/dry-run", {"token": prepared["confirmation"]})
task_id = submitted["task"]["id"]
for _ in range(50):
    task = api("/api/tasks/current").get("task")
    if not task or task["id"] != task_id:
        raise RuntimeError("Task changed; stop inspecting this simulation")
    if task["state"] != "running":
        print("RESULT:", task["state"], task["message"])
        if task["state"] != "simulated" or task.get("dry_run") is not True:
            raise RuntimeError("Simulation did not finish successfully")
        break
    time.sleep(0.1)
else:
    raise TimeoutError("Simulation result timed out")
PY
```

正常结果是单步 `navigate_to_point` 预览和最终 `simulated`；不代表机器人可实机到达。运动门关闭时警告是正常保护，不需要为模拟开放运动。真实执行使用网页 PIN 流程；不要把 PIN 硬编码进 curl、脚本、命令行历史或仓库。

## D. 新电脑/新 PC2：从源码开始

真正源码仓库位于当前交付目录的 `dist/DaoLan/`，外层 README/docs 是阅读副本，不是部署根目录。原 bundle 也可克隆：

```bash
git clone /路径/DaoLan.bundle DaoLan
cd DaoLan
git rev-parse HEAD
/usr/bin/python3 -m unittest discover -s scripts -p 'test_*.py'
```

完整测试需可监听回环 HTTP；可选 Node/g++ 测试取决于环境。没有真实硬件也可运行隔离测试，但生产网站当前会初始化 Unitree AudioClient，并不是脱离 SDK 的纯静态演示服务。

部署到新 PC2 前，按 [DEPLOYMENT.md](DEPLOYMENT.md) 准备 Noetic/编译工具/SDK 和两个 Python 环境；在 PC2 构建 `G1Nav2D/build.sh`，配置内部网卡，另行恢复匹配的现场资产与点位。Omni 可选配置：

```bash
# 以下在 PC2 已安装 SDK/ROS 且源码目录正确时执行
cd ~/robot/DaoLan
bash scripts/omni_setup.sh
# 如需 Omni，隐藏输入 Key；不需要则跳过
python3 scripts/omni_configure.py
bash scripts/mobile_guide_start.sh
```

不要用这组命令覆盖旧地图/点位，或认为它安装好了所有依赖。默认模板运动门为 0；配置网站权限、PIN 和独立点位导航门仅在现场验收后进行，手臂保持关闭。Key/PIN 留在 PC2 `run/config/omni.env`（600），不进 Git。

## 快速排查

- SSH 可达、网页不可达：确认 PC2 网站启动、端口8765、日志 `run/logs/mobile_guide.log`，或使用 SSH 转发。
- health 成功但导航失败：检查初始化、真实定位、地图/点位指纹、scan、TF、本目标路径与权限；不绕过安全门。
- points 为空：源码包未包含场地登记数据；需另行交付，不是前端丢点。
- 操作电脑没有 rostopic：网站访问不需要 ROS；ROS 诊断在 PC2 source Noetic 后进行。
- 无 Omni：仅移动仍可用，但生产网站音频 SDK 初始化依赖仍存在。

更多内容：[部署](DEPLOYMENT.md) · [操作](OPERATIONS.md) · [API 与能力契约](OMNI_GUIDE.md) · [验证边界](VALIDATION.md)
