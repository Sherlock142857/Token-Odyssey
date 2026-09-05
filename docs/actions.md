# 动作协议与扩展

## 批量意图

```json
{"private_thought":"先开锁，再查看。","actions":[
  {"kind":"unlock","lockable_id":"reliquary","key_item_id":"bronze_key"},
  {"kind":"open","openable_id":"reliquary"}
]}
```

private_thought 可省略，只进入该次决策记录。动作类型和字段由各自的 Intent 模型验证；不接受多余字段。actor_id 由当前参与者身份注入，不是动作参数。

每个动作都有可选 `amplitude: subtle | normal | overt`，默认 normal。各动作自行定义显著度响应；这不改变其物理合法性。队列上限由 scenario.turn_policy.max_actions 控制，不写死在解析器中。

| 动作 | 参数 | 判定与直接结果 |
|---|---|---|
| move | destination_room_id；可选 passage_id | 沿相邻可通行通道移动角色；携带物随父链移动 |
| take | item_id | 物品可搬动、可接触；变为 attached 自己，已有安装连接解除 |
| give | item_id, recipient_id | 持有且可接触物品，对方同房可接触；物品交给唯一接收者 |
| place | item_id, destination_id, relation | 把持有物品放到实体表面或容器内部；inside 要求容器打开 |
| hide | item_id | 持有物品且尺寸允许；变为 inside 自己 |
| show | item_id, observer_ids | 向同房、能够看见自己的角色明确展示物品；不改变位置 |
| say | content；可选 listener_ids | 发言；指定对象须同房，其他角色按声音传播感知 |
| search | container_id | 仔细查看可接触且打开的 Item 容器；辨认可见内容，不穿透不透明嵌套容器 |
| open / close | openable_id | 改变门或容器的开闭；open 要求未锁 |
| lock / unlock | lockable_id, key_item_id | 使用自己持有、可接触且匹配的钥匙；lock 要求已关闭 |
| install | item_id, slot_id | 兼容组件 attached 到空 Slot，并创建独立安装连接 |
| operate | device_id | 发出设备操作信号，由机关规则决定响应 |
| wait | 无 | 等待，无可观测 cue |

place 不接受 Character 目的地：交付使用 give，藏到自己身上使用 hide。容器尺寸限制和空间无环约束不能绕过。门是 Passage，因此不是可放置物品的父节点。

`give.recipient_id` 是一个标量；多人分发用多条 give 明确表达每一笔交付。不存在同一唯一物品同时交给多个人的语义。

## Poss 与反馈

Action.poss 返回结构化 Issue。常见原因包括 NOT_COLOCATED、NOT_HELD、LOCKED、WRONG_KEY、CONTAINER_CLOSED、CLOSED_CONTAINER_BLOCKS_ACCESS、CONTROLLED_BY_OTHER、TOO_LARGE。

闭合路径和控制权错误不返回未知容器或实际持有者的 ID；跨房错误不告诉角色目标现在在哪个房间。对应自然语言集中在 `translators/language.py`。

重复开门、移动到当前房间等请求返回 notice，并保持状态不变。合法 operate 没有响应不算非法输入，也不会被自动替换成 search。

## 一个 Action 应该实现什么

- 独立 Intent 模型：严格定义字段与参数类型。
- `references(intent)`：列出需要在作出决定时已经知道的 ID。默认自动收集标量 `_id` 字段，列表需覆盖此方法。
- `check(context, intent)`：通过 Fluent 和公共检查函数验证 Poss。失败使用 `require` / `Rejected`。
- `effects(context, intent)`：返回 EffectPlan，包含 EventDraft、Change 和 Cue；不能修改权威状态。
- `salience`：这个动作的 subtle、normal、overt 显著度。
- 各 Cue 的阈值、空间锚点、感官通道，以及获准事实。

动作无需编写 LLM 上下文字符串，也不需要编辑 Runner 或 Harness 中的动作分支。

## 新增动作示例

下面的 tap 只产生一次真实的敲击事件和声音，不假定敲击改变设备状态：

```python
from typing import Literal
from token_odyssey.kernel.actions.base import Action, EffectPlan, Intent, item
from token_odyssey.kernel.events import EventDraft

class TapIntent(Intent):
    kind: Literal["tap"] = "tap"
    item_id: str

class Tap(Action[TapIntent]):
    kind, intent_type = "tap", TapIntent
    salience = {"subtle": 0.2, "normal": 1.0, "overt": 2.0}

    def check(self, context, intent):
        item(context, intent.item_id)

    def effects(self, context, intent):
        cue = self.cue(intent, "tap_heard", intent.item_id, {},
                       channel="audio", threshold=0.2)
        return EffectPlan(EventDraft(
            kind=self.kind, actor_id=context.actor_id,
            data={"item_id": intent.item_id}, cues=(cue,),
        ))
```

将实例加入 `ActionRegistry`；给 LLMTranslator 的 action_help 加上说明，在 language.py 为 tap_heard 添加文案。如果 scenario 的 scripts 使用它，给 load_scenario 传入同一 registry。组合入口也应使用该 registry。

如果一个动作改变状态，用 `change_to(state, table, key, after)` 构造 Change；支持的状态表在 state.py 明确声明。新增一种持久世界事实时，先扩展 WorldState、不变量和变化类型，再让动作使用它。

## 观测扩展注意

`Action.cue` 会为事实里额外的 `_id` 字段建立额外视觉/听觉证据要求。不能因为一名角色清楚可见，就公开同一事件中所有对象。

`certain_for` 是明确交付、自己操作结果等直接经验；它只保障该 Cue，绝不意味着能够看到整个事件或全部机关内部状态。只听到声音的 Cue 应使用声音描述，避免在 fields 中携带未知物品 ID。
