# 交互加权 Router

实现：`runtime/router.py`；配置：`runtime/routing_policy.py`；连接点：`ActRunner._publish / step`。
默认 `routing.strategy: weighted`。`shuffled` 保留“每轮每人一次”的对照策略；旧 `sealed_chalice.yaml` 显式使用它。

## 输入与边界

`next_actor(actor_ids, recent_events)` 选择一个角色。Runner 在一次动作事务提交、感知投影完成后，调用可选的 `observe(events, observations)`，提交该事务的实际观察结果。旧的自定义 Router 只需实现 next_actor，仍可使用。

Router 不读取 WorldState、角色私人目标、LLM private_thought 或发言语义，不触发第二次感知抽样。输入给 observe 的事件必须已提交，观察必须来自同一事件投影；它是受信任的运行层接口，不能直接接收玩家伪造的 Observation。

只有观察到的 Fact 决定刺激。事件的 listener_ids / observer_ids 仅用于识别定向回应；未知物品、未听见的机关、仅存在于事件 data 中的 ID 都不会提升角色权重。

- give：收到物品的角色有强回应需求；旁观者只按自己看清的交接事实计分。
- say：听清公开话语有一般刺激；实际收到具名定向话语才按交谈对象计分。仅听见声音、没认出说话者，不推断谁在叫自己。
- show：实际展示对象有强回应需求。当前动作只向指定对象投影展示事实。
- take/place/hide：看清物品处理才触发关注对象加成；仅看见有人动手，给予很小刺激。
- open/close/lock/unlock：从观察到的操作提升关注该容器或门的角色；不通过 Router 猜测隐藏锁态。
- search：旁观者只看到翻找；搜索者新发现物品可为其后续行动保留动机。
- install/operate：看到安装或操作，优先关注相关设备的角色。机关反应另按实际看见/听见计分。
- move：离开、到达分别由各地的实际目击者获得刺激；不从离开推断目的房间。
- wait、失败、重复设置等无事务请求：无旁观者刺激。等待/纯 no-op 回合降低本人基础权重，但仍有等待保障。

普通自身动作不为自己加分；自身新发现和世界机关反应可以。一次事务的视觉、听觉线索以及整回合内的多个动作，按每个角色所获**最大刺激**合并，避免多 cue、多动作、机关连锁重复刷分。按事件 sequence 去重。

## 数值

下表对应实际授权的 Fact，不是动作成功概率。

| 观察事实 | 基础刺激 |
|---|---:|
| 模糊声音 voice / 说话者 speaker / 摆弄 handling | 0.20 / 0.30 / 0.25 |
| 公开话语 speech | 0.80 |
| take / give / place / hide | 1.00 / 1.20 / 0.80 / 1.20 |
| show / item_location | 1.00 / 0.40 |
| search / 自身 discovery | 0.70 / 1.50 |
| open / close / lock / unlock | 1.00 / 0.80 / 1.20 / 1.20 |
| install / operate | 1.40 / 1.00 |
| arrival / departure | 1.20 / 0.80 |
| mechanism_seen / mechanism_heard | 1.80 / 1.20 |
| 定向 say / 定向 show / 收到 give | 最低 4.00 / 4.00 / 5.00 |

关注项由 `routing.interests[actor_id][object_id]` 声明，范围 0～2：0 无额外关注，0.5 次要，1 明显相关，1.5 专长/职责，2 核心利害关系。只匹配 Fact 已披露的 `*_id` 字段，取其中最大值，不相加，也不匹配 content/description 自然语言。

```text
fact_impulse = fact_base × (1 + 最大命中的关注值)
若实际定向收到：fact_impulse = max(fact_impulse, direct_floor)
U_i = min(attention_cap, 本轮角色 i 所获最大 fact_impulse)
A_i = min(attention_cap, decay × 上次残留 A_i + U_i)
B_i = 1 / (1 + 0.55 × idle_i)
W_i = B_i + age_weight × age_i + A_i
```

默认 decay=0.65，未消费关注经过 3 次选择剩约 27%，6 次剩约 7.5%；新剧情会逐步替代旧刺激。attention_cap=6，限制强利益相关项和持续对话的累积。age_weight=0.45，每被跳过一次加 0.45。

idle 是最近连续无实质动作的回合数，封顶 3；基础权重依次约 1、0.645、0.476、0.377。收到 U>=1 的新刺激会立即解除等待抑制。它不惩罚短回复、不读取文本，也不把“没触发机关的有效 operate”当作失败。

以六人场景为例，排除刚行动者后，五个候选基础权重均为 1、年龄均为 0：give 接收者权重为 6，其余各 1，选择概率为 60%；定向 say 接收者权重为 5，概率约 55.6%。年龄、已有关注和其他人实际观察会改变这些概率，定向互动不保证下一位必定回应。

## 选择与公平性

1. 衰减已有关注，合并新刺激，计算所有角色的 W。
2. 多人时排除刚行动的角色；单人允许连续行动。避免一次行动后立即再次选中同一人。
3. age 达到 `fairness_rounds × N`（默认 2N）时，候选缩小为超时者中等待最久的人；同龄可按权重随机。
4. 在候选中按 `P_i=W_i/ΣW` 使用独立的有种子 RNG 抽样。
5. 被选者 age 和 attention 清零；其他角色 age 加一。清除本轮刺激缓冲。

固定、持续参与的 N 人场景下，超过阈值后还可能等待其他更久未行动的人。保守上界为 `(fairness_rounds + 1) × N` 次选择内必再获得行动权。人类输入暂停不算新的选择；挂机的人类仍会暂停整个串行运行。

`max_rounds × N` 仍是总行动预算，`rounds_completed=floor(turns/N)` 只是预算单位。加权模式不承诺每个预算轮次人人行动一次。对比实验可用相同 seed 分别设置 weighted/shuffled；两者会改变行动顺序，后续感知抽样次序也会随之变化。

## YAML 与调试

```yaml
routing:
  strategy: weighted       # weighted / shuffled；默认 weighted
  decay: 0.65              # [0,1)
  age_weight: 0.45         # (0,2]
  attention_cap: 6         # [1,12]
  fairness_rounds: 2       # 整数 [1,4]
  interests:
    guard: {manifest: 1.5, medicine_cabinet: 0.8}
```

配置只声明调度倾向，不授予身份知识；仍要用 roles.known_entity_ids 授予合法先验。不要用内部 flag/机制规则 ID 作为关注对象。所有角色与对象引用都会在编译阶段校验。

`routing.jsonl` 记录 turn、actor_id、roll、fairness_override 以及每人 age/base/attention/impulse/weight/probability/reasons。reasons 保存本轮最大刺激的事件序号、Fact 种类、是否定向、关注加成；历史残留已体现在 attention，历史原因从前面的 routing 行追溯。

网页 World Log 的 Router 折叠项显示最近一次选择；观察者 API 缓存最近 20 次，完整历史在磁盘。它属于测试视角，不送给角色。普通玩家只收到自身观察与简报。

## 验证与限制

测试覆盖：定向交谈/展示/交付、各类动作观察、隐蔽信息隔离、关注 ID 门控、重复事件去重、同回合刷动作、衰减、等待唤醒、公平上界、单角色、种子复现及实际 Runner 接线。1200 个种子下，孤立 give 刺激的接收者频率落在理论 60% 附近。

新渡口样本包含五个 NPC，各自有不同关注项；脚本完整通关在 1/7/19/41/99 五个种子验证。固定 seed=41 的脚本/模拟 LLM 传输两种模式均为 42 次行动权、63 个事务、65 条事件。

当前机制描述不会披露 source_id，所以听到铃声只能获得一般刺激，不能凭隐藏声源匹配特定设备关注。这是现有感知字段的边界。Router 不分析谎言、情绪、话题语义，不承诺 NPC 会做出何种行动；实际决策仍由各参与者负责。上述数值是可解释的首版设计与离线验证结果，尚未进行真人或真实模型的体验标定。
