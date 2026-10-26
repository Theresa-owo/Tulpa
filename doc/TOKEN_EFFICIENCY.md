# Token 开销与省 Token 模式

服务设置中的「省 Token 模式」开启后，完整聊天消息、身份校验、发送队列和既有学习成果继续保留。该开关只调节交付与调度，不切换模型、不扩大连接权限。普通安装默认兼容双份 MCP 结果；本地升级可显式开启省 Token 模式。

## 已实现

- `list_chat_sessions` 默认只列活跃会话摘要，共享 receiver，按 offset/limit 分页。`include_stopped=true` 查询旧会话，`response_format=full` 读取完整记录。目录不再反复交付人格全文。
- `list_chat_personas` 默认仅目录；所选人格由 start/get 交付。首次读取后缓存返回的 `static_version`，其他相同人格会话可在 start/get 传 `known_static_version`；只省略哈希完全相同的人格和行为原文。动态主人身份和群状态仍交付，缓存丢失或版本变化必须补读。
- 普通纯文本在 content 中保留全文，只移除与它完全相同的 text segments 副本；包含图片、@、引用等混合消息保留结构，数据库原文不变。
- 工具总说明缩短，详细规则在具体工具/首次人格和动态提示中；默认 get 使用 compact，full 排查入口保留。
- 开启模式后 MCP 在线传输仅保留一份完整 JSON TextContent 和原图像块，不重复 structuredContent。客户端应读取 structuredContent 或解析第一条文本 JSON；标准文本客户端同样能读取。没有 outputSchema 的工具不依赖 structuredContent。
- 无关图片不自动下载或交付像素。`materials.images[].state=on_demand` 及其 `read_call` 是按需入口，不是拒绝或失败。模型谈论图片前仍须读取真实像素，不能根据笔记猜图。`read_chat_image(max_frames=1)` 只看首个采样帧，默认3帧，想判断动画必须补读；不假装看完未交付动作。
- 学习继续使用原窗口和证据/复核流程、原知识库及其启停设置；全群共用每小时8次领取预算，同群阶段间隔300秒。已有知识仍可供回复选择，待办不会因预算删除，到期继续。新知识积累速度会下降。
- 明确决定沉默时，下次 wait 传 `silence_batch_id`，或 wait_many 的 `silence_batches={session_id:batch_id}`，由服务端调用原 Planner 记下 SILENT 并确认，省去一次工具往返。没有模型判断时不能自动填；READY/WAITING/SENDING/UNKNOWN 阻止此快捷确认，批次无效不推进游标。
- 多群等待返回绑定当前连接的 `wait_state_id`，原样沿 next_call 续接，避免每次携带8群ID/版本/游标。可同时覆盖 timeout、ACK 或加入下一批明确的 silence_batches。状态重启失效或半小时过期时，以原 session_ids 和实际游标恢复，不能重发历史动作。每次仍重新验证当前授权/会话。
- 空闲沿150秒长轮询，不提交空规划，不反复读会话列表，不输出等待报告。实时新事件仍立即唤醒；未增加静默丢消息或自动回复规则。

## 策略与兼容

独立配置为当前数据目录的 `mcp-efficiency.json`，可包含：

```json
{"enabled":true,"images":"on_demand","learning_hourly_calls":8,"learning_interval_seconds":300}
```

界面开关只修改 enabled。images 可设 automatic 恢复自动图片，预算和间隔可调；不会修改原主人设置、群授权、人格、聊天/学习记录。关掉开关恢复双份结果、自动图片和原学习调度；目录摘要仍为默认，旧客户端需要全文应显式 full。

重连 MCP 或新开专用聊天任务，刷新工具参数与说明。工具缓存或静态人格缓存是否仍在，由 Harness 自己负责；只传实际读过的版本，不能凭返回编号声称读过原文。

## 验证边界

`scripts/check_fork_token_efficiency.py` 通过实际本机 MCP 与隔离 OneBot 检查摘要/人格复用/正文完整、按需像素与帧预算、静默确认/等待不可跳过、跨连接续接拒绝、主人门禁、UNKNOWN 保留，以及发送重试不重复。学习预算在原 claim 事务内重新检查。

固定八群夹具的传输目录约92800字符降至4145，续接参数943降至52；这是字符及传输量，不是模型账单。真实节省还取决于群活跃、图片选择、Harness工具映射/缓存和模型推理输出。没有模型用量记录时不宣称账单节省相同比例。
