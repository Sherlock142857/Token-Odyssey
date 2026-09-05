# 核心算法：绘图用说明

本文对应当前实现，推荐画一张运行总图，再展开“事务闭包”“感知投影”“Router”三个子图。图中将权威世界、角色知识、控制器/调度器分为三个泳道；仅 Harness 的提交节点可以替换权威世界。

## 1. 数据与不变量

| 符号/类型 | 内容 | 生命周期 |
|---|---|---|
| D / WorldDefinition | Room、Character、Item、Passage、组合能力、声明式机关 | 本幕固定 |
| S / WorldState | placements、openings、locks、connections、flags、fired_rules、revision | 仅事务提交更新 |
| K_i / Memory | 角色已知身份、最后位置签名、未读 Observation、执行反馈 | 各角色独立 |
| V_i / ActorView | 决策时的当前位置、出口、人物、物品、背包、观察、反馈 | 一次决策快照 |
| I / Intent | kind、amplitude 和动作专用参数 | 不代表动作已发生 |
| Δ / Change | table、key、before、after | 状态更新与回放共用 |
| E / WorldEvent | source、kind、data、signals、subject_ids、changes、cues、caused_by | 提交后的客观记录 |
| T / Transaction | 一个根动作和全部即时反应；前后 revision | 最小提交边界 |

主要不变量：每个非 Room 实体恰有一个 placement，祖先链无环且终止于 Room；inside 的 Item 父节点必须是容器；物品尺寸符合其所在容器/角色的单件限制；开闭、锁态和 flag 表恰好匹配定义；不能既打开又上锁；一个 Slot 至多一个兼容安装件，connections 对应 attached placement。Passage 连接两个不同房间，没有 placement。

`attached` 是空间关系，`installed` 来自 connections；两者不等价。尺寸约束是单件上限，尚未计算总容积、总负重。

## 2. 运行总图

```mermaid
flowchart TD
    YAML[Scenario YAML] --> Compile[编译、补默认值、校验引用与初始不变量]
    Compile --> Init[创建 Harness、角色 Memory、参与者、Router 和 Recorder]
    Init --> End{结束条件成立或预算耗尽?}
    End -->|是| Final[记录最终状态与运行结果]
    End -->|否且无待处理回合| Route[Router 选择角色 i]
    Route --> View[扫描同房环境、读取未读观察、构造 ActorView]
    View --> Freeze[固定本次已知 ID 集合，创建 DecisionRequest]
    Freeze --> Decide[Human / LLM / Scripted 提交 Decision]
    Decide -->|人类尚未提交| Pause[保存 PendingTurn，等待输入]
    Pause -->|提交对应 request_id| Decide
    Decide --> Parse[校验角色、整个 ActionBatch 的参数形状与长度]
    Parse -->|失败| Retry{还有修正次数?}
    Parse -->|有效| Execute[按顺序执行一个 Intent 的单动作事务]
    Execute -->|拒绝且此前无动作接受| Retry
    Retry -->|有| Repair[附 Issue 创建新 request_id；复用原视图和已知 ID]
    Repair --> Decide
    Retry -->|无| Wait[提交 fallback wait，结束本次行动权]
    Execute -->|拒绝且已有接受前缀| Stop[保留此前提交，记录停止队列反馈]
    Execute -->|接受| Publish[记录事务与事件、投影观察、交给 Router]
    Publish --> Continue{达到结束条件、有效 move 结束策略或队列耗尽?}
    Continue -->|否| Execute
    Continue -->|是| TurnEnd[结束本次行动权，计数加一]
    Stop --> TurnEnd
    Wait --> End
    TurnEnd --> End
    Final --> Replay[可选：按 Change 与已记录视图校验回放]
```

对应 `ActRunner.step / _publish / run`。LLM 的 private_thought 只属于决策记录，不进入 WorldEvent；不同控制器共用相同规则和感知权限。

初始或本次请求的整个 JSON 形状错误，连合法前缀都不会执行。世界条件 Poss 则逐动作检查，前面已提交的状态可影响后面动作。重试仅在本回合还没有动作被接受时发生；被接受的 no-op 也算接受前缀，但不产生事务。默认 max_actions=5、max_retries=2，即最多初次加两次修正。

## 3. 单动作事务与即时反应闭包

```mermaid
flowchart TD
    Input[actor、Intent、决策时 known_ids] --> Auth[角色存在、类型合法、引用属于 known_ids]
    Auth --> Poss[Action.poss：读取当前 Fluent 检查前置条件]
    Auth -->|失败| Reject[返回 Issue，不提交]
    Poss -->|失败| Reject
    Poss --> Effects[Action.effects 生成 EffectPlan]
    Effects -->|无 EventDraft| Noop[接受 no-op，返回 notice]
    Effects -->|有 EventDraft| Draft[复制当前 World 为私有草稿]
    Draft --> Stage[分配事件序号；检查 Cue 引用；应用 Change；校验草稿；保存前后帧]
    Stage --> Queue[事件加入队列]
    Queue --> Consume{还有未消费事件?}
    Consume -->|有| Match[按 YAML 顺序检查 signal、subject、once、when]
    Match --> Reaction{匹配规则?}
    Reaction -->|有| Limit{反应次数超过上限?}
    Limit -->|否| StageRule[按最新草稿生成反应，caused_by 指向触发事件]
    StageRule --> Stage
    Limit -->|是| Rollback[丢弃整个草稿；抛 WorldExecutionError]
    Reaction -->|无/该事件规则处理完| Consume
    Stage -->|引用或世界不变量失败| Rollback
    Consume -->|无| Commit[唯一提交点：替换权威状态、追加 Transaction、revision 加一]
    Commit --> Result[返回事件帧与 rescan/ends_batch 标志]
```

这是示意图：同一触发事件的多条规则先按声明顺序处理完，再从队列取下一事件。每条规则的 when 都对**最新草稿**求值，因此前面的规则可以改变后面的匹配结果；不是先一次性预计算全部匹配规则。

精确伪代码：

```text
验证 actor、Intent、known_ids；执行 Poss
plan ← effects(当前世界)
若 plan.event 为空：返回 accepted + notices
W' ← 世界快照；frames ← []
stage(plan.event)
cursor ← 0；reactions ← 0
while cursor < len(frames):
    trigger_event ← frames[cursor].event；cursor += 1
    for rule in 按声明顺序遍历规则:
        if 信号不符/主体不符/once已触发/最新W'不满足when: continue
        reactions += 1
        if reactions > max_reactions_per_action: 丢弃W'并报错
        stage(reaction(W', rule), caused_by=trigger_event.sequence)
提交 W' 与本事务所有事件
```

stage 保存每个事件自己的 before/after；反应上限默认 32。规则 effects 只支持设置 open/locked/flag；发生实际变化时发 state_changed，once 规则还记录 fired_rules。条件为真本身不驱动规则，必须有匹配的显式信号。

事务失败不提交事件，不产生感知，不消耗感知 RNG；更早的事务仍然保留。有效 wait、未触发机关的 operate 仍有事件。重复 open、重复 place、移到当前房间是接受但不写事务。

## 4. 空间查询与感知授权

Fluents 只读。动作前置条件分开询问“认识”“能看见”“可接触”“可通行”。例如透明关闭容器可看见内容物，仍不能伸手取出；其他角色控制的物品需要由其 give，不能靠看见就 take。

空间传播：祖先包含路径上各阻隔系数相乘；跨房时乘房间图上的最大乘积路径，视觉再乘目标房间 light 与物品 visibility。房间图用最大乘积形式的 Dijkstra 搜索，视觉有方向性，声路与通行权限独立。当前规模优先正确性，未做大型场景图缓存。

对某 Cue 的 observer：

```text
若不在 only_for：忽略
若在 certain_for：quality=1，无抽样
否则：
    T ← min(主锚点及所有 requires 的对应时点/通道传播值)
    p ← clamp(T × salience, 0, 1)
    u ← Uniform[0,1)，p=0 时不抽样
    q ← max(0, 1-u/p)，p=0 时 q=0
授权 ← q>0 且 q>=threshold
```

非 guaranteed Cue 的边际授权概率为 `p × (1-threshold)`。因此显著度不是“成功率”，阈值也不是“达到清晰度后必定成功”。同一事件、同一观察者下，共享 anchor/moment/channel/salience/requires 的 Cue 共用一次抽样，使逐层详细披露使用一致证据。

```mermaid
flowchart LR
    Frames[已提交 EventFrame] --> Filter[逐角色逐 Cue：only_for 与时点/通道]
    Filter --> Evidence[计算多锚点传播，或 certain_for 直接经验]
    Evidence --> Sample[复用同组采样，计算 quality]
    Sample --> Gate{达到该事实阈值?}
    Gate -->|是| Fact[授权 Fact、字段标签]
    Fact --> Identity[identifies 更新身份；locates 才确认位置]
    Identity --> Memory[追加角色 Observation 与未读记忆]
    Gate -->|否| Nothing[不生成该事实]
    Memory --> Router[Router 消费实际投影]
    Memory --> View[下次 ActorView]
```

初始 known_entity_ids 只授予身份与静态描述。事件的 Fact、身份辨认 identifies、位置确认 locates 分别授权；父节点未知时不返回其 placement。隔墙声音不暴露声源 ID，目击离开不暴露目的地。

扫描只枚举同房非自身 Item/Character：直接随身物品必定进入背包，其他按视觉传播概率辨认。未重新辨认、但完整祖先位置签名未变且传播仍大于零时，可保留弱持续定位，不刷新未观察到的动态状态。历史记忆不等于当前可见/可触及对象。

## 5. Router 子图

```mermaid
flowchart LR
    Obs[已提交事件的实际 Observation] --> Gate[按已披露事实识别直接互动与关注对象]
    Gate --> Max[每角色：整回合最大刺激，事件序号去重]
    Max --> Score[衰减旧关注；合并刺激；加基础分与等待年龄]
    Score --> Eligible[排除刚行动者；超时者中优先最久未行动者]
    Eligible --> Draw[候选权重归一化，独立 seeded RNG 抽样]
    Draw --> Reset[被选者清空已消费关注和年龄；其他人年龄增加]
    Reset --> Actor[返回一个 actor_id；记录概率与原因]
```

公式、完整动作映射与数值理由见 [Router 说明](router.md)。Router 是运行层策略，不写世界、不新增内核事件、不复制感知算法。三条随机/事实链须在算法图上区分：世界事务确定性，感知 RNG 决定角色得到什么，Router RNG 决定谁获得行动权。

## 6. 回放与本幕边界

回放从 initial_state 出发，依次校验 Change.before 并应用 after，复核事务顺序、世界不变量与 final_state；读取记录过的 Observation 和 ActorView。不会重跑 LLM、Router 或感知随机数。`routing.jsonl` 是选择证据，`perception_samples.jsonl` 是感知证据，均属于作者调试材料。

目前对象集合固定、机关即时执行；没有实体生成/销毁、战斗伤害、延迟计时器、异步世界写入或自动跨幕继承。磁盘日志是提交后输出，不是数据库级崩溃原子性保证。

跨幕构建 AI 应生成新的可编译 Scenario；哪些实体、事实、私有记忆需要继承应由幕间协调器明确提供。不要在图中画出尚未实现的“LLM直接修改世界”或“自然语言自动成为事实”通路。生成规范见 [场景构建约定](scenario-generation.md)。
