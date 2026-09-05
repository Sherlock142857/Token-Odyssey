# Agent、翻译器与 API

## 通用参与者接口

`Participant.decide(DecisionRequest) -> Decision` 是脚本、LLM 和 Human 共用的入口。

DecisionRequest 包含 request_id、actor_id、授权 ActorView，以及修正输入时的结构化 issues。Decision 只包含 actor_id 和 ActionBatch，或者解析错误。它不要求 raw_content、模型名或 token 信息。

运行者不会将 WorldState 交给 Participant。Human 与 LLM 使用相同的身份检查、引用知识检查和 Poss，不存在玩家专用物理权限。

## LLM 三个部件

| 部件 | 工作 |
|---|---|
| LLMTranslator | 生成 system prompt、将 ActorView 转为自然语言、将模型 JSON 回复转为 ActionBatch |
| LLMAgent | 保存私有消息会话，发起请求，处理传输失败，提交诊断记录 |
| LLMBackend | 传输层；输入 LLMRequest，返回统一 LLMResponse |

上下文翻译是确定性的模板代码，不需要多调用一次模型。自然语言与错误文案位于 `translators/language.py`，上下文布局位于 `translators/llm.py`。

模型输入分为当前位置、出口、随身物品、当前人物、当前物品、最近事件和执行反馈。这里只列授权信息，不发送 Canonical WorldState JSON，也不把所有旧物品记忆每回合重复列入环境。

模型回复仍为 JSON；支持移除完整的 Markdown 代码围栏，但不会从任意混杂叙述中猜出一个动作。字段错误会作为私有反馈要求修正。

会话目前保留追加的历史消息，重试不删除旧回复。它是一项外层实现选择，不是世界内核不变量；后续如需摘要、检索或 token 预算管理，应在 LLMAgent 或独立记忆策略中实现，不能改写客观世界日志。

## API 配置

```yaml
schema_version: 3
backends:
  service_a:
    driver: openai_compatible
    base_url: https://your-provider.example/v1
    api_key_env: AIRPG_API_KEY
profiles:
  standard:
    backend_id: service_a
    model: your-model-id
    temperature: 0.8
    max_output_tokens: 1200
cast:
  seeker: {adapter: llm, profile: standard}
```

backend 声明传输服务和一个凭据来源：api_key_env 或 api_key_file，二者只能选一个。密钥文件路径相对配置文件所在目录解析，文件必须恰好有一行非空内容。不要把密钥写入角色背景或 scenario。

profile 声明模型参数；`extra` 可透传供应商支持的扩展请求参数。多个 profile 可以复用同一 backend，多个角色也可以选择不同 backend。composition 只建立本次角色实际需要的传输实例。

新增供应商时实现 `complete(LLMRequest) -> LLMResponse`，再在 `runtime/composition.py` 的 BACKEND_FACTORIES 注册工厂。新增能力不需要给 Runner 加模型名称分支。

## 诊断输出

LLMAgent 通过显式 on_exchange 回调提交 LLMExchange：实际请求、实际回复、usage 和可选传输错误类型。Recorder 无需读取 Agent.messages 等内部属性。

`llm_exchanges.jsonl` 保留完整请求，便于精确检查；`prompt_flow.md` 展示每次新增输入和输出，便于调整文字布局；`token_usage.json` 汇总各角色的供应商报告用量。离线模拟没有真实 token 计费数据。

运行记录包含各角色的私人目标和模型私有对话，属于作者调试产物。它们不能作为某个角色的前端数据源；前端只能消费 HumanTranslator.present 返回的授权 DTO。

## Human 接口

HumanAgent 是内存中的非阻塞适配器。localhost [网页测试台](web.md) 按以下流程接入：

1. Runner.step 选择这个角色，HumanAgent 发布 pending_request，返回 waiting_for_input。
2. 网页后端调用 HumanAgent.present，取得 request_id、授权视图和错误信息。
3. 玩家点击动作按钮或填写表单，后端将字段传给 HumanAgent.submit(request_id, actions)。
4. 再次调用 Runner.step，恢复同一角色的当前行动权。

```python
human.submit(request_id, [
    {"kind": "give", "item_id": "bronze_key", "recipient_id": "seeker"}
])
runner.step()
```

这里的字典由网页代码构造，不要求玩家手写 JSON。过期 request_id、重复提交、尝试替别人行动会被拒绝。等待期间再次 step 不会重新路由、扫描或推进世界。

CLI `run` 进程返回 waiting_for_input 后不提供跨进程恢复命令；`web` 子命令保持同一个 Runner / HumanAgent 实例，支持网页刷新后继续。多用户身份会话和服务重启恢复尚未实现。

## Router

`next_actor(actor_ids, recent_events)` 每次只返回一位角色。recent_events 是自上次选择之后已提交的事件，不包含失败意图或别人尚未看到的“模型计划”。

默认 InteractionWeightedRouter 消费已提交事件的实际感知投影，结合定向回应、关注对象、刺激衰减和等待补偿选择角色。不会读取私人目标或用隐藏事件推断目击者。`routing.strategy: shuffled` 可恢复每轮人人一次的基线。数值、接口和选择证据见 [Router 说明](router.md)。

模型和人类都可以一次提交多个动作，但不会同时修改世界，也不会在当前队列执行中插入另一位角色的行动。
