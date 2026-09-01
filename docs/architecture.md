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
2. TurnPlan 在 draft state 上完整规划；任一 frame 失败时状态、revision 和 World Log 均不改变。
3. Canonical World Event 只记录实际发生的结构化事实。私有想法、非法回复和观察结果存入各自流，不进入 World Log。
4. Observation 只读取 committed frame 的 before/after snapshot，通过 Action anchor 计算投影。
5. Runner 只编排 Participant、IntentGate、Harness、Observation 和监听器，不识别 Action kind。
6. Registry 在 Act 启动前冻结。Action schema、执行、渲染和 prompt metadata 来自同一个 `ActionSpec`。
7. LLM Session 永远只在尾部追加 system/user/assistant 消息；重试不会删除失败分支。

## 一次回合

```text
Router 选择 Character
  → 环境扫描更新该角色的结构化 Knowledge
  → ContextProjector 生成 TurnContext
  → Participant 产生 TurnPlan
  → IntentGate 拒绝未观察实体引用
  → Harness 在 draft state 规划全部 frames
  → 原子提交 WorldState 与 World Events
  → Observation 按 frame snapshot 投影事件
  → 执行通用 ObservationDirective
```

Participant 输出错误、知识域错误或 Harness 拒绝都会作为私有 feedback 重新请求。超过重试次数后由 Runner 提交注册表中的 `wait`。
