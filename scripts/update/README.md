# 更新脚本

将本目录复制到 Ubuntu 的 `/home/ztx/robot/DaoLan/scripts/update/`，打包机和客户机均需此目录。

## 文件说明

- `pack_on_ubuntu.sh` - 打包脚本，仅在**打包机**（最新 Ubuntu）执行
- `do_update.sh` - 更新脚本，在**客户机**执行
- `run_update_choose_desktop.sh` - 桌面快捷方式入口（选择服务器/本地包）
- `run_update_desktop.sh` - 从服务器更新
- `run_update_local_desktop.sh` - 使用本地包更新
- `install_desktop_shortcut.sh` - 安装桌面快捷方式
- `update_config.json` - 配置（版本 API、排除规则等）

## 首次使用

```bash
chmod +x pack_on_ubuntu.sh do_update.sh run_update_desktop.sh run_update_local_desktop.sh run_update_choose_desktop.sh install_desktop_shortcut.sh
```

## 桌面快捷方式（推荐给非技术用户）

在客户机执行一次安装脚本，之后可从桌面或应用菜单双击运行更新，无需输入命令：

```bash
cd /home/ztx/robot/DaoLan/scripts/update
./install_desktop_shortcut.sh
```

安装后会创建「DaoLan 系统更新」快捷方式，双击即可运行。首次会提示选择「从服务器下载更新」或「使用本地包更新」，选择本地包时会弹出文件选择框。首次会提示输入管理员密码，之后在有效期内不再重复提示。若桌面图标显示「未信任」，可右键 -> 允许启动。

## 打包（打包机）

```bash
cd /home/ztx/robot/DaoLan/scripts/update
./pack_on_ubuntu.sh --full --version 1.0.0
# 输出: ~/update_packages/update_v1.0.0.zip
```

## 更新（客户机）

```bash
cd /home/ztx/robot/DaoLan/scripts/update

# 从服务器下载并更新全部
./do_update.sh --all

# 使用本地包更新全部
./do_update.sh --local /path/to/update_v1.0.0.zip --all

# 仅更新前端
./do_update.sh --frontend

# 使用本地包仅更新 DaoLan
./do_update.sh --local /path/to/update_v1.0.0.zip --daolan

# 跳过版本校验
./do_update.sh --all --force
```

## 配置

更新脚本通过版本管理 API 动态获取下载地址，修改 `update_config.json` 中的：

- `version_api_base` - 版本 API 基础地址（末尾含 `/`），例如：`http://8.156.78.182:9999/g1-manager/service/versionManager/`
- `version_id` - 当前系统对应的更新版本 ID，API 会返回该版本的 `packagePath` 作为下载地址
- `sudo_password` - 可选，填写后桌面快捷方式更新时免输入 sudo 密码（存在安全风险，仅限受控环境）

API 返回需满足：`code==200`、`status=="0"`，`packagePath` 为实际 zip 下载 URL。

也可通过环境变量覆盖：`VERSION_API_BASE`、`VERSION_ID`。

## AI 语音助手（omni）

当前 DaoLan 的新 Omni 网页适配器位于 `DaoLan/scripts/omni_client.py`，与
`mobile_guide_server.py` 一起由 `--daolan` 包下发；密钥保存在运行期
`run/config/omni.env`，初始化运行 `bash scripts/omni_setup.sh`。能力与接口见
`DaoLan/docs/OMNI_GUIDE.md`。下述独立 `--omni` 包是旧机器的定制部署流程，
依赖额外的 `start_omni.sh` 和 `robot.json`；在没有那份旧源码时不要选择它。

路径：`/home/ztx/robot_program/alibabacloud-bailian-speech-demo`  
（`alibabacloud-bailian-speech-demo` 可能尚不存在，由 `--omni` 更新解压创建）

| 组件 | 行为 |
|------|------|
| `omni` | 整树下发到 `robot_program/`；**不打包** `robot.json`（防 api_key）与 `venv/`；动作表运行时读机载 `unitree_sdk2/build2/bin/actionOptions.json` |

```bash
# 打包机：仅打包 omni
./pack_on_ubuntu.sh --select --omni --version 1.0.1

# 全量打包（含 omni）
./pack_on_ubuntu.sh --full --version 1.0.1

# 客户机：仅更新 omni
./do_update.sh --local /path/to/update_v1.0.1.zip --omni

# 验证
cd /home/ztx/robot_program/alibabacloud-bailian-speech-demo/samples/conversation/omni/python
source venv/bin/activate
python -c 'import dashscope, cv2, pyaudio; print("ok")'
```

可指定建 venv 的解释器：`OMNI_PYTHON_BIN=/usr/local/python3.11/bin/python3.11 ./do_update.sh --local ... --omni`

