# Claude 桌面客户端 Windows 版 中文汉化（官网安装器 / 微软商店）

**汉化对象：Claude 桌面客户端 Windows 版** —— 以 MSIX 包格式安装（包名 `Claude_pzs8sxrjxfjjc`，位于 `C:\Program Files\WindowsApps`，内含 Cowork / Claude Code 界面，所以上游也叫它 Claude Code 桌面版）。双击一次就好，Claude 每次自动更新后重跑一次。

> **两种安装渠道装出来的是同一个包，都适用**：① 官网 [claude.ai/download](https://claude.ai/download) 的 `Claude Setup.exe`；② 微软商店（Microsoft Store）搜 "Claude"。

> **实测版本：Claude 5.11492.2.0（Windows 11 x64，2026-11，官网 `Claude Setup.exe` 安装）**。Claude 自动更新到新版本后汉化会被覆盖，重跑一次 `install.bat` 即可。

> ⚠️ 非官方汉化。只写入本地界面翻译文件与三处语言开关，不修改核心逻辑、不替换任何 JS 标识符。

## 适用范围

| 形态 | 是否适用 |
|------|------|
| **Claude 桌面客户端 Windows 版**（MSIX 包 `Claude_pzs8sxrjxfjjc`，官网 `Setup.exe` 或微软商店安装均可；5.11492.x+ 的 `ion-dist.asar` 布局） | ✅ **本 fork 的目标**，走 `install_asar.py` |
| 旧版桌面客户端（`ion-dist/` 解包目录布局） | ✅ 走上游原版 `install.py` |
| Claude Code 命令行工具（CLI / npm 安装的 `claude`） | ❌ 那是终端程序，不是本仓库的对象 |
| claude.ai 网页版 | ❌ 无需汉化，网页版设置里已可选简体中文 |

不确定自己的安装形态？`install.bat --check` 会自动定位 Claude 桌面客户端并报告布局与状态。

## 本 fork 的新增：ion-dist.asar 单文件布局兼容

上游 [pvq125800/claude-code-zh-cn](https://github.com/pvq125800/claude-code-zh-cn) 的 `install.py` 依赖 `app/resources/ion-dist/` 解包目录。**2026-11 起的桌面客户端版本（5.11492.x+，官网与商店渠道相同）把整个 ion-dist 打成了单文件 `ion-dist.asar`，原脚本会报"读不到官方翻译底稿"安全退出。**

本 fork 新增 `install_asar.py`，`install.bat` 会自动检测安装布局并选择脚本：

| 安装目录布局 | 适用版本 | 脚本 | 原理 |
|------|------|------|------|
| `ion-dist/` 解包目录 | 旧版 | `install.py`（上游原版，未改动） | 直接部署翻译 JSON + JS 补丁 |
| `ion-dist.asar` 单文件 | 5.11492.x+ | `install_asar.py`（本 fork 新增） | 重打包 asar + 同步 exe 内嵌哈希 |

`install_asar.py` 在上游思路之外额外做了什么：

1. 解析 `ion-dist.asar`，以官方 `en-US.json` 为底稿叠加翻译包，生成 `i18n/zh-CN.json` 与 `i18n/zh-Hans.json`（含 `dynamic/` 副本——两个名字都部署，主进程与渲染进程无论解析到哪个都能命中）
2. 对 asar 内 `assets/v1/*.js` 打三处语言开关补丁（白名单加 `zh-CN`、`zh-Hans` / 名称表 / `localeChoice` 钉住），每个文件过 `node --check`，不过即回滚
3. 重打包 asar：未改动文件逐字节原样流式拷贝，新增/修改文件重算 4MB 分块 SHA256 integrity
4. **替换 `claude.exe` 内嵌的 asar 头部哈希**（64 位 hex 等长原地替换）。exe 开启了 `EnableEmbeddedAsarIntegrityValidation` 熔丝，不同步这个哈希 Claude 会拒绝启动
5. `Claude-3p/config.json` 的 `locale` / `language` 设为 `zh-Hans`（主进程自带简中 shell 翻译，`app.asar` 无需改动）

## 效果

**ion-dist.asar 布局（5.11492.2.0 实测，本 fork）：**

| 范围 | 覆盖 |
|------|------|
| 前端界面（44,218 条） | 约 **63%** 已译，其余回退英文 |
| 动态文案 | 49 / 51 |
| Shell / 主进程 | 内置 `zh-Hans` 全量 |

未译条目是新版 Claude 才出现、翻译包尚未覆盖的 key，自动回退英文，欢迎 PR 补齐（见下方"贡献翻译"）。

**ion-dist/ 解包目录布局（旧版，上游原脚本）：** 31,749 / 32,243 条，98%。

## 快速使用

**前置条件**：Windows 10/11、[Python 3](https://www.python.org/downloads/)（安装时勾选 Add to PATH）、已安装 Claude 桌面客户端（官网 `Claude Setup.exe` 或微软商店装的都可以，同一个 MSIX 包）。

1. 下载本仓库（Code → Download ZIP，或 `git clone`）
2. 双击 **`install.bat`**（会自动请求管理员权限，自动识别新旧布局）
3. 重启 Claude Code

脚本会从上游 [Releases](https://github.com/pvq125800/claude-code-zh-cn/releases/latest) 自动取翻译包 `resources-zh-CN.zip`（本仓库不分发翻译文本，尊重上游的分发范围约定）。如果你的网络连 GitHub API 不通，手动下载该 zip 放到 `install.bat` 同目录，即可完全离线安装。

**卸载 / 还原英文**

```bash
install.bat --uninstall
```

会按备份逐文件还原 asar、`claude.exe` 内嵌哈希与 `config.json`，并把所有权交还 `TrustedInstaller`。

**其他模式**

```bash
install.bat --check   # 只诊断，不改动任何东西
install.bat --build   # 只构建新 asar 到 staging，不安装
```

## 常见问题

- **提示未找到 Claude**：确认桌面客户端已安装（官网 `Setup.exe` 或微软商店均可）；或手动指定 `install.bat --dir "C:\Program Files\WindowsApps\Claude_x.x.x.x_x64__pzs8sxrjxfjjc"`。
- **Claude 自动更新后变回英文**：正常，更新会覆盖汉化文件，重跑 `install.bat` 即可。
- **Claude 更新报"包已损坏 / 修复失败"**：先 `install.bat --uninstall`（会把属主与 exe 哈希还原），更新完再重装汉化。
- **安装后 Claude 启动失败**：说明 exe 哈希与 asar 不匹配（例如手动替换过文件），跑 `install.bat --uninstall` 还原备份即可恢复英文原版。

## 贡献翻译

翻译文本随上游 **Release 附件** `resources-zh-CN.zip` 分发，不进仓库。想补翻译：下载 zip，对照 Claude 安装目录里的 `i18n/en-US.json`（asar 内）或 `ion-dist/i18n/en-US.json`（旧布局）找出仍是英文的 key，把改好的文件发到上游仓库的 issue / PR（附 diff 即可）。

## 许可与免责

- 本项目采用 MIT 许可，见 [LICENSE](LICENSE)，授权范围是安装脚本本身。
- 基于 [pvq125800/claude-code-zh-cn](https://github.com/pvq125800/claude-code-zh-cn)（MIT）修改，上游原脚本完整保留于 `install.py`。
- 与 Anthropic 官方无关联。仓库与 Release 里都不含账号、密钥或个人路径；脚本只写本机 Claude 安装目录和你的 `AppData`。
- 汉化文本对应 Claude 官方界面字符串，请仅在个人使用范围内自行分发。

## 致谢

- [pvq125800/claude-code-zh-cn](https://github.com/pvq125800/claude-code-zh-cn) — 上游项目，`install.py` 与翻译包均出自该项目
- [claude-desktop-zh-cn](https://github.com/xixu-me/Claude-Desktop-ZH-CN) — 基础前端翻译
