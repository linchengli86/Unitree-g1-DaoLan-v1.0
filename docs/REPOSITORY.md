# Git 仓库与私有数据交付

## 公开发布许可门槛

2026-10-04 源码许可审查尚未通过：三个本地包缺少明确授权证据，FastLIO / ikd-Tree 需追溯许可范围，自有代码也尚未确定项目许可。详见 [审查证据与检查表](THIRD_PARTY.md)。不要在创建 GitHub 仓库时自动选择 MIT 来覆盖整个源码树；先完成来源、授权、适用许可及资料审查。

此次文档更新不构成授权补齐，也未自动提交、推送或重建 bundle。已有 bundle 是旧快照，不能据其宣称包含当前工作树的审查记录；发布时需包含审核后的提交，并明确交付版本。

## 仓库边界

当前交付位置为 `/media/robot2/49EB5B0A321ABC03/git_HM`：`dist/DaoLan` 是真实 Git 仓库，外层 README/docs 是阅读副本。`main` 保留已验证导航基线；`feat/web-onboarding` 为新增网页建图候选分支，尚待 PC2 编译和新图验收。`DaoLan-web-onboarding-candidate.bundle` 保存两条分支及历史，不覆盖旧 bundle。

```bash
git clone -b feat/web-onboarding /路径/DaoLan-web-onboarding-candidate.bundle DaoLan
cd DaoLan
bash scripts/quickstart.sh --check
```

以上 check 只在支持的 PC2 检查；实际准备和操作见 Quickstart。原 `EXPORT_MANIFEST.json` 是初始快照清单，后续提交以 Git 对象/提交和 bundle 校验为准，不应将初始 SHA256 当作当前树清单。

当前工作目录的 `.git` 是环境只读占位目录，不是已有版本库。为避免覆盖它，导出脚本在临时目录创建真实 Git 仓库，将审核后的源码快照提交到 `main`，生成 `dist/DaoLan.bundle`。该 bundle 是本地 Git 仓库交付物，不是已推送的 GitHub 仓库。

```bash
python3 scripts/export_repository.py --output dist/DaoLan.bundle
# 在任意新的、尚不存在的工作目录克隆
git clone /绝对路径/DaoLan.bundle DaoLan
cd DaoLan
git log --oneline
```

导出提交使用明确的快照机器人身份，不伪造历史作者。`EXPORT_MANIFEST.json` 包含导出时间、文件 SHA256 和排除策略；bundle 经 verify，可用 clone/fsck 再验证。若要发布远端，由用户提供目标和发布权限后设置 remote/push；本次不创建远端、不上传。

## 纳入与排除

纳入根 README、Git 规则、`docs/`、`scripts/`、ROS 源码/配置、Livox SDK 和 Unitree SDK 源码及已有许可证。使用 vendored 快照而非虚构版本号的 submodule；保留本地适配。

默认排除：`run/`、`config/`、`map/`、`routes/`、PCD/关键位姿、照片/文献现场资料、捕获日志、构建目录、虚拟环境、缓存、编辑器数据库、密钥和旧部署配置；`PythonProject/` 历史语音实验整体保留本地但不导出。当前网站/Omni/TTS 代码在 `scripts/`，旧小智 `/api/assistant` 兼容入口若要运行需另行部署旧应用。默认导出因此是**当前活跃系统源码**，不是原硬盘镜像。

导出前检查禁止路径、符号链接、文件大小和明显 Key/私钥特征，发现可疑内容只报告路径而不打印秘密。检查不是全面 DLP 保证；公开发布前需人工审核许可证、配置和资料。`.gitignore` 不会自动取消已跟踪文件，后续发布前也要检查 `git ls-files`。

## 现场资产另行交付

私有部署包至少包含：

- 一致版本的二维地图及相关 JSON、三维/地面 PCD、关键位姿。
- 点位登记 JSON、关联照片、审核文献、必要示教路线。
- 本地权限 600 的配置（通过受控渠道，Key/PIN 不放源码仓库）。

地图与登记点必须匹配指纹。备份目录 `map/backups/`、`run/backups/` 可能同时含场地数据和秘密，不可公开上传。建议记录文件相对路径、大小、SHA256、地图版本和验收日期，源码包与资产包分别标识版本。

## 开发与验收

每次变更记录源码差异及验证范围：无硬件回归 → PC2 隔离测试 → 局域网预览/模拟 → 操作员实机验收。部署时只同步本次源文件，不覆盖现场数据；不要把本文档化操作当作后台运动授权。提交禁止包含真实 PIN、Key、录音或展板原件。
