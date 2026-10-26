<p align="center">
  <img src="./doc/assets/tulpa-hero-night.svg" width="100%" alt="Tulpa" />
</p>

# Tulpa

此 fork 在上游基础上保留原生文件与图片下载、工作区文件发送、群授权追加、低延迟多群消息获取、主人 QQ 校验和独立拍一拍权限，并提供带 Token 预算的官方 Codex 聊天运行器。实时@与当前话题优先于旧积压，原生Agent保留自然接话和主动话题能力。安装与升级见[部署本 fork](#部署本-fork)，功能与权限见 [fork 补丁说明](doc/FORK_PATCHES.md)，后续合并上游并保留自有修改见 [维护说明](doc/FORK_MAINTENANCE.md)。

**使用MCP，让你几乎所有的Agent访问QQ/微信/B站，甚至亲自下场水群！**

> 为推动硅基生命的发展和普及贡献一份力量！

[部署本 fork](#部署本-fork) · [生产聊天入口](doc/CHAT_PRODUCTION.md) · [Linux 终端版](doc/LINUX.md) · [MCP 命令行](doc/MCP_CLI.md) · [下载](#下载) · [快速开始](#快速开始) · [Agent 配置教程](doc/AGENT_SETUP.md) · [相关文档](#相关文档)

Tulpa 通过同一 MCP 连接提供 QQ、微信与哔哩哔哩能力，三个模块分别授权。Agent 可以查资料、追溯消息、带着自定义人格参与 QQ 群聊，或从视频字幕、真实画面和评论中寻找信息。

**桌面发行版：Windows 10 / 11 x64；Linux：x86_64 / ARM64 终端源码版。**

## 能做什么？

下面是可以交给 Agent 的请求示例。

### 让小鲸鱼来水群

> 用随包的小鲸鱼持续聊天，你也可以自行编写你的人物卡片。

随包提供小鲸鱼（`little_whale`）、小鲸鱼2号（`little_whale_v2`）和龙娘（`dragon_girl`，测试版），你也可以用 Markdown 写自己的角色人设卡片。支持按语境接话、原生 @ 与引用；授权后可看图、发送和收藏表情、给消息贴表情回应，保留理解笔记供后续会话选用。

持续聊天会在当前已授权的机器人账号和群里积累群友表达与黑话，潜水时也能学习；同账号同群重开可接续积累。人物卡与学习参考分开，模型可以选择本轮不用。[聊天节奏](doc/MCP_CHAT_TURNS.md) · [实时学习](doc/MCP_LANGUAGE_LEARNING.md)

人格卡详见 [人格教程](doc/MCP_CHAT.md)与[表情链路](doc/MCP_STICKERS.md)。

视频链接：[点击这里查看哔哩哔哩视频](https://www.bilibili.com/video/BV16jHL6AE5z)

### 刷B站（测试中）

> 直接让Agent帮你刷B站视频，B站信息可直接植入Agent的思维链和工作流。

支持关键词搜索、账号推荐流、视频详情与分 P、带时间戳的字幕、真实抽帧和评论分页。无字幕时可使用已安装的本地 ASR 模型；读不到的内容会明确返回缺失项。B站账号扫码登录，权限在同一 MCP 连接中勾选。[B站使用说明](doc/BILIBILI_MCP.md)

### 找回聊过的资料和文件

> 帮我找我的群里的一些我需要的附件，核对时间，并标出消息来源。

按关键词、人物、会话和日期检索，再展开上下文。图片、语音和文件按需读取；轻量版可经 OneBot 下载 QQ 群原文件，交给同机 Agent 自己的文件工具阅读。

![Tulpa](doc/assets/file_finding.png)

## 为什么用 Tulpa

- **多平台信息整合**：把QQ、微信和哔哩哔哩的能力整合到任意Agent上。
- **安装方便**：具备图形化界面和CLI界面，直接让你的肥鱼全自动配置即可~
- **全自动部署**：自动下载、校验、配置和运行SnowLuma和配置OneBot服务。已获得SnowLuma主开发授权！
- **复用熟悉的 Agent**：MCP协议使得你可以用几乎所有的桌面Agent来使用Tulpa提供的大量接口。

经过测试可完美接入以下。理论上可完全兼容所有已发行Agent：

- <img src="doc/assets/agents/openai.png" width="24" height="24" alt="Codex"> [Codex](doc/AGENT_SETUP.md#codex) ·
- <img src="doc/assets/agents/claude.png" width="24" height="24" alt="Claude Code"> [Claude Code](doc/AGENT_SETUP.md#claude-code) ·
- <img src="doc/assets/agents/deepseek.png" width="24" height="24" alt="DeepSeek Harness"> [DeepSeek Harness](doc/AGENT_SETUP.md#deepseek-harness) ·
- <img src="doc/assets/agents/antigravity.svg" width="24" height="24" alt="Antigravity"> [Antigravity](doc/AGENT_SETUP.md#google-antigravity) ·
- <img src="doc/assets/agents/workbuddy.png" width="24" height="24" alt="WorkBuddy"> [WorkBuddy](doc/AGENT_SETUP.md#workbuddy)

## 下载

**本 fork 的最新补丁从本仓库 `main` 部署，步骤见下方。** 下表链接均指向上游原版发行包，不包含本 fork 自有补丁。需要 Windows 便携包时，按[桌面构建](doc/DESKTOP.md)从本 fork 的已提交代码构建，再按[保留数据升级](doc/UPGRADE.md)部署到原目录。

| 版本 | 适合你，如果你想… | 下载 |
| --- | --- | --- |
| **上游 Windows MCP 版** | 图形界面或命令行，连接已有 Agent | [0.5.7 ZIP](https://github.com/fumingyang2004/Tulpa/releases/download/v0.5.7/Tulpa-MCP-0.5.7-win-x64.zip) |
| **上游 Linux MCP 终端版** | Linux x86_64 / ARM64，CLI/TUI 与 MCP 服务 | [0.5.7 tar.gz](https://github.com/fumingyang2004/Tulpa/releases/download/v0.5.7/Tulpa-MCP-0.5.7-linux.tar.gz) |


**0.5.7 只发布 MCP 版。** Windows 包自带 Python 与命令行入口，使用系统 WebView2 Evergreen Runtime；Linux 包为终端源码包，首次启动安装依赖。语音模型不随包附带。[MCP 说明](doc/MCP_LITE.md) · [最新发布](https://github.com/fumingyang2004/Tulpa/releases/latest) · [0.5.7 更新说明](doc/releases/0.5.7.md)

Windows 需要 **.NET Framework 4.8**。下载 ZIP 后完整解压到可写目录，打开 `Tulpa/Tulpa.exe`；命令行使用 `Tulpa/Tulpa.CLI.exe`。不要从压缩包直接运行，GitHub 自动生成的 `Source code` 也不是 Windows 安装包。QQ / 微信客户端与外部 Agent 需自行准备；SnowLuma 可由 Tulpa 自动配置。

**老用户升级：** 按[保留数据升级教程](doc/UPGRADE.md)检查、备份并更新原目录，无需重新导入。自改的同名随包人格先另存；升级后仍打开原目录 EXE，并重连 MCP。

## 部署本 fork

以下是 **Windows 源码部署与低 Token 聊天**流程，需要 Windows x64、Python 3.13 x64、Git 和 PowerShell 7（`pwsh.exe`）。先用 `python --version` 确认版本。Tulpa MCP 本身不运行回答模型；生产聊天运行器复用现有官方 Codex 登录，要求当前账号可用 `gpt-6.1-sol`，配置使用官方 OpenAI provider，不自动换模型或第三方服务。Linux 的服务部署见 [Linux 教程](doc/LINUX.md)，以下 `.ps1` 聊天入口面向 Windows。

### 1. 获取代码并启动服务

在 PowerShell 7 中执行：

```powershell
git clone --branch main https://github.com/Theresa-owo/Tulpa.git
Set-Location Tulpa
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements/mcp.txt
& .\.venv\Scripts\python.exe -X utf8 -m chatlocal.mcp_cli serve
```

保持这个终端运行。在**另一个 PowerShell 7 终端**进入同一仓库目录，查询实际页面地址：

```powershell
& .\.venv\Scripts\python.exe -X utf8 -m chatlocal.mcp_cli --json status
```

用浏览器打开返回的 `url`。页面使用本机随机端口，MCP 地址以创建连接时生成的配置为准。生产运行器需要 `serve` 发布的本机管理接口；`python app_mcp.py` 只用于固定端口的页面开发，不用于这套生产启动流程。MCP 版无需填写旧内置模型的 `API_BASE`、`API_KEY` 或 `MODEL` 字段。

### 2. 绑定 QQ、配置授权和主人

1. 登录目标 QQ，在页面的 **OneBot 设置 → 连接 QQ** 完成配置，核对实际账号；持续群聊需要 **HTTP + 正向 WebSocket**。无需先导入整库历史。[连接教程](doc/SNOWLUMA_MANAGED.md)
2. 创建 MCP 连接，选择该账号与允许参与的群，启用持续聊天和发送；看图、文件、群管理及拍一拍分别按需授权。需要主动拍或自然回拍时开启独立的 `chat_poke` 权限，无需因此开放其它 QQ 管理操作。
3. 在连接卡片的 **主人 QQ 校验** 中填写部署者自己的主人 QQ 并启用。该值保存在本机，仓库不预设个人号码。主人可以直接自然语言请求，`/tulpa` 是可选前缀，不需要手写 JSON；Tulpa 服务端核验真实发送者，昵称、引用和转发不能授权。非主人只接受闲聊与简短问答，达到“画一张SVG的鹈鹕骑自行车”的复杂请求默认由模型简短拒绝。[详细规则](doc/FORK_PATCHES.md#主人-qq-校验)
4. 开启 MCP 服务，并在服务设置开启 **省 Token 模式**，将页面生成的 MCP 配置接入 Codex 并重连。下面的生产运行器读取现有 Codex 配置中的 `mcp_servers.tulpa`，该连接必须指向当前实例。[接入教程](doc/AGENT_SETUP.md)
5. 让已接入的 Agent **复用已有聊天会话，或仅为所选群调用 `start_chat_session` 创建会话，暂不等待或发言**；可选择通用 `白龙娘` 人格。运行器接管当前连接下已有的活跃会话，最多 8 个，不自动建群会话。交接前暂停旧聊天代理，避免两个 Agent 处理同一群。

文件工作区可在本机 `.env` 设置 `TULPA_WORKSPACE_DIR`；原文件放入该工作区的 `outgoing-files/`，准备发送的图片放入 `outgoing-images/`。文件下载、发送及主人校验继续受原服务端权限约束。原有配置不要用 `.env.example` 覆盖。

### 3. 检查后启动生产聊天

在仓库根目录的另一个 PowerShell 7 终端执行。`Shadow` 会读取实际资料并调用模型，但不向 QQ 发言，也不确认群消息游标：

```powershell
# 先做一次零发言观察。
pwsh.exe -NoProfile -File .\scripts\windows\chat-agent.ps1 -Mode Shadow

# 显式开始真实聊天；后台运行，最多一天或 100000 批次。
pwsh.exe -NoProfile -File .\scripts\windows\chat-agent.ps1 -Mode Live -Background -MaxSeconds 86400 -MaxBatches 100000

# 查看状态或暂停，不调用模型。
pwsh.exe -NoProfile -File .\scripts\windows\chat-agent.ps1 -Mode Status
pwsh.exe -NoProfile -File .\scripts\windows\chat-agent.ps1 -Mode Stop
```

运行器固定使用 **`gpt-6.1-sol / low`**，保留现有认证与服务档位，只调整这个独立进程的上下文，不改主 Codex 权限。普通文字由模型决定内容，再由本地程序执行原 Planner 和发送队列；空闲无消息且无学习任务时不调用模型。默认预算为初始化非缓存输入加输出 45,000、稳态单次 12,000、滚动 5 分钟 20,000、滚动 15 分钟原始总 Token 1,000,000。超限、超时或明确 HTTP 429 时先关闭发送再停止，保留待处理消息；UNKNOWN 不换编号重发。

状态与用量位于本实例 `.tmp/chat-agent/`。Live 未指定运行上限时默认最多 300 秒或 5 批次，Shadow 仅观察一批；同协议暂停后可以加 `-Resume` 沿原模型线程恢复。升级后若提示 `runtime_protocol_changed_start_fresh`，去掉 `-Resume` 重新初始化，原 QQ 会话、人格、聊天记录及游标仍保留。可用 `-Workspace` 指定已存在的电脑工作目录，`-TulpaRoot` 必须与当前 MCP 实例一致。[完整运行说明与验收数据](doc/CHAT_PRODUCTION.md) · [Token 策略](doc/TOKEN_EFFICIENCY.md)

### 4. 更新并保留原数据

源码版先暂停聊天运行器、停止当前实例，再将 `.env`、`data/`、自定义人格及需要保留的导入、附件和外置工作区备份到本机独立位置。检查 `git status --short`，先保存对程序文件的自有修改；随后在**原仓库目录**更新：

```powershell
git pull --ff-only origin main
& .\.venv\Scripts\python.exe -m pip install -r requirements/mcp.txt
& .\.venv\Scripts\python.exe -X utf8 -m chatlocal.mcp_cli serve
```

不要删除 `data/`、重建授权、重新导入聊天，或用上游原版包覆盖 fork。重连 MCP 后核对账号、主人设置、原会话与接收状态，先运行 Shadow；正式续聊使用原会话。Windows 便携版应使用**从 fork 构建的新包**和[保留数据升级工具](doc/UPGRADE.md)，不能把源码文件零散覆盖到便携包。

后续吸收原项目更新时，按[维护说明](doc/FORK_MAINTENANCE.md)配置 `upstream` 并运行 `scripts/sync-upstream.ps1`：先预览，再以 `-Apply` 合并和检查，保留双方 Git 历史与自有补丁。是否推送由显式 `-Push` 决定，不自动覆盖冲突。

## 快速开始

### SnowLuma 自动配置

打开并登录 QQ，在 Tulpa 的 **OneBot 设置 → 连接 QQ** 中勾选协议和自动配置授权，点击 **一键配置**。Tulpa 自动准备固定版本 [SnowLuma 2.1.1](https://github.com/SnowLuma/SnowLuma/releases/tag/v2.1.1)，校验运行包、初始化随机凭据、选择空闲端口、配置 HTTP/WS 并核对 QQ 与实时事件。多个 QQ 进程时选择目标，未登录时在 QQ 扫码即可，无需终端、下载路径、手填 Token 或管理密码。

优先使用校验通过的随包运行文件；未随包提供时从官方渠道下载。原有独立 SnowLuma 可在高级选项导入，保留原配置；不会扩大 MCP 授权。第三方许可仍独立适用。[操作与恢复](doc/SNOWLUMA_MANAGED.md) · [本次开发验证](doc/SNOWLUMA_AUTOMATIC_VALIDATION.md)

已取得 SnowLuma 主开发作者对自动部署的书面同意，条件是**不修改 SnowLuma 本体**。[查看授权记录](doc/assets/SnowLuma.png)。使用者仍需在 Tulpa 内主动同意 SnowLuma 协议。

### 我要 QQ 水群

**无需先导入整库历史。** 这条路径直接接收 OneBot 实时事件。

1. 启动 QQ 和 Tulpa；在连接 QQ 中勾选协议并点击“一键配置”，或从高级选项导入已有 OneBot 连接。持续群聊需要 **HTTP + 正向 WebSocket**。[连接教程](doc/SNOWLUMA_MANAGED.md)
2. 点击 **开始创建链接**，启用 **QQ** 模块，选择当前群与持续聊天、发送权限；按需加看图、表情发送或收藏。只水群不需勾选历史读取。
3. 点击 **一键开启 MCP**。名称可留空；按 [Agent 教程](doc/AGENT_SETUP.md)配置并重连客户端。首页可单独启停或删除连接。
4. 在外部 Agent 的**专用对话**中使用上面的水群示例。想换龙娘，选择 `dragon_girl`。新人格放进当前安装目录的 `chatlocal/prompts/mcp_chat/`，重新列出即可发现。

保持 QQ、OneBot、Tulpa 和外部 Agent 运行。宿主结束回合、达到预算、退出或休眠后，Tulpa 不会自行继续推理。可在 Tulpa 点 **停止聊天 / 停止全部群聊**；已发出的消息不会因此撤回。改过人格后，停止并重新开启会话才会采用新设定。

### 我要看 B站视频

1. 在左侧 **B站账号设置** 用 B站 App 扫码登录。
2. 点击 **开始创建链接**，启用 **哔哩哔哩** 并勾选只读能力，点击 **一键开启 MCP**。可以只选 B站，也可和 QQ / 微信共用这条连接。
3. 在 Agent 中刷新工具列表，直接描述想找的话题。模型可自行搜索、读取推荐、字幕/本地 ASR、真实画面及评论。[使用与限制](doc/BILIBILI_MCP.md)

### 我想通过命令行配置

Windows 双击 `Tulpa.CLI.exe`，或在终端执行 `Tulpa.CLI.exe --json status`。Agent 可以直接管理连接、配置 QQ、发起 B站扫码、查询学习库并调用已授权 MCP 工具。[CLI 教程](doc/MCP_CLI.md)；Linux 沿用 `./tulpa` CLI/TUI。

### 我要查 QQ / 微信历史

1. 登录本机 QQ / 微信，在 **数据与同步 → 本机账号**核对账号，再选会话和日期读取。可以从少量近期消息开始。
2. 点击 **开始创建链接**，启用对应的 QQ / 微信模块，勾选聊天记录读取及所需附件权限，选择资料范围。
3. 点击 **一键开启 MCP**，按[教程](doc/AGENT_SETUP.md)接入外部 Agent，从上面的资料查询或总结示例开始。未导入资料时，界面会在选择历史读取后提示导入。

本地历史查询和草稿不需要 OneBot。读取范围受客户端版本、账号、权限及本机缓存影响，无法保证找回未取得的历史；见[数据读取](doc/IMPORTS.md)与[实时摄取](doc/LIVE_INGESTION.md)。

## 隐私与权限

- **资料保存在本机，不等于完全离线。** 必要消息片段会进入你选择的模型服务；启用识图时也会发送所选图片。
- 看图、发送、收藏和群管理分别授权，默认关闭、可撤销。**MCP 的发送和群管理勾选后是持续授权，调用直接执行，不会逐条回到 Tulpa 审批。**
- 微信提供资料读取，回复草稿可由外部 Agent 生成，不提供微信发送能力。
- 不要分享使用过的整个安装目录，尤其是 `data/`、`imports/`、`.env`、Token 和私人诊断。撤销连接不能收回已交给客户端的内容；MCP 范围限制也不是整台电脑的文件隔离。

> [!CAUTION]
> Tulpa 是独立的第三方项目，与腾讯 / 哔哩哔哩无隶属或授权关系。项目仅供学习与技术研究，使用风险自负。

## 相关文档

- 接入与群聊：[Agent 教程](doc/AGENT_SETUP.md) · [MCP 权限](doc/MCP.md) · [OneBot](doc/SNOWLUMA_SETUP.md) · [人格](doc/MCP_CHAT.md) · [表情](doc/MCP_STICKERS.md)
- 资料与调查：[文件](doc/ARTIFACTS.md) · [语音](doc/VOICE.md) · [调查与证据](doc/INVESTIGATION.md) · [工作区](doc/WORKSPACES.md) · [会话记忆](doc/TULPA.md)
- 功能与维护：[完整功能](doc/FEATURES.md) · [回复助手](doc/REPLY_COPILOT.md) · [群管理](doc/GROUP_MANAGEMENT.md) · [升级](doc/UPGRADE.md) · [安全报告](doc/SECURITY.md)
- Fork 部署与运行：[生产聊天](doc/CHAT_PRODUCTION.md) · [Token 优化](doc/TOKEN_EFFICIENCY.md) · [补丁与主人校验](doc/FORK_PATCHES.md) · [保留补丁同步上游](doc/FORK_MAINTENANCE.md)

## 参与开发

<details>
<summary>从源码运行与贡献</summary>

本 fork 的源码安装、启动、QQ 授权和更新命令见上方[部署步骤](#部署本-fork)。只调试页面时也可以运行 `.venv\Scripts\python.exe app_mcp.py`，访问 `http://127.0.0.1:7861/`；生产聊天使用上面的 CLI `serve` 入口。实时 QQ 与 B站不要求安装历史读取器；历史资料及便携包构建依赖见下方文档。

[贡献指南](doc/CONTRIBUTING.md) · [桌面构建](doc/DESKTOP.md) · [验证说明](doc/VALIDATION.md)

</details>

## 许可与致谢

Tulpa 自有代码采用 [MIT License](LICENSE)，第三方组件、模型与客户端遵循各自许可，见 [THIRD_PARTY.md](doc/THIRD_PARTY.md)。本项目不是腾讯官方产品，与 QQ、微信及模型服务商没有隶属关系。

感谢以下仓库提供的参考或支持！

- [qq-bridge](https://github.com/Derpyu520/qq-bridge)：群聊行为与提示词设计参考；小鲸鱼基于其角色卡。
- [MaiBot](https://github.com/Mai-with-u/MaiBot)：会话的组织逻辑和学习机制参考。
- [QQ-agent](https://github.com/K0nd1us/QQ-agent)：表情收藏、语义笔记、候选与用量反馈的设计参考。
- [SnowLuma](https://github.com/SnowLuma/SnowLuma)：提供 QQ / OneBot 接入能力；感谢主开发[墨梓柒](https://github.com/DrSmoothl)对 Tulpa 自动部署的书面授权与支持。[授权记录](doc/assets/SnowLuma.png)
- [墨梓柒](https://github.com/DrSmoothl)：感谢您的开源精神以及对本人的支持！

<details>
<summary>SnowLuma 自动部署授权记录</summary>

![SnowLuma 自动部署授权记录](doc/assets/SnowLuma.png)

</details>
