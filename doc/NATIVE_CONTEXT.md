# 原生聊天代理的上下文边界

Tulpa MCP不能删改宿主原生线程的历史。角色文件里的自动压缩参数在部分宿主不会生效；安装了配置不等于发生了压缩。本方案不依赖这个参数，采用**实测输入阈值、关闭发送、安全交接、新原生线程**。不能在越界后resume旧线程，也不能fork旧历史。外部`chat_agent.py`进程仍是另一种运行方式，不可冒充原生子代理。

## 保留的能力与资料

不改变模型、推理强度、工具、宿主权限、人格、参与度、主动话题、接话、看图或主人校验。不提高沉默率来降低用量。交接逐字保留完整人格及群特定追加规则、全部原始工具证据、附件资料和发送回执。模型实际收到的每个图片块按字节保留；工具明确返回的本机原图也按原字节保存，包含全部已有帧，不另行转成OCR、缩略图或单帧替代品。未取得的原图不声称已归档。查历史时图片转换成可用`view_image`读取的持久路径，不把Base64当文字塞回模型。只有需要的证据页进入当前上下文，全部归档仍在本机。历史证据不能提供新的主人授权，也不能触发重放发送或ACK。

每个群必须有显式交接事实：`topic`、`recent_event_ids`、`visual_evidence`、`pending_owner_requests`。空闲群也明确写“当前无话题”，不能省略。原始消息和图片是核对依据，模型备注不是完整历史的替代品。当前人格、主人策略、8会话身份改变时，旧交接禁止直接放行。

保留能力及原始资料可以做确定性验证；随机模型在所有未来话题上的“效果完全相同”不能由一次回归测试保证。缩短活跃历史仍会改变模型的工作上下文，必须如实披露这个差异。原始图片与记录保留并可按需重新读取，不等于它们每一轮都在模型的即时注意范围里。

## 父代理必须执行的流程

这些命令只管理当前明确授权的会话。脚本不会运行模型、创建QQ会话、打开发送、配置定时任务或自行生成原生子代理。父代理使用自己的原生协作工具，负责以下顺序：

1. `collaboration.list_agents`确认当前唯一发送代理和线程ID。把只读`chat_bridge.py __bootstrap`结果保存到私有目录。保留原会话、游标、UNKNOWN回执和学习库。
2. 对已完成初始化的本代原生rollout运行`watch`；它读取真实provider `token_count`，默认单次输入阈值90000，包含缓存的累计用量不得因交接清零。使用进程输出等待返回给父代理，不需要Codex心跳或另一个监控模型。代理每轮同时读取简短状态。
3. 越界时watch先通过本机管理API暂停**匹配原scope和lease_id**的发送，再返回`context_rotation`。旧租约不得关闭替换后的新代租约。父代理只等待/中断这个原生代理，确认它已退出。代理不开始新动作；已派发操作只核对真实回执。UNKNOWN、READY、WAITING、SENDING未解决时不能继续交接。
4. `checkpoint`将完整bootstrap、每群事实、已终止原生线程的工具证据及图片保存到新私有目录。多代使用`--previous-checkpoint`链接旧档，不重复复制旧证据；源线程不得重复计费。脚本不修改原Codex rollout、数据库或QQ游标。
5. `render`返回有界完整bootstrap。父代理用`fork_turns="none"`创建真正的新`tulpa_chat`原生子代理，将render的完整JSON逐字作为引用资料放入任务消息，并附当前用户约束；不能只给版本号或文件路径、不能继承老工具历史。新代理仍禁止发送，读全人格和当前事实后确认全部static_versions。
6. 父代理核对原生列表中的新身份，重新取得只读bootstrap，然后运行`verify`。它要求新线程ID、完整bootstrap确实出现在原生输入中、实际provider输入低于阈值、所有人格版本确认、累计计数保留和当前主人/会话/人格一致。仅写配置或仅声明“已压缩”无法通过。verify从不开放发送。
7. 仅在用户已要求开始聊天且本轮总预算尚有余量时，父代理沿原协议初始化新代发送租约，启动本代watch，继续原8会话。否则保持暂停。父代理在授权的连续聊天任务内处理交接；不能在自己结束后宣称后台会自动生成原生子代理。

安装路径示例（全部是PowerShell 7语法；文件由父代理准备，不要求用户手写JSON）：

```powershell
$tulpaRoot = 'E:\Applications\Tulpa-MCP-0.5.2\Tulpa'
$python = Join-Path $tulpaRoot 'runtime\python.exe'
$contextTool = Join-Path $tulpaRoot 'scripts\native_chat_context.py'
& $python -X utf8 $contextTool watch --rollout $nativeRollout --thread-id $nativeThreadId --previous-total $previousTotal --bootstrap $bootstrapFile --control-root $tulpaRoot --state $contextStateFile
& $python -X utf8 $contextTool checkpoint --folder $newCheckpoint --bootstrap $bootstrapFile --sources $completedSourcesFile --notes $groupFactsFile
# 第2次及后续交接再传 --previous-checkpoint $oldCheckpoint。
& $python -X utf8 $contextTool render --folder $newCheckpoint
& $python -X utf8 $contextTool verify --folder $newCheckpoint --thread-id $newNativeThreadId --rollout $newNativeRollout --static-versions $fullyReadVersions --current-bootstrap $freshBootstrapFile
& $python -X utf8 $contextTool evidence --folder $newCheckpoint --query '之前提到的原文关键词' --limit 4
```

`watch`只会关闭发送，不负责续租或打开租约；沿用当前聊天运行器的续租和故障保护。`--control-root`要求当前本机管理API可用。失败时不生成成功交接；旧租约变更时拒绝影响新租约。若没有运行watch及父代理交接循环，角色模板本身无法限制历史增长，应报告未启动上下文治理。

## 计量与验证边界

原生provider用量在调用后上报，因此预留至少500万Token或8次当前输入加结束余量，提前关闭发送。它是保守停机阈值，不是服务端计费硬上限；已在途的模型调用仍可能产生费用。旧线程最终退出后重新读取完整计数，不能把关闭发送时的计量快照冒充最终用量。

自动检查：`runtime\python.exe -X utf8 scripts\check_native_context.py`。覆盖真实格式的provider计数、分段日志、身份和计数回退、上下文越界锁存、只暂停原租约、多代精确证据、无损人格/图片、缓存清理、UNKNOWN交接拒绝、旧线程复用拒绝、实际bootstrap交付与实际输入阈值、主人策略变更和归档完整性。全部使用隔离本机数据，不调用模型、不访问真实QQ。

这些检查证明交接控制与信息保留；不冒充新代码上线后的真人聊天质量或真实新原生线程测量。用户要求先不聊天时，更新服务后仍保持原8会话发送关闭，不启动聊天代理。
