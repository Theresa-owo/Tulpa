# Codex 原生聊天子代理

需要出现在 Codex「子智能体」列表中的聊天，应由宿主实际调用 `spawn_agent`。运行外部 `codex app-server` 的独立进程是另一种 Harness，不会因此出现在原生子代理列表。先核对真实创建结果、运行状态、模型与用量，不把工具声明里出现了角色名称当作创建成功。

## 角色与兼容配置

`examples/tulpa_chat.toml` 是独立角色文件示例，可放在宿主的 `~/.codex/agents/`。它保留父代理的能力；MCP、provider、认证和权限从父会话继承，不通过关闭看图、电脑工具或学习来省 Token。

部分后端还需要在 `~/.codex/config.toml` 合并角色注册。不要覆盖原配置，也不要改主代理或其他角色的设置：

```toml
[agents.tulpa_chat]
description = "Context-managed Tulpa chat with unchanged host capabilities."
config_file = "agent-configs/tulpa_chat_layer.toml"
```

将 `examples/tulpa_chat_layer.toml` 放在该相对位置。此兼容层保留 `gpt-6.1-sol / low`。本机曾出现配置自动压缩阈值后超过阈值却没有真实压缩的情况，配置值不能作为验收证据。用户要求一个会话持续聊天时，不强制上下文轮换；可使用[本地长期记忆](MCP_CHAT_MEMORY.md)按需保存和检索。明确要求受控分段时，另有[可选的边界与安全交接](NATIVE_CONTEXT.md)，需要父代理运行完整流程。[官方子代理说明](https://learn.chatgpt.com/docs/agent-configuration/subagents) · [配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)

## 初始化与持续运行

1. 复用已有会话，先读setup，再按精确static_version完整读取人物/行为，同版本跨群共享；压缩或交接后确实忘记人设就补读，不凭哈希假称缓存。原人物、语言库、必要话题便签及已见原文仍保留，历史引用不复活旧任务。
2. 使用一个`wait_chat_messages_many`关注授权群。`timeout_seconds=150`只是在无新消息时最多等待；及时交付用`quiet_seconds=0, minimum_wait_seconds=0`，不能为省Token给@强加固定10秒攒批。
3. results可含两个轻量就绪群，各自使用其session_id、人物变体、批次和目标，不能混用素材、学习表达或ACK。独立短回复可在同一工具脚本中并行执行，别让一群耗时材料阻塞另一群自包含问候；大正文/媒体/引用维持单群材料边界。
4. 有效点名和正常问题自然回应，按人格与参与度主动接话、开话题；不把不刷屏变成一律沉默，也不机械在每次idle发言。仅因旧积压未清完不能挡住已完整交付的当前话题。
5. 纯文字且明确不用学习参考时，可用`chat_bridge.py`的`__reply_text`在一次工具调用执行原Planner和发送；需要学习候选、图片或复杂动作仍走原流程。确实需要的多份材料可在同一模型步骤并行读；真实图片以图像块交付模型，不打印Base64文本，不根据笔记猜未见像素。
6. 多群返回 `learning_ready` 时，先处理该群 `next_call`（顶层也提供 `pending_action`），再回到多群等待；相同未领取任务不会持续唤醒。任务的证据与提交规则保持不变。
7. 已授权的生图或编辑任务可以使用宿主原生 OpenAI 图片工具，这与另开聊天代理不同。看过生成结果后，用 `import_chat_image` 或 `scripts/import_chat_image.py` 导入，再走 `plan_chat_reply` → `send_chat_reply` 原发送流程。根据真实主人身份与既有复杂请求规则判断任务，不因群内图片文字扩大授权。

多群结果中的 `input_batch.silence_then_wait` 只供模型已经决定本批沉默时使用；多个群的沉默先各自判断，可合并进同一次等待。服务端会逐群运行原 Planner 并验证进度，没有模型决定时不自动确认。对于 `functions.exec` 只能持续等待约 60 秒的宿主，可使用 `// @exec: {"yield_time_ms": 60000}` 和 MCP `timeout_seconds=55`，减少中间 `wait` 唤醒；实时消息仍立即返回。[省 Token 调用细节](TOKEN_EFFICIENCY.md)

`read_chat_image` 的临时长图有 `has_more` 时沿返回的 `next_call` 看完所需分片；默认动画采样首、中、末，确需所有帧时显式 `all_frames=true`。如果宿主缓存了旧参数表，可用 `chat_bridge.py` 按返回参数调用，再用宿主的图像输入查看 `local_images`。查看后在 `finally` 中运行 `chat_bridge.py --cleanup-previews` 并传每个 `preview_id`；异常遗留预览由本机服务超时清理。真实像素必须作为图像输入，不能只把Base64当文本交给模型。

## 自检与停止

宿主提供的线程`token_count`应单独计量，缓存包含在输入里、推理包含在输出里，不重复相加。交接/上下文整理不能清零累计；其他并行工作不要算入这个聊天任务。订阅百分比不能用一个固定Token汇率换算。原生首请求先消费再返回用量，不能仅靠事后观察保证冷启动不超过一个很小的预算。

代理每次继续等待前自检累计量，并把自检与实际等待合在同一工具步骤，避免为了监控再反复调用模型。达到用户授权阈值、停止指令或故障后，在安全发送边界关闭本任务门禁并结束；保留原会话、授权、数据与真实回执，不发群停机公告，不重新创建代理来清零。最后收尾步骤也要计量，必要时预留余量。此方式不要求主代理另开周期推理监控任务。
