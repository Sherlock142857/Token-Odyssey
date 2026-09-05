# 编写 Scenario v3

完整参考是 `scenarios/floodgate_dispatch.yaml`（1 玩家、5 NPC、2 房间）。加载器只编译 YAML，不调用 LLM。给场景构建 AI 的完整字段约束、跨幕输入契约和可复制提示词见 [场景生成规范](scenario-generation.md)。

## 顶层结构

```yaml
schema_version: 3
id: my_act
title: 我的场景
public_background: 所有角色都知道的开局背景。
seed: 7
max_rounds: 20
turn_policy:
  max_actions: 5
  continue_after_move: false
  max_retries: 2
routing:
  strategy: weighted
  interests: {}
world:
  entities: {}
  passages: {}
  flag_names: []
  mechanics: []
initial_state:
  placements: {}
roles: {}
cast: {}
scripts: {}
end_when: []
expected: []
```

这段只展示字段布局；有效场景至少需要一个 Room、一个 Character 和全部非 Room 的初始 placement。

| 部分 | 用途 |
|---|---|
| public_background | 所有角色可获知的背景，不能放隐藏机关答案 |
| world | 本幕固定定义；实体、能力、通道、规则 |
| initial_state | 初始位置及动态状态 |
| roles | 各角色独立的人格、目标、记忆和事先认识的身份 |
| cast | 控制器选择，以及可选模型 profile 引用 |
| scripts | 离线驱动用的动作队列，不发送给角色 |
| routing | 加权/洗牌策略和基于实际观察的角色关注项，不授予知识 |
| end_when | 非空时，全部成立就结束运行 |
| expected | 全流程验收要检查的最终条件，不决定角色行为 |

## 实体与能力

ID 使用字母开头的字母、数字、下划线或连字符。映射键就是 ID，可以省略定义中的 id 字段；显式 id 必须与键一致。

```yaml
entities:
  study: {kind: room, name: 书房, description: 桌上有一个带锁盒子。}
  reader: {kind: character, name: 读者, description: 衣袖上沾着墨迹。}
  key: {kind: item, name: 铁钥匙, size: 1}
  box:
    kind: item
    name: 带锁盒子
    size: 4
    portable: false
    container: {capacity_size: 4}
    openable: {open_visibility: 1, closed_visibility: 0}
    lockable: {key_item_ids: [key]}
```

外观 description 可以包含可直接观察的文字、形状、声音；私人用途、兼容结论和目标分别放 roles 或 mechanics。

- Room 可配置 light。
- Character 可配置 size、concealment_size、concealed_visibility。
- Item 可配置 size、portable、visibility，以及 Container、Openable、Lockable、Slot、operable。
- Item 的 Openable 要求 Container；Lockable 要求 Openable。
- Slot 用 compatible_item_ids 声明安装兼容性，这些 ID 不会作为公共能力提示发给模型。

## 初始动态状态

```yaml
initial_state:
  placements:
    reader: {parent_id: study}
    key: {parent_id: reader, relation: attached}
    box: {parent_id: study}
  openings: {box: false}
  locks: {box: true}
  connections: {}
  flags: {}
```

relation 默认 inside。initial_state 不包含实体静态定义。编译器为声明的 Openable 和 Lockable 补默认关闭、未锁值，为 flag_names 补 false；不凭叙述推断缺失位置。

`revision` 开局必须为 0。fired_rules 通常不写；它用于记录本幕已经触发的一次性规则。

## 通道

```yaml
passages:
  study_door:
    name: 书房门
    rooms: [hall, study]
    forward_travel: true
    reverse_travel: true
    forward_visibility: 1
    reverse_visibility: 1
    sound: 1
    openable: {closed_visibility: 0, closed_sound: 0.3}
    lockable: {key_item_ids: [key]}
```

rooms 两个端点必须不同且都为 Room。所有 Passage ID 与实体 ID 分离。门的 openings 和 locks 与容器共用同样的状态表。

## 角色与 API 绑定

```yaml
roles:
  reader:
    personality: 谨慎，喜欢验证线索。
    private_goal: 找到盒子中的信物。
    memories: [以前见过守护者带着一把铁钥匙。]
    known_entity_ids: [keeper]
cast:
  reader: {adapter: llm, profile: standard}
  keeper: {adapter: scripted}
```

known_entity_ids 只授予身份和定义中的感官描述，不授予实时位置。仅在 memories 中自然语言提及一个名字，也不自动生成可执行 ID；需要预先认识的对象应明确列出。

profile 的具体模型和服务配置来自外部 RunConfig。RunConfig.cast 可以覆盖本次运行的角色绑定，省略的角色沿用 scenario.cast；完全未指定时使用 scripted。

## 脚本和终止条件

scripts[actor_id] 是一系列 ActionBatch。每次被选中（包括输入重试）取下一批；耗尽时 wait。多角色脚本需要自行安排依赖顺序，不能依赖其他人尚未执行的动作已发生。

end_when 和 expected 都使用与机关相同的 Predicate 词汇。例如：

```yaml
end_when:
  - {kind: inside, subject_id: reader, object_id: inner_room}
expected:
  - {kind: flag, subject_id: mechanism_active}
  - {kind: locked, subject_id: box}
```

普通 run 到轮数上限可以正常返回 limit_reached；不代表谜题已解决。selftest 则检查 expected，未满足时返回失败。真实模型的策略可能与离线脚本不同，也可能未在预算内完成目标。

## 编译检查

加载阶段拒绝重复 YAML 键、未知实体/钥匙/通道引用、非容器 inside、空间环、缺失 placement、开着且上锁、非法安装连接、未知 flag、错误规则参数和脚本形状。

脚本只在加载时验证字段和实体引用，不预演未来动作的 Poss。这样可以编写具有真实前后依赖的队列，也可以像新场景那样故意包含一个世界条件失败来验证成功前缀保留。

对象集合在本幕中固定。本版没有实体生成、销毁、角色离场或跨幕记忆算法；新增这些能力时需要先定义相应世界变化和观测语义。
