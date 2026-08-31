# WorkBuddy for Debian/Ubuntu（非官方）

这是一个为腾讯 WorkBuddy 制作非官方 Linux `.deb` 安装包的自动化项目，适用于 Debian、Ubuntu 及其衍生发行版，支持 `amd64` 和 `arm64`。

腾讯目前没有发布官方 Linux 客户端。本项目参考 AUR 社区的 [`workbuddy`](https://aur.archlinux.org/packages/workbuddy) 配方：下载 WorkBuddy 官方 macOS 应用资源，将其中依赖平台的 `better-sqlite3` 和 `node-pty` 替换为 Linux 构建，并用打包在 `.deb` 内的 Electron runtime 启动。

## 安装

从 [Releases](../../releases) 下载与你的架构匹配的 `.deb`，然后运行：

```bash
sudo apt install ./workbuddy_*.deb
```

安装后可从应用菜单启动 WorkBuddy，也可以在终端运行 `workbuddy`。

## 自动构建与更新

GitHub Actions 每天查询 WorkBuddy 官方更新接口。检测到新版本后会构建两个架构的包，并创建 GitHub Release。也可以在 Actions 页手动运行 **Build and release Debian packages**；勾选 `force` 可覆盖重建已有版本的附件。

构建过程会计算下载文件的 SHA-256，并与官方更新接口提供的值比较。已观察到官方元数据的哈希与实际 ZIP 不一致（AUR 配方也因此对该文件使用 `SKIP`）；遇到这种情况，构建会明确警告并记录实际哈希，而不会把不准确的官方值伪装成已验证。最终 Release 同时提供每个 `.deb` 的校验文件。构建脚本不会把上游应用二进制提交到本仓库。

## 来源与用途

- 官方产品页：<https://www.workbuddy.cn/app>
- 官方更新接口：<https://copilot.tencent.com/v2/update?platform=workbuddy-darwin-x64>
- AUR 参考包：<https://aur.archlinux.org/packages/workbuddy>
- AUR Git 仓库：<https://aur.archlinux.org/workbuddy.git>

本项目的用途仅是为 Linux 用户提供兼容性打包和自动构建。这里的打包脚本采用 MIT License；WorkBuddy 应用、商标及其资源不属于本项目，权利归腾讯或相应权利人所有。本项目与腾讯无隶属、认可或官方支持关系，也没有为上游专有软件授予额外许可。公开分发前，请自行确认你所在地区及上游条款允许重新分发。

## 已知限制

- 这是从 macOS 资源移植的非官方版本，部分依赖 macOS 或腾讯内部原生模块的功能可能不可用。
- 上游界面更新可能使兼容补丁失效；遇到问题请附上发行版、架构和终端启动日志。
- Electron runtime 固定在构建脚本中的版本，升级后应重新验证登录、托盘、终端和文件操作。

## 本地构建

需要 Bash、curl、unzip、Node.js、npm 和 `dpkg-deb`。先从官方更新接口取得 `url` 和 `sha256hash`，然后运行：

```bash
VERSION=5.3.14.36279234_825709d4 \
SOURCE_URL='https://download.codebuddy.cn/…/WorkBuddy-darwin-x64-….zip' \
SOURCE_SHA256='…' \
ARCH=amd64 \
./scripts/build-deb.sh
```

输出位于 `dist/`。
