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

将 `examples/tulpa_chat_layer.toml` 放在该相对位置。此兼容配置层只作用于这个角色，模型保持 `gpt-6.1-sol / low`；`model_auto_compact_token_limit = 120000` 是整理过时上下文的触发点，不是总用量预算，也不是模型的硬上下文上限。不缩当前消息、像素或授权能力。不同宿主/版本以实际加载及上下文整理日志为准，角色文件存在不等于配置已生效。[官方子代理说明](https://learn.chatgpt.com/docs/agent-configuration/subagents) · [配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)

## 初始化与持续运行

1. 复用已有会话，先读setup，再按精确static_version完整读取人物/行为，同版本跨群共享；压缩或交接后确实忘记人设就补读，不凭哈希假称缓存。原人物、语言库、必要话题便签及已见原文仍保留，历史引用不复活旧任务。
2. 使用一个`wait_chat_messages_many`关注授权群。`timeout_seconds=150`只是在无新消息时最多等待；及时交付用`quiet_seconds=0, minimum_wait_seconds=0`，不能为省Token给@强加固定10秒攒批。
3. results可含两个轻量就绪群，各自使用其session_id、人物变体、批次和目标，不能混用素材、学习表达或ACK。独立短回复可在同一工具脚本中并行执行，别让一群耗时材料阻塞另一群自包含问候；大正文/媒体/引用维持单群材料边界。
4. 有效点名和正常问题自然回应，按人格与参与度主动接话、开话题；不把不刷屏变成一律沉默，也不机械在每次idle发言。仅因旧积压未清完不能挡住已完整交付的当前话题。
5. 纯文字且明确不用学习参考时，可用`chat_bridge.py`的`__reply_text`在一次工具调用执行原Planner和发送；需要学习候选、图片或复杂动作仍走原流程。确实需要的多份材料可在同一模型步骤并行读；真实图片以图像块交付模型，不打印Base64文本，不根据笔记猜未见像素。

## 自检与停止

宿主提供的线程`token_count`应单独计量，缓存包含在输入里、推理包含在输出里，不重复相加。交接/上下文整理不能清零累计；其他并行工作不要算入这个聊天任务。订阅百分比不能用一个固定Token汇率换算。原生首请求先消费再返回用量，不能仅靠事后观察保证冷启动不超过一个很小的预算。

代理每次继续等待前自检累计量，并把自检与实际等待合在同一工具步骤，避免为了监控再反复调用模型。达到用户授权阈值、停止指令或故障后，在安全发送边界关闭本任务门禁并结束；保留原会话、授权、数据与真实回执，不发群停机公告，不重新创建代理来清零。最后收尾步骤也要计量，必要时预留余量。此方式不要求主代理另开周期推理监控任务。
