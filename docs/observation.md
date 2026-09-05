# 观测、数值判定与角色记忆

## 三层职责

1. Fluent 计算空间和感官传播系数，不决定谁知道什么。
2. ObservationSystem 对每个 Cue 采样，按动作定义的阈值授权事实。
3. 翻译器把授权事实转成模型文字或网页 DTO，不再读取世界。

没有通用的 full / partial / none 枚举限制。一个动作可以有任意多个不同阈值的 Cue，分别公开不同事实。

## Cue 的含义

Cue 包含 Fact、主 anchor、before/after 时点、visual/audio 通道、阈值和 salience。requires 可以增加其他必要位置锚点。

一个“甲把物品交给乙”的完整事实，需要足够的证据辨认这些对象；不能用其中最大的一项可见度代表整条事实。系统取所有必要锚点传播系数的最小值。

```text
score = clamp(min(各必要锚点传播系数) × salience, 0, 1)
roll  = [0,1) 随机数
quality = max(0, 1 - roll / score)   # score=0 时 quality=0
授权条件：quality > 0 且 quality >= cue.threshold
```

score 表示发现机会，quality 是本次证据质量，threshold 是这个事实的披露要求。高阈值并不保证在清晰环境下每次都被旁观者读出；直接参与者的明确经验用 certain_for 表达。各动作的阈值和显著度表都可独立调整。

同一事件中具有相同锚点、时点、通道、显著度和额外证据要求的 Cue 共享一次抽样，因此不同详细程度不会各抽一次、互相矛盾。不同空间事实可以拥有不同证据。

## 事实授权与身份、位置

Fact 是已获准内容。identifies 表示辨认身份；locates 表示确认位置。身份知识不自动包含当前位置。

- 看到 departure 可以知道某人离开，不能因此得到未见的 destination_room_id。
- 听到机关铃声可以获得声音描述，不能因此得到 source Item 的 ID。
- give 的交付双方会确认交付物品，但不会因此知道物品内部隐藏内容。
- only_for 限定某 Cue 只供特定角色；certain_for 只保证这些角色获得这个 Cue。

公开 EntityView 不含世界状态、隐藏规则和内部定位签名。父节点身份未获准时不输出相应 placement。

## 环境扫描

先严格筛选同房的 Character 和 Item，再计算感知；不对其他房间对象抽样，也不生成“附近有模糊物体”之类旁路提示。

当前环境包含：

```text
SameRoom(actor, entity)
AND (
  DirectAttachedOrInsideSelf(entity)
  OR 本次扫描辨认成功
  OR (已有位置记忆 AND 完整位置签名未变 AND 当前传播系数 > 0)
)
```

直接随身物品独立列为 inventory，仅含直接子节点。携带不透明盒子不会递归公开盒中物品。

第三项定义为弱持续定位，记录 source=continuity。它允许随机漏看时维持对原位物品的定位，体现当前 demo 的游戏性选择；不借此刷新未观察到的开闭等动态信息。

位置签名包含祖先路径。箱子被搬动会使箱内物品的签名变化，即使物品的直接 parent_id 没变。容器变得不透明时，即使位置未变，也不再进入当前列表。

没有进入本次列表，不代表旧记忆被清除，也不会自动生成“物品已移动”的新事实。历史位置保留为上次所知；不再每回合把全部旧物品列进当前环境。

## 房间与出口

当前房间和相邻出口属于房间投影，独立于 Item/Character 的同房筛选。出口可从本侧观察到时，会给出名称、目的房间、开闭及通行情况。房间和出口投影也写入 ObservationLog。

本版没有隐藏出口发现算法；可感知的相邻出口作为房间描述提供。需要秘密门时，应先扩展 Passage 的感知/发现状态，再让这些状态进入相同投影入口。

## 观察记录和上下文

ObservationLog 包含 observer_id、世界 revision、来源、可选源事件序号、获准 Fact 和 EntityView。初始角色身份知识与私人背景另由本人的 RoleBrief 提供。

ActorView 包含当前位置、出口、当前物品与人物、随身物品、未消费的观测和执行反馈。LLMTranslator 用自然语言组织它，不发送整个 JSON 视图。HumanTranslator 提供同一授权视图的结构化 DTO。

每次决策的已知 ID 集合固定。队列中搜索得到新知识后，需要等下次决策再使用新发现；内核不会用刚发生的搜索给预先猜测的未知 ID 补授权。

## 调试与回放

`perception_samples.jsonl` 保存实际 score、roll、quality、阈值和是否授权；这是作者调试文件，包含隐藏锚点，不能发给角色。`observations.jsonl` 保存实际授权结果，`views.jsonl` 保存实际决策视图。

回放直接读取已提交变化与这些投影记录，不重新采样。若调整算法后要比较新的观测效果，应开始一次新的运行，不能把它当作原记录的相同回放。
