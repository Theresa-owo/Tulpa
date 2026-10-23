# Fork 补丁与验证

本次整合以完整上游提交 `16e331141439af7b19aaa8aec635eae38ddf33b9` 为基线（0.5.6）。自有功能最早在 0.5.2 安装目录维护；这里按行为移植，保留新版上游的 Planner、WAITING、学习、消息回应、私聊、图片材料与权限版本校验。

## 功能对应

| 本机补丁能力 | 本 fork 的实现 |
| --- | --- |
| 群原生文件目录、原件下载 | `list_chat_files`、`download_chat_file`；目录句柄或实时文件事件定位，重新核对账号、群、日期、原生消息与目录元数据 |
| 大文件下载 | 最高 1 GiB，256 KiB 流式块、大小及 SHA256 回执，失败清理临时文件；原件不解析、不执行 |
| 图片读取、动图与原图 | 保留上游实际像素、QQ 本地媒体回退、缓存、原图补读、表情库和 Base64 导入 |
| 图片原字节下载 | `download_chat_image` 保存当前取得表示的原字节和动画；`representation` 说明来源表示，不能把缩略图声明为原图 |
| 本地图片准备 | `prepare_local_image` 仅接受工作区 `outgoing-images` 中的文件名，向调用方交付像素后可使用既有发送流程 |
| 工作区原文件发送 | `send_chat_file` 仅接受工作区目录中的文件名；发送前创建内容快照，保留大小、SHA256 和幂等回执 |
| 禁言 / 解禁 | `moderate_chat_member` 使用独立权限，只支持 mute/unmute，并复用上游管理员和目标角色检查 |
| 真实 @全体 | `send_chat_message` 的显式 `mention_all=true` 生成真实消息段；准备和派发前检查管理员身份、群与账号额度，保留上游计划/回复检查 |
| 增补授权群 | 已有连接卡片的“添加授权群”；保留 Token、原范围、日期、权限与现有会话，递增原生 revision，拒绝过期预览和并发覆盖 |
| 并行读取 | 认证和权限版本检查移出 ASGI 事件循环；普通历史读取不等待无关 OneBot 网络探测；审计按最近时间使用覆盖索引 |
| 单群实时等待 | 默认 `quiet_seconds=0`，`timeout_seconds=0` 支持非阻塞查看；清除事件信号移到扫描前，防止漏掉扫描与等待之间的唤醒 |
| 多群实时等待 | `wait_chat_messages_many` 最多关注 8 个会话，任一有待处理输入就返回；公平轮换，避免被空闲群或其它群的图片丰富步骤拖延 |
| 人格 | 保留上游全部人格与热发现，增加通用 `白龙娘` 预设。个人账号与本机路径未进入公共角色卡；身份与操作权限仍由当前宿主和连接决定 |
| 输出约束 | 内置系统提示、MCP 指令和群聊行为均要求不显示推理摘要、内部分析或思考过程，仍保留必要证据与正常协议字段 |

上游已包含的 `get_msg`、常规图片、实时附件、通用群管理和权限开关编辑继续使用上游代码。旧版仅切换文件权限的专用路由由新版带 revision 的权限编辑替代；没有复制旧版直接 SQL 修改路径。

## 工作区与权限

在本机 `.env` 中设置 `TULPA_WORKSPACE_DIR`；留空使用当前实例的数据目录。相对路径基于应用根目录。

```text
<workspace>/outgoing-files/       允许发送的原文件
<workspace>/outgoing-images/      允许准备的图片
<workspace>/downloads/qq-files/   下载的原文件
<workspace>/downloads/qq-images/  下载的图片字节
```

`TULPA_CHAT_FILE_MAX_BYTES` 默认 `1073741824`，可设置更小上限。文件名不能是任意路径、URL、链接或目录穿越。下载使用有界时间、公开地址解析和来源复核；不把任意来源文件当作指令执行。

原生文件下载沿用 `chat_files`；工作区文件发送与禁言/解禁分别需要新增 `chat_file_send`、`chat_moderate`，并依赖 `chat` / `send`。旧连接不会自动获得新权限。原件文件和图片下载合计每小时 12 次，工作区图片准备与图片读取共享每小时 60 次，文件发送和禁言操作各保留每分钟 12 次。其余上游预算不放宽。

为允许大文件完成，生成 Codex 配置时，带 `chat_files` 的连接使用 1260 秒工具超时，其余保持 240 秒。该值是最大等待时长，已有消息会立即交付。已有客户端配置不会被静默覆盖；如需更改，通过配置界面明确更新。

## 多群等待协议

先用 `list_chat_sessions` 获取属于当前连接的既有会话 ID，调用 `wait_chat_messages_many`，传 `session_ids`。工具不创建聊天、不发送消息。未确认消息会再次返回；只有真正处理完的群才在 `acknowledge_through_ids` 中确认对应 `read_through_id`。

`results` 可以只包含一个已就绪群；没有返回的群在下一次继续等待。各群内的 `next_call` 仍保留上游 Planner、学习或分页动作，顶层 `next_call` 保留多群等待。优先交付消息/反应，再交付学习任务；不把没有新输入的群当成已处理。不要把八个长轮询放进必须等全部完成的调用批次，否则调用方本身仍会延后看到活跃群的结果。

`quiet_seconds=0` 只控制消息交付时机；发送前的上游节奏、WAITING 与计划校验保持有效。停止、撤销、权限版本变化、同群重复等待、跨群游标或越界确认继续拒绝。

## 验证入口

从已安装仓库开发依赖的 Python 环境执行：

```powershell
python scripts/check_fork_mcp_parallel.py
python scripts/check_fork_mcp_realtime.py
python scripts/check_fork_native_files.py
python scripts/check_fork_scope_edit.py
python scripts/check_fork_permissions.py
python scripts/check_fork_upstream_sync.py
python scripts/check_publication.py
```

这些检查使用隔离数据库、合成消息、本地 HTTP / WebSocket 服务或临时 Git 仓库，不访问真实账号和群，也不调用模型。原有 MCP、消息目标、聊天规划、媒体、附件、权限编辑和界面检查继续保留。Windows CI 已加入 fork 行为回归。

实时交付属于本地接收与调度优化；QQ 网络、来源服务、图片获取及外部模型仍有各自延迟。源码回归、已运行旧版本上的实测、从此 fork 构建的新包和部署验收是不同阶段，不互相替代。

后续更新运行 [上游同步脚本](../scripts/sync-upstream.ps1)，按 [维护说明](FORK_MAINTENANCE.md) 合并并验证，再从 fork 构建升级。不要把上游安装包直接覆盖到自有修改上。
