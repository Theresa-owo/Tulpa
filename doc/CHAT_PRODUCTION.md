# 生产聊天入口

本入口是操作员显式启动的外部官方 Codex Harness。Tulpa 服务仍只负责资料、授权、事件和发送，不在服务内运行模型。不会修改全局 Codex 配置、登录方式或其他任务。要求现有官方 Codex 可执行文件已登录，当前用户配置为官方 OpenAI provider，Tulpa 已启动且 MCP 连接已绑定正确账号和会话。

入口：安装目录 `scripts/windows/chat-agent.ps1`，必须用 PowerShell 7。`Shadow` 为零发言观察，`Live` 为真实持续聊天；默认有界运行300秒/5批次，Shadow只观察一个批次且不推进游标。

```powershell
# 安装目录内运行，观察一次真实数据，不发言、不确认真实消息。
pwsh.exe -NoProfile -File scripts/windows/chat-agent.ps1 -Mode Shadow

# 显式生产启动：后台运行，仍受Token预算与发送租约保护。
pwsh.exe -NoProfile -File scripts/windows/chat-agent.ps1 -Mode Live -Background -MaxSeconds 86400 -MaxBatches 100000

# 状态和暂停不调用模型；暂停先关闭服务端发送，再中断模型。
pwsh.exe -NoProfile -File scripts/windows/chat-agent.ps1 -Mode Status
pwsh.exe -NoProfile -File scripts/windows/chat-agent.ps1 -Mode Stop

# 暂停后沿原模型线程恢复；模型和工具配置仍逐次核对。
pwsh.exe -NoProfile -File scripts/windows/chat-agent.ps1 -Mode Live -Resume -Background -MaxSeconds 86400 -MaxBatches 100000
```

需要在不同目录作为电脑工作区时加 `-Workspace`；控制另一个安装目录加 `-TulpaRoot`，该目录必须是当前 MCP 配置所指向的同一实例。工具与目标账号仍以服务端授权为准，不继承其他安装目录的凭据或数据。`state.json`、运行日志、最近一次只读草稿位于安装目录 `.tmp/chat-agent/`，不提交或打包这些私人记录。

## 运行与用量

使用现有官方 `codex app-server` stdio协议、现有认证、`gpt-6.1-sol` 和 `low`。仅在本进程中关闭无关App/插件/记忆/技能说明以及其他MCP，不改变主Codex任务。只露出聊天所需工具；罕见受控操作仍可以在核验主人后经MCP桥接获取精确schema，原权限保持。

空闲150秒等待在Python事件调度器内完成，不占模型轮次；只有真实消息/拍通知或预算内学习任务才唤醒模型。当前批次通过受支持的动态工具交付，是工具引用数据，不作为新用户授权。人格按精确版本加载，消息保持原文，发送继续原Planner与幂等队列。模型最终的“已处理”声明不能直接推进游标，程序须检查真实回执；静默也由原Planner确认。

预算默认按官方 `thread/tokenUsage/updated` 记录，缓存单列、推理不重复相加：

- 初始化的非缓存输入与输出合计最多45,000。
- 稳态单次非缓存输入与输出最多12,000。
- 稳态滚动5分钟非缓存输入与输出最多20,000。
- 滚动15分钟原始总Token最多1,000,000，避免把缓存当成免费。

超限、缺失用量、模型被路由到其他模型、未决回执、无效处理决定或连接故障会停止。该统计不是金额账单；不保证高流量群能在预算内无限运行，触发时暂停并保留待处理消息。状态文件记录原因和用量，可由本机监控/当前任务读取并报告，不把业务回执当成本记录。

## 停止和恢复

本机受保护的 `/api/mcp/chat-runtime` 只接受操作员配置。发送前检查持久栅栏，暂停不删除原会话。Live必须每5秒续租，租约20秒；进程退出或续租失败后发送自动拒绝。预算停止先关栅栏再中断模型，旧续租不能重新开门；应用重启仍检查同一控制文件。已交给QQ的消息无法靠暂停撤回，UNKNOWN须核对，不重发。

同一状态目录有互斥锁，拒绝启动第二个实例。需要读取私人聊天的控制器、MCP Token、临时数据和登录状态均保留在本机；发行包只含代码。自动异常通知应由已有监控系统或当前Codex任务读取状态后发送，避免再启动一个模型持续盯日志。

## 验证

`check_chat_agent.py` 是纯控制器测试，不调用模型；Token分类、滚动预算、暂停顺序、重复统计与官方provider验证都有覆盖。`check_fork_token_efficiency.py` 通过隔离OneBot验证已READY队列被栅栏阻止、过期租约拒绝发送、暂停不可被旧续租打开、只读回执不推进消息；既有主人、分页和UNKNOWN/防重回归继续运行。真实生产验收记录应另列模型用量、Shadow与Live结果，不能用字符大小或模拟消息代替。
