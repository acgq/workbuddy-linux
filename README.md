# WorkBuddy for Linux

腾讯官方 Linux 版本很久不更新，最新版的5.6.2一直cpu占用很高，重新将 macOS 版 WorkBuddy 移植到 Linux。

这是非官方移植项目，构建产物由 GitHub Actions 生成，可在 [Releases](https://github.com/acgq/workbuddy-linux/releases) 下载。

Arch Linux / Manjaro 可通过 AUR 安装：`yay -S workbuddy-cn-bin`（或 `paru -S workbuddy-cn-bin`）。包使用本仓库已验证的便携版，安装命令仍为 `workbuddy`，用户数据仍在 `~/.workbuddy`。

Actions 在 Release 发布成功后自动更新 AUR；每天检查更新时也会重试同步。维护者需将专用 AUR SSH 私钥保存为仓库 Actions secret `AUR_SSH_PRIVATE_KEY`，并将对应公钥添加到 AUR 账户。源码仓库不保存私钥。

也可检查官方下载链接[官方 Linux 版本检查更新接口](https://copilot.tencent.com/v2/update?platform=workbuddy-linux-x64-deb)

