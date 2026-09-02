# Token Odyssey v2 架构

## 依赖方向

```text
inside_act.domain
  ↑        ↑
actions   observation
  ↑        ↑
harness  context
    \      /
     runner ← router + Participant port
       ↑
agents / recording / interfaces
       ↑
      CLI composition root
```

`inside_act` 不依赖具体 LLM、CLI 或人类界面。Agent 只获得 `DecisionRequest`，其中包含结构化 `TurnContext` 或私有 `ValidationFeedback`，不存在 `WorldState`。

## 权威与不变量

1. `WorldHarness` 拥有 canonical `WorldState`，其他组件只使用 snapshot。
2. TurnPlan 的顺序 actions 在 draft state 上完整规划；每个 action 内部对应一个 committed frame，Harness 对每次 resolve 仍保持原子性。Runner 可在 move 后的环境交互失效时，构造一份截至最后有效 move action 的新前缀计划并单独原子提交。
3. Canonical World Event 只记录实际发生的结构化事实，并以 `source=action|world` 区分角色动作和 WORLD mechanics 响应。私有想法、非法回复、执行 notice 和观察结果存入各自流，不进入 World Log。
4. Observation 只读取 committed frame 的 before/after snapshot，通过 Action anchor 计算投影。
5. Runner 只编排 Participant、Harness、Observation 和监听器；move 截断候选与环境敏感 action 由 Registry metadata 标记，知识引用检查由 Harness 完成。
6. Registry 在 Act 启动前冻结。Action schema、执行、渲染和 prompt metadata 来自同一个 `ActionSpec`。
7. LLM Session 永远只在尾部追加 system/user/assistant 消息；重试不会删除失败分支。

## 一次回合

```text
Router 选择 Character
  → 环境扫描更新该角色的结构化 Knowledge
  → ContextProjector 生成 TurnContext
  → Participant 产生 TurnPlan
  → Harness 按角色已知实体在 draft state 逐 action 规划
  → 对 ActionEffect trigger 计算声明式 WORLD mechanics 响应
  → 原子提交 WorldState 与 action/world Events
  → Observation 按 frame snapshot 投影事件
  → 执行通用 ObservationDirective
```

Participant 输出错误、知识域错误或 Harness 拒绝都会作为私有 feedback 重新请求。已接受的 no-op 和 move 截断作为 execution notice 在角色下次 context 单独投影。超过重试次数后由 Runner 提交静默且独占的 `wait`，只在运行记录中标记 fallback。
