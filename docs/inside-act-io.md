# Inside Act 输入与输出

## Scenario v2

Scenario 必须声明 `schema_version: 2`，包含 Act 背景以及：

```text
world.rules
world.mechanics
world.entities
world.placements
world.room_graph.edges
```

`world.mechanics` 可省略；它声明精确安装配对和设备 operation 的 WORLD 响应，且不直接进入角色 prompt。作者格式也可省略 Room/Character 的统一空间默认值。Scenario compiler 在启动前将其规范化为字段完整、自包含的 canonical WorldState。旧 airpg YAML 不受支持。

## RunConfig v2

RunConfig 与 Scenario 分离，包含 backend driver 实例、LLM profiles 和 Character cast。每个 LLM Character 只引用一个 mode。

## 输出

run artifact schema v2 包括 manifest、规范化 Scenario、初始/最终 state、decisions、World Events、Observation outcomes、trace、token usage、LLM sessions、易读的 `prompt_flow.md` 和 transcript。Decision 使用 outcome 区分 `accepted`、`accepted_with_notice`、`truncated`、`rejected` 和 `fallback`，并单列 execution notices。

Replay 读取所有原始 AgentDecision，以相同 Router/Observation seed 重新运行，并逐项比较 World Events 与 final state。schema v1 不提供读取或转换。
