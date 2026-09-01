# Agent、Human port 与 LLM

统一入口：

```text
Participant.decide(DecisionRequest) -> AgentDecision
```

DecisionRequest 只能携带 `TurnContext` 或私有 `ValidationFeedback`。未来 HumanAgent 可以直接消费同一个结构化请求；当前用 contract double 验证该边界，不提供交互 UI。

LLM 分层：

```text
LLMAgent
  → mode
  → LLMProfileRegistry
  → backend_id
  → LLMBackendRegistry
  → LLMBackend.complete
```

OpenAI-compatible 只是 backend driver。DeepSeek 等供应商名称只出现在 RunConfig 的 backend/profile 实例中，不进入 Agent 类型或 CLI 控制流。

LLMAgent 为每个 Character 保存独立 append-only Session。首次调用追加 system 和 turn user；每次响应追加 assistant；拒绝反馈追加 user 后再请求。任何消息都不允许删除、修改、摘要或截断，以保持模型缓存前缀。

Scenario 只保存世界和角色事实。backend、profile、mode 与 cast 映射属于独立 RunConfig；密钥通过环境变量或外部 secret file 引用注入。
