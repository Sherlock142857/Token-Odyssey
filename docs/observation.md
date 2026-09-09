# 观测、数值判定与角色记忆

## 三层职责

1. Fluent 计算空间和感官传播系数，不决定谁知道什么。
2. ObservationSystem 对每个 Cue 采样、授权事实，再调用该 action 的 `compose_observation(facts)` 合并视听证据。合并方法只接收已授权事实，不读取世界。
3. LLM 和网页共用 `render_observation` 转述合并结果，不再各自拼接感官碎片。

没有通用的 full / partial / none 枚举限制。一个动作可以有任意多个不同阈值的 Cue，分别公开不同事实；
实体描述则通过可扩展的命名模式（目前是 scan / inspect）分层。

## 扫视与仔细观察

实体不配置 `perception` 时，旧 `description` 仍在首次辨认时公开。需要保护细节时，
作者将不同粒度写入命名模式：

```yaml
perception:
  scan: {description: 衣袋边露出一角纸张。, salience: 0.25}
  inspect: {description: 信纸上的完整内容。, salience: 1.5, threshold: 0.6}
```

- scan 决定环境扫描时的初步描述；其 salience 乘入对象原有视觉传播系数。
- `inspect(target_id)` 是通过 ActionRegistry 注册的普通动作，不修改世界状态。
- 直接检查已知且可见、未被他人控制的物品，可升级到其 inspect 描述。
- 检查人物、透明容器或打开容器时，只枚举该目标路径下实际有视觉传播的对象；
  每个候选继续使用自己的 inspect salience / threshold 独立授权。
- 藏在人物身上的对象仍受 `concealed_visibility` 影响；不透明关闭容器的传播为 0，
  inspect 不会绕过它。发现对象与读到细节也不会把同批后续动作的未知 ID 变成合法引用。

`Cue.describes` 是动作与感知层之间的通用描述授权接口。感知系统不判断
`event.kind == inspect`，因此未来注册 read / analyze 等动作时可以使用新的模式键，
无需增加专用记忆状态或修改投影主流程。

## Cue 的含义

Cue 包含 Fact、主 anchor、before/after 时点、visual/audio 通道、阈值、salience 和 `clear_in_room`。requires 可以增加其他必要位置锚点。

一个“甲把物品交给乙”的完整事实，需要足够的证据辨认这些对象；不能用其中最大的一项可见度代表整条事实。系统取所有必要锚点传播系数的最小值。

```text
score = clamp(min(各必要锚点传播系数) × salience, 0, 1)
roll  = [0,1) 随机数
若 clear_in_room 且所有锚点都在观察者同房（门可以邻接），且各传播系数均 >= 0.8：
    quality = 1 if roll < score else 0
否则：
    quality = max(0, 1 - roll / score)   # score=0 时 quality=0
授权条件：quality > 0 且 quality >= cue.threshold
```

普通 normal/overt 动作默认启用 `clear_in_room`：在明亮、无遮挡的同房中，发现后会看清细节。subtle、hide、远处或受阻隔的证据继续使用分级抽样。这个策略只用于事件，不改变环境扫描、隐匿物品内容的披露粒度或记忆语义。直接参与者的经验仍用 certain_for 表达。

同一事件中具有相同锚点、时点、通道、显著度、clear_in_room 和额外证据要求的 Cue 共享一次抽样。不同空间事实可以拥有不同证据。

## Action 内的事实合成

- say：听清内容且认出说话者时合成一条具名 speech；只听清内容时保留匿名 speech；没有内容时才保留 speaker 或 voice。不会借客观事件的 actor_id 补全未获准的身份。
- take/give/place/hide/install：有具体操作时省略模糊 handling 和重复 item_location；定位授权仍保留在 EntityView/Memory。
- move：departure 只携带出发房间，arrival 只携带目的房间；两者均获准时合成一条 move。文案直接指明获准的房间，不使用“这里”；移动者只收到自己的到达回执。
- 默认合成：相同事实只保留一次；同 kind 的操作回执补充更多字段时，保留更完整的那条。

视觉和听觉仍分别计算传播权限。合成发生在授权之后，ObservationLog 保存合成事实，perception_samples 保存每条 Cue 的授权依据。机关的互补视听描述在公共转述层组成一段文字。

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

其中“本次扫描辨认成功”的 score 还会乘实体 `perception.scan.salience`；不配置时为1。

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

`perception_samples.jsonl` 保存实际 score、roll、quality、阈值、是否授权和 mode（certain/clear/graded）；这是作者调试文件，包含隐藏锚点，不能发给角色。`observations.jsonl` 保存合成后的授权结果，`views.jsonl` 保存实际决策视图。

回放直接读取已提交变化与这些投影记录，不重新采样。若调整算法后要比较新的观测效果，应开始一次新的运行，不能把它当作原记录的相同回放。

## 当前动作数值

空间传播和采样算法保持不变。为了让普通剧情行为在有轻度视觉衰减时仍容易辨认，动作自带 Cue 的默认阈值降为0.20，同时大幅提高各动作显著度。语音/内容/说话者阈值分别为0.05/0.15/0.15，物品处理和详细位移阈值为0.10/0.20，开闭锁操作为0.20。单独创作的 perception 模式仍保留作者显式设定的阈值。

| 动作 | subtle / normal / overt |
|---|---|
| take、place（通用） | 0.8 / 2.5 / 4 |
| give | 0.8 / 2.5 / 4 |
| hide | 0.3 / 0.8 / 2.5 |
| install | 0.9 / 2.5 / 4 |
| open、close | 0.9 / 2.5 / 4 |
| lock、unlock | 0.7 / 2.2 / 3.5 |
| move、operate、show | 1 / 3 / 5 |
| search、inspect | 0.9 / 2.5 / 4；inspect 的作者自定描述仍用该 perception 模式的独立显著度 |
| say | 0.8 / 3 / 5 |

模糊摆弄阈值0.10、模糊声音阈值0.05；机关视觉0.30、听觉0.10仍由世界机制独立管理。show对象、give双方和自身操作回执使用certain_for；search对本人的有效发现也确定披露，不是保证所有封闭后代都能被搜出。hide 仍关闭 clear_in_room，它的普通精确细节阈值为0.70；subtle 物品转移的精确细节阈值为0.85。因此旁观者更容易察觉“有人在摆弄东西”，刻意隐蔽的对象和去向仍受保护。

say 的合法定向对象在实际声路大于0时得到确定的话语/说话者回执，与show/give的直接互动一致。旁观者的普通同房话语可走清晰分支，隔房和低调话语继续分级抽样。低调不保证私聊。

清晰分支的授权概率为 `min(1, transmission × salience)`；Campaign 房间要求 light≥0.8，所以无遮挡同房的 normal 说话与普通行动会稳定观察和归因。分级分支仍为 `min(1, transmission × salience) × (1-threshold)`。任何必要锚点完全被遮挡（transmission=0）时仍不会被显著度绕过；这次只调整数值，没有改动感知算法。

动作数值用于“能获知什么”；[Router数值](router.md)用于“谁更应先行动”，两者分开。后续用perception_samples核对实际授权率、routing核对调度结果，再决定是否继续调整。
