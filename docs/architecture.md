# 架构与职责

## 世界与控制器

Character 是游戏内角色，LLM、Scripted、Human 是控制器。控制方式不参与任何物理规则或观测权限判断。每个角色只提交意图，只有 WorldHarness 可以替换权威 WorldState。

```text
common
  ↑
kernel/definitions + state + fluents + events
  ↑                         ↑
kernel/actions + mechanics  perception/models + system
  ↑                         ↑
kernel/harness              agents/contracts
          \                /
              runtime/runner ← router
                   ↑
          runtime/composition
           ↑               ↑
      CLI / verification    agents + translators + llm
```

`kernel` 不导入 agents、translators、recording 或 CLI。所有模型 API 类型在 `llm/contracts.py`，通用 Decision 没有原始模型回复或 token 字段。

## 模型归属

| 模块 | 内容 | 不负责 |
|---|---|---|
| `kernel/definitions.py` | 固定实体集、组合能力、Passage、机关定义 | 当前开闭、锁态、位置 |
| `kernel/state.py` | WorldState、Change、World、不变量 | 决定角色做什么 |
| `kernel/fluents.py` | 同房、控制、可接触、通行、感知传播、声明式条件求值 | 写入状态、随机抽样 |
| `kernel/actions/` | 类型化 Intent、Poss、直接效果、观测候选 | 提交状态、LLM 文案 |
| `kernel/mechanics.py` | 从信号和条件生成即时反应 | 私下写世界或日志 |
| `kernel/harness.py` | 单动作事务、验证与提交 | 整个行动队列、模型重试 |
| `perception/` | 感知抽样、事实授权、位置记忆、ActorView | 修改物理事实 |
| `runtime/runner.py` | 路由、逐动作执行、反馈、输入暂停、发布记录 | 按动作类型修改世界 |
| `translators/` | 自然语言/网页 DTO 与通用意图之间的转换 | 查询权威 WorldState |
| `recording/` | 明确接收记录、回放已提交变化 | 查看参与者私有实现 |

`World` 组合 definition 和 state，提供 snapshot 和 validate。definition 使用冻结模型；Harness 对外返回深拷贝，使外部字典修改也无法影响权威状态。当前 demo 的世界很小，优先保持这条边界清楚，不引入快照缓存或增量持久化框架。

## 一次动作事务

1. Harness 检查角色身份、意图类型以及本次决策时已知的引用。
2. Action.poss 通过 Fluent 读取当前状态，返回结构化 Issue。
3. Action.effects 返回 EventDraft；事件中的 Change 描述直接效果。
4. Harness 在草稿上应用 Change，检查不变量，保存该事件自己的前后快照。
5. MechanicsEngine 消费事件信号，按 scenario 顺序检查规则，在最新草稿上产生后续事件。
6. 重复处理事件队列，直到即时反应结束；超过反应上限则报世界执行错误。
7. 一次提交草稿状态和 Transaction。每个事务的 revision 增加一。
8. Runner 记录事务，逐事件进行观测投影，再处理后续动作。

直接动作和所有即时机关反应只有一个提交点；各事件仍保留顺序与 `caused_by`。世界变化和回放都使用相同的 Change 数据，而不是分别维护“效果实现”和“描述性日志”。

语法无效和 Poss 失败不会产生虚假的物理事件。合法 operate 没有触发机关，仍是一次发生过的操作尝试。wait 形成一个无感知 cue 的事件。重复 open 等无效果请求返回 notice，不创建事务。

世界不变量或机关循环出错是 scenario/实现问题，会抛出 WorldExecutionError，当前动作不提交。它不作为可供模型反复猜测的普通行动错误处理。此前的事务不受影响。

## 队列与重试

ActionBatch 是外层概念。解析器在执行前检查整个队列的参数形状；Poss 在每一步真正执行之前检查世界条件。

- 首个动作就失败，或模型回复无法解析：可以请求修正，最多 `max_retries` 次。
- 至少一个动作已接受，随后失败：停止队列，下一次行动权收到具体失败和成功数量；绝不重做前缀。
- 移动真的发生且 `continue_after_move=false`：结束队列，剩余动作取消。
- 移动到当前房间是 no-op，不触发“成功移动结束队列”。
- 重试耗尽：记录一次 fallback wait。

已知 ID 集合固定在本次 DecisionRequest 生成时。open/search 可以更新角色记忆，但预先写在同一队列里的新 ID 不会因此被“猜中即授权”。新的决策才能使用新发现。

## 客观记录与主观记录

WorldLog 是 Transaction 序列，描述发生了什么。ObservationLog 描述谁实际获得了哪些事实；包括事件、扫描、弱持续定位以及房间/出口视图的依据。角色初始背景与既有身份知识来自自己的 RoleBrief。

ActorView 将这些主观记录和当前扫描结果组织成输入，不暴露整张世界表、机关条件、其他角色的目标、隐藏位置签名。角色说出的内容只是一条 speech 事实，不会自动变成内容所描述的世界状态。

磁盘记录是提交后的下游输出。本 demo 保证内核的单动作事务语义，不承诺进程崩溃时具备数据库级持久化原子性。
