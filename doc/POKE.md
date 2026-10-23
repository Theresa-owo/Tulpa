# MCP 拍一拍

已获 `qq_interactions` 权限的连接可由模型按语境主动拍一拍当前已核实的群成员或私聊好友，不要求先收到对方拍一拍。

持续聊天先 `plan_chat_reply`，选择 `expressions: ["qq_action"]`，再将以下气泡交给 `send_chat_reply`，沿用返回的 `plan_id`：

```json
{"kind":"qq_action","tool":"qq_interact","action":"send_poke","params":{"user_id":"成员QQ"}}
```

OneBot `notice/notify/poke` 且 `target_id == self_id` 时，通知通过现有会话 inbox 唤醒单群或多群等待。事件保留 `source: onebot_notice`、`synthetic: true`、`notice.operator_id` 和 `notice.target_id`，不是聊天文字或主人指令。模型可选择普通文字、回拍或沉默；服务端不会自动回拍，也不调用内部模型。

文字回复通知须 `quote: false`。通知不能用作消息引用或消息表情回应。回拍继续走成员核验、原授权、Planner、发送队列、停止/撤销检查和幂等回执；UNKNOWN 不换编号重试。

只交付有效授权日期内、当前活动会话收到的通知。自己拍出的回声和群友之间互拍不唤醒模型。重复事件按账号、会话、操作者、目标、时间和适配器事件编号去重；没有细粒度事件编号时，同一秒完全相同的通知可能合并。通知不进入语言学习，也不能替代主人文字授权。重连缺失消息的既有提示继续适用。

验收：`check_fork_mcp_realtime.py` 覆盖 WebSocket 拍一拍唤醒、去重、范围/回声过滤和 Planner 文字发送；`check_mcp_qq_extensions.py` 覆盖主动拍、收到通知后规划回拍及重试去重。均使用隔离 loopback OneBot，不向真实群发送测试消息。
