# Token 开销与省 Token 模式

服务设置中的「省 Token 模式」开启后，完整聊天消息、身份校验、发送队列和既有学习成果继续保留。该开关只调节交付与调度，不切换模型、不扩大连接权限。普通安装默认兼容双份 MCP 结果；本地升级可显式开启省 Token 模式。

## 已实现

- `list_chat_sessions` 默认只列活跃会话摘要，共享 receiver，按 offset/limit 分页。`include_stopped=true` 查询旧会话，`response_format=full` 读取完整记录。目录不再反复交付人格全文。
- `list_chat_personas` 默认仅目录；所选人格由 start/get 交付。首次读取后缓存返回的 `static_version`，其他相同人格会话可在 start/get 传 `known_static_version`；只省略哈希完全相同的人格和行为原文。动态主人身份和群状态仍交付，缓存丢失或版本变化必须补读。
- 开启省 Token 模式时，默认 compact get 只返回 `setup` 元信息：不展开历史、不标记消息已读、不生成批次。`view=persona` 单独交付完整人格和行为；`view=context` 读取最多8条未确认消息和必要前文，可用 context_limit 调小并沿 has_more 分页；`view=all,response_format=full` 保留完整排查入口。未决回执在 setup 中以ID、状态及读取入口提示，不能因此忽略 UNKNOWN 或重发。
- 普通纯文本在 content 中保留全文，只移除与它完全相同的 text segments 副本；包含图片、@、引用等混合消息保留结构，数据库原文不变。
- 工具总说明缩短，详细规则在具体工具/首次人格和动态提示中；默认 get 使用 compact，full 排查入口保留。
- 开启模式后 MCP 在线传输仅保留一份完整 JSON TextContent 和原图像块，不重复 structuredContent。客户端应读取 structuredContent 或解析第一条文本 JSON；标准文本客户端同样能读取。没有 outputSchema 的工具不依赖 structuredContent。
- 无关图片不自动下载或交付像素。`materials.images[].state=on_demand` 及其 `read_call` 是按需入口，不是拒绝或失败。模型谈论图片前仍须读取真实像素，不能根据笔记猜图。`read_chat_image(max_frames=1)` 只看首个采样帧，默认3帧，想判断动画必须补读；不假装看完未交付动作。
- 学习继续使用原窗口和证据/复核流程、原知识库及其启停设置；全群共用每小时8次领取预算，同群阶段间隔300秒。已有知识仍可供回复选择，待办不会因预算删除，到期继续。新知识积累速度会下降。
- 明确决定沉默时，下次 wait 传 `silence_batch_id`，或 wait_many 的 `silence_batches={session_id:batch_id}`，由服务端调用原 Planner 记下 SILENT 并确认，省去一次工具往返。没有模型判断时不能自动填；READY/WAITING/SENDING/UNKNOWN 阻止此快捷确认，批次无效不推进游标。
- 多群等待返回绑定当前连接的 `wait_state_id`，原样沿 next_call 续接，避免每次携带8群ID/版本/游标。可同时覆盖 timeout、ACK 或加入下一批明确的 silence_batches。状态重启失效或半小时过期时，以原 session_ids 和实际游标恢复，不能重发历史动作。每次仍重新验证当前授权/会话。
- 空闲沿150秒长轮询，不提交空规划，不反复读会话列表，不输出等待报告。实时新事件仍立即唤醒；未增加静默丢消息或自动回复规则。
- 实时点名与当前话题先于旧积压交付；八群比较就绪优先级后只丰富选中群。`attention.backlog_pending`表示仍保留的旧消息，不阻止当前语境完整的回复。`has_more`只描述当前选择窗口后续，相关材料不全时仍须补读。
- 优先批次确认只完成其实际事件ID，`completed`与连续游标分离；游标不能跨过未处理的旧消息。重启与重复确认保留该状态；UNKNOWN/WAITING不会因此获准发送或被当作已成功。旧缓存裁剪不再误伤材料完整且连接未变的优先回复，真实重连与目标缺失继续拒绝。
- 原生MCP代理可按完整人格和参与度判断空闲时主动接话或开话题，不机械发言。需要自主主动话题时使用原生常驻Agent；下述外部事件运行器在完全空闲时不自行唤醒模型。人格、图像像素、学习参考和主人校验不因优先调度而删减。
- 生产运行器只向模型提供本批数据与按需工具桥接，普通文字的原Planner/发送往返在本地执行。模型仍决定接话内容、复杂度与学习表达选择；详见[生产入口](CHAT_PRODUCTION.md)。
- 通用`chat_bridge.py`新增`__reply_text`：仅当Agent已经决定纯文字及`expression_ids=[]`时，一次工具执行原Planner与原发送，减少一次模型往返；支持本批真实`target_event_id`。需要选择学习候选、多媒体或复杂动作时仍用原两阶段流程。非READY、失效、暂停、缺权、UNKNOWN等结果原样返回，不自动重试或确认。

## 策略与兼容

独立配置为当前数据目录的 `mcp-efficiency.json`，可包含：

```json
{"enabled":true,"images":"on_demand","learning_hourly_calls":8,"learning_interval_seconds":300}
```

界面开关只修改 enabled。images 可设 automatic 恢复自动图片，预算和间隔可调；不会修改原主人设置、群授权、人格、聊天/学习记录。关掉开关恢复双份结果、自动图片和原学习调度；目录摘要仍为默认，旧客户端需要全文应显式 full。

重连 MCP 或新开专用聊天任务，刷新工具参数与说明。工具缓存或静态人格缓存是否仍在，由 Harness 自己负责；只传实际读过的版本，不能凭返回编号声称读过原文。

`scripts/chat_bridge.py` 是可选的标准MCP传输脚本，不运行模型。输入 `{"tool":"__bootstrap"}` 会一次整理最多8个活动会话的初始化数据，完全相同的人格/行为只交付一次，不读取聊天历史。普通调用用 `{"tool":"工具名","arguments":{}}`。它正确解析文本JSON而不是再次打印整个MCP信封；图片保存为真实像素文件供宿主查看，不把base64当文字打印。超过16000字符的完整结果原样保存到当前安装目录 `.tmp/chat-bridge/results/`，返回未读状态与文件哈希，必须分段读完才可确认消息或声称缓存；不会静默删字段或沿续接自动ACK。

若两个会话的人格只是同一原卡的追加要求，初始化会完整交付一次原卡，其他版本用 `inherits_static_version` 和逐字 `persona_append` 无损表示。只有行为完全相同、人格精确前缀匹配才使用，重建后的SHA256必须等于版本；不同原卡仍完整分别交付。不能将该机制理解为删掉群内补充要求或只看一份卡就忽略其他版本。

Harness 的系统、工具和技能本身也计入输入。目录变小不能证明冷启动的模型输入已经变小。支持自定义角色的Codex可以为专用聊天角色关闭无关App/插件/记忆及技能说明注入；这种角色仍继承宿主权限和MCP配置，不能用来改写主人的授权边界。角色能否在当前会话热加载须实际验证，不支持时不能声称已生效或反复冷启动高耗默认代理。

## 验证边界

`scripts/check_fork_token_efficiency.py` 通过实际本机 MCP 与隔离 OneBot 检查摘要/人格复用/正文完整、按需像素与帧预算、静默确认/等待不可跳过、跨连接续接拒绝、主人门禁、UNKNOWN 保留，以及发送重试不重复。学习预算在原 claim 事务内重新检查。

固定八群夹具的传输目录约92800字符降至4145，续接参数943降至52；这是字符及传输量，不是模型账单。真实节省还取决于群活跃、图片选择、Harness工具映射/缓存和模型推理输出。没有模型用量记录时不宣称账单节省相同比例。
