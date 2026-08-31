# 第一阶段架构与不变量

## 分层

| 层 | 职责 | 明确禁止 |
| --- | --- | --- |
| Agent adapter | 根据某角色私有上下文生成 `TurnIntent` | 读取或修改 `WorldState` |
| Context builder | 只向角色自己的 `AgentSession` 追加新 Observation 和行动条件 | 重建、摘要、截断历史，或注入其他角色私有状态 |
| Router / Engine | 洗牌、选择角色、处理重试、编排一次回合 | 自行解释动作是否合法 |
| World Harness | 原子校验意图、修改状态、产生事件 | 使用 LLM 决定事实 |
| Observation System | 对事件和环境进行带 seed 的投影、更新角色知识 | 把 canonical log 整体广播给角色 |

## 状态不变量

1. Actor 直接位于且只位于一个 room。
2. Item 只拥有一个 `Location`；有效 room 通过位置链递归得到。
3. 容器关系不能形成环，目标必须确实是容器。
4. 每次 `TurnIntent` 的动作与对话先整体校验，任一部分非法时不发生任何状态变化。
5. Agent 不能替另一个 Actor 提交意图，也不能操作从未观察过的 item id。
6. 非法意图和私有想法不进入 `WorldEventLog`，但永久保留在该角色的私有 `AgentSession`。
7. 角色的直接对话对象、展示对象和物品接收者必定得到完整观察；其他角色参与观察判定。
8. 同房间空间系数固定为 `1.0`；跨房间矩阵方向为 `observer room -> source room`。

## 观察计算

动作模式系数：

```text
secretive = 0.3
normal = 1.0
conspicuous = 2.0
```

事件投影：

```text
salience = spatial_visibility * mode_factor
full_threshold = clamp(salience * detail_visibility, 0, 1)
partial_threshold = clamp(salience * 1.5, 0, 1)

roll <= full_threshold       -> full
roll <= partial_threshold    -> partial
otherwise                    -> none
```

物品环境扫描使用物品暴露度作为 `full_threshold`。容器内容暴露度会沿嵌套路径逐层相乘；`search` 成功后，行动者直接获得该容器直接子物品的完整私有观察。

## 一次角色回合

1. 对尚未发现或位置可能变化的物品执行环境扫描。
2. 从 `observation_cursor` 之后取得全部新 Observation，作为一条 user 消息追加到该角色会话。
3. Agent 接收完整 append-only 会话，提交一项物理动作和可选对话；原始 assistant JSON 立即追加回会话。
4. Engine 检查观察域，Harness 检查世界合法性。
5. 失败时将原因作为新 user 消息只反馈给该角色，最多重新生成两次；失败分支不会从会话删除。
6. 每一次可解析生成的 `private_thought` 都保留，不区分动作最终是否合法。
7. 先记录对话事件，再执行并记录物理动作事件。
8. 每个事件分别投影给所有角色。
9. `search` 追加私有容器结果；`move` 追加新房间互动对象更新。

## 扩展边界

- `GameEngine.add_observation_listener()` 是玩家终端或图形界面的只读流接口。
- `Agent` Protocol 允许 Human Agent、其他模型或脚本 Agent 使用同一个世界循环。
- Act 结束、剧情 beat、长期摘要和更高层导演应位于当前 Engine 之上，不改变 Harness 对事实的权威。

## AgentSession 与缓存不变量

1. 每个 Actor 对应且只对应一个独立 `AgentSession`。
2. Session 第一条是固定 system message；之后只允许在尾部追加。
3. 已追加消息不会修改、删除、摘要或按数量截断。
4. 每次模型请求的 messages 必须是上一次请求的精确前缀加新增消息。
5. `AgentRuntime.observations` 与 `private_thoughts` 同样完整保留。
6. `prompt_cache_hit_tokens` 和 `prompt_cache_miss_tokens` 必须分别记录，不能只报告总 token。

## Harness package

```text
harness/
├── facade.py
├── registry.py
├── query.py
├── effects.py
├── dialogue.py
├── actions/
│   ├── move.py
│   ├── search.py
│   ├── take.py
│   ├── give.py
│   ├── place.py
│   ├── show.py
│   ├── hide.py
│   └── wait.py
├── observation/
└── spatial/
```

各动作 schema 使用 `kind` 判别联合。Handler 只实现 `validate` 和 `plan`；`plan` 返回包含状态前置条件、状态变更、事件数据及观察显眼度的 `ActionEffect`。Facade 在任何 mutation 前验证整个 TurnIntent，并在提交 effect 后生成 canonical event。

## Run、记录与 replay

- RunRecorder 默认记录初始 Scenario、seed、模型参数、完整 agent message append、每次原始响应、合法性、观察投影、状态变化和 token usage。
- `WorldEventLog` 只包含实际发生的事实；`decisions.jsonl` 和 `agents/*.jsonl` 可以包含私有及非法内容。
- `transcript.md` 是独立的可读话剧投影，不承担调试或 replay 职责。
- Replay 使用保存的 AgentDecision 和原始 seed 重跑，不调用 LLM，并校验事件序列与最终状态。
