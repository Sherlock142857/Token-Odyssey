# 场景构建 AI：Scenario v3 生成规范

这是用于后续多幕构建的输入输出约定，按当前代码能力编写。可直接复用末尾的生成提示词。正式载入入口是 `compile_scenario(raw)` / `load_scenario(path)`；YAML 和对应 Python/JSON 数据具有相同字段，但 YAML 编译器会补部分默认值。

可运行参考：[雨夜渡口](../scenarios/floodgate_dispatch.yaml)，1 玩家＋5 NPC、2 房间；[封存圣杯](../scenarios/sealed_chalice.yaml) 保留为旧机制回归样本。Router 数值见 [router.md](router.md)。

## 1. 构建器输入契约

幕间协调器应明确提供以下信息，缺失部分要标为待设计，不能假装来自上一幕。

| 输入 | 要求 |
|---|---|
| 本幕任务 | act ID、主题、玩家角色 ID、公开开场、预期冲突、预算 |
| 出场对象 | 需要保留的角色/道具稳定 ID、允许新增的对象、房间数量 |
| 已确认世界事实 | 上幕提交后的允许继承状态；权威来源应是 final_state/事务记录 |
| 各角色私人延续 | 分角色提供获准记忆、身份先验、目标变化；与公共摘要分离 |
| 叙事约束 | 不可改写的事实、未解决承诺、允许发生在幕间的变化 |
| 运行约束 | 支持的动作/机制词汇、可用 profile 名、是否需要离线验收脚本 |

当前运行时没有自动跨幕继承器，以下继承流程是调用方应实现的构建协议。新 YAML 始终是一幕的完整初态，不能只给上一幕的增量补丁。

## 2. 输出形状

输出单个 UTF-8 YAML 映射。禁止重复键、未知字段、自定义 YAML 标签、嵌入代码、API 密钥和自然语言伪指令。中文叙述使用普通字符串；有冒号、换行时使用引号或 `>-`/`|`。不要把 boolean 写成带引号的字符串。

```yaml
schema_version: 3
id: unique_act_id
title: 本幕标题
public_background: 所有人开局均可知道的背景。
seed: 41
max_rounds: 24
turn_policy: {max_actions: 5, continue_after_move: false, max_retries: 1}
routing:
  strategy: weighted
  interests: {}
world:
  entities: {}          # 有效输出必须填写，见下文
  passages: {}
  flag_names: []
  mechanics: []
initial_state:
  placements: {}        # 每个非 room 实体恰好一个位置
  openings: {}
  locks: {}
  connections: {}
  flags: {}
  fired_rules: {}
  revision: 0
roles: {}
cast: {}
scripts: {}
end_when: []
expected: []
```

这是字段布局，不是可运行场景。至少需要一间房和一个角色。id、title、world、initial_state 是模型所需字段；schema_version 必须显式写 3 才能经过编译器。

| 顶层字段 | 默认/范围 | 作用 |
|---|---|---|
| public_background | 空字符串 | 全员公共先验 |
| seed | 7；建议使用网页支持的 0～2³²−1 | 感知和路由的复现种子来源 |
| max_rounds | 20；1～10000 | 总行动预算=max_rounds×角色数；网页上限1000 |
| turn_policy.max_actions | 5；1～50 | 每次提交动作上限 |
| turn_policy.continue_after_move | false | 有效移动后是否继续原队列 |
| turn_policy.max_retries | 2；0～10 | 无动作接受时允许追加的修正次数 |
| routing | 默认 weighted | 调度倾向；不会赋予角色知识 |
| roles / cast / scripts | 空映射 | 角色私有简报、控制器绑定、离线动作序列 |
| end_when | 空列表 | 非空时全部成立立即结束；空则只有预算停止 |
| expected | 空列表 | 验收最终状态；selftest 要求非空 |

生成游戏样本应为每名角色填写 roles，并给出非空 end_when/expected。配置可选字段的“省略合法”不代表一个可玩的场景就应省略它们。

## 3. 实体、能力与通道

所有实体/Passage ID 使用 `[A-Za-z][A-Za-z0-9_-]*`；映射键可代替对象内部 id，显式 id 必须与键一致。统一使用稳定的英文 snake_case，中文显示名放 name。实体和 Passage 的 ID 集合不能重叠。

### 实体字段

| kind | 字段 |
|---|---|
| 所有实体 | id（可省略，编译时补）、kind、name（非空）、description（默认空） |
| room | light：0～1，默认1 |
| character | size：1～10，默认6；concealment_size：1～10，默认3；concealed_visibility：0～1，默认0.3 |
| item | size：1～10，默认2；portable：默认true；visibility：0～1，默认1；下列可选组合能力 |

| Item 能力 | 结构及约束 |
|---|---|
| container | `{capacity_size: 10, visibility: 1}`，capacity_size为1～10；限制每个内部对象的尺寸，非总容量 |
| openable | `{open_visibility: 1, closed_visibility: 0, closed_sound: 0.3}`；系数0～1；Item 必须同时是 container |
| lockable | `{key_item_ids: [key_id]}`，至少一个现有 Item ID；必须同时 openable |
| slot | `{compatible_item_ids: [component_id]}`，至少一个现有且非自身 Item ID；每插槽最多安装一个组件 |
| operable | boolean，默认false；只有true才接受 operate；不会自动产生开关状态 |

`{}` 可表示使用 container/openable 默认参数。固定家具要设 portable:false；普通桌面可为无 container 的 Item，物品用 attached 放上去。container 无 openable 时视为常开。门建为 Passage，不要复制成两个房间内各一扇独立 Item。

公开 description 只写可辨认的外观、铭文和线索。物品一旦被认识，description 可能作为先验下发；不得夹带秘密内容物清单、机制 rule/flag ID、他人秘密或不应公开的幕后答案。稳定外貌放角色 description，当前地点放 placement，私有经历放 memories。

### Passage

```yaml
passages:
  store_door:
    name: 器材库木门
    description: 门上有一道旧划痕。
    rooms: [office, store]
    forward_travel: true
    reverse_travel: true
    forward_visibility: 1
    reverse_visibility: 1
    sound: 0.8
    openable: {open_visibility: 1, closed_visibility: 0, closed_sound: 0.25}
```

rooms 必须是两个不同的现有 Room ID。通行方向默认均 true，视觉与 sound 默认1，范围0～1。forward 表示 rooms[0]→rooms[1]。可选 openable/lockable，与 Item 同样要求有锁就可开闭，但 Passage 不需要 container。

通行、视觉、声音彼此独立；关闭门仍可能传声。现有出口投影会公开可感知相邻出口和目的房间，因此不能只在 description 里称“秘密门”就期待自动隐藏。

## 4. 初始状态必须满足的约束

1. 每个非 Room 实体在 placements 中恰有一条；Room 和 Passage 没有 placement。
2. `parent_id` 指向现有实体，relation 只能 inside/attached，默认 inside；不能形成自环或祖先环，整条链必须最终到 Room。
3. 角色通常直接 inside 所在 Room。附身可见物品用 attached，衣内藏物用 inside。携带物品的间接子节点也受角色控制。
4. 放进 Item 内部要求父 Item 有 container，尺寸不超 capacity_size。放入角色内部不超 concealment_size；超大药箱不能 hide。
5. openings 的 key 恰好为所有 openable 对象；locks 恰好为所有 lockable 对象。编译器补缺省 false，未知 key 会失败。开启与上锁不可同时 true。
6. flags 的 key 恰好匹配 flag_names，缺省 false；flag_names 不得重复。值用真正的 boolean。
7. connections 是 `{component_id: slot_id}`；兼容、唯一占槽，并且该组件 placement 必须 `{parent_id: slot_id, relation: attached}`。只写 attached 不构成安装。
8. revision=0；fired_rules 默认空，只能记录本幕 once:true 的现有规则 ID，值必须 true。

不要把“门开着”“钥匙在某人手里”只写在叙述中：必须落实为这些状态。锁钥匙只须是 Item，不存在特殊 `kind: key`。

## 5. 动作词汇与脚本

所有动作通用字段为 kind 和可选 amplitude（subtle/normal/overt，默认normal）。不得创建新的动作名或别名字段。

| kind | 专用字段 | 关键条件/含义 |
|---|---|---|
| move | destination_room_id；passage_id可选 | 邻接且允许通行、门已开；默认有效移动结束队列 |
| take | item_id | 可搬动、可接触；取下安装件同时拆除连接 |
| give | item_id, recipient_id | 持有、可接触物品；对方同房且非自身 |
| place | item_id, destination_id, relation | 持有物品放房间/Item；inside/attached；对角色使用 give/hide |
| hide | item_id | 持有且可接触的小物品，变为 inside 自身 |
| show | item_id, observer_ids | 持有且可接触；非空角色列表、同房、能看见展示者 |
| say | content, listener_ids可选 | 文本1～4000字符；定向对象同房且非自身；空列表为公开话语 |
| search | container_id | 打开的、可接触的 Item 容器；发现结果下次决策才能用于新 ID |
| open / close | openable_id | 容器或 Passage；打开前必须未锁，关闭不自动锁 |
| lock / unlock | lockable_id, key_item_id | 持有可接触的匹配钥匙；上锁前需关闭；解锁不自动打开 |
| install | item_id, slot_id | 持有可接触的兼容组件、可接触的空插槽 |
| operate | device_id | 可接触的 operable Item；只是操作尝试，反应取决于机制 |
| wait | 无 | 结束一次等待动作，无旁观者观察刺激 |

say 不改变其文本描述的事实，不直接触发“说出口令开门”。listener_ids 不是私聊权限。show 不交付物品。当前没有 inspect/read/use/attack、任意 effect 参数或自动把自然语言解释成动作的内核逻辑。

```yaml
scripts:
  seeker:
    - actions:
        - {kind: unlock, lockable_id: strongbox, key_item_id: station_key}
        - {kind: open, openable_id: strongbox}
        - {kind: search, container_id: strongbox}
    - actions:
        - {kind: take, item_id: manifest}
```

scripts[actor] 是 ActionBatch 列表，每次该角色决定时取下一批，包括失败重试；耗尽后 wait。ActionBatch 可有 private_thought，但它不会变成世界事件。脚本编译仅验证参数形状、引用和长度，不预演未来 Poss。

同一批不得“先 search 再猜未知 item_id 来 take”：本次已知 ID 集合在决策开始时固定。先验已经认识的对象可以在同批使用，前提是动作执行到该处时满足真实可接触条件。

加权 Router 不保证轮次内人人一次，脚本不能把“甲的第三次动作”绑定为“乙必已做过两次动作”。关键链最好集中在一个驱动者；其他 NPC 操作自身物品和独立设备。需要多人交接依赖时，用实际观察驱动的参与者或专门测试调度器验证，不通过大量无意义 wait 假装可靠同步。

## 6. 机制、Predicate 与终止

```yaml
world:
  flag_names: [stock_released]
  mechanics:
    - id: emergency_release
      trigger: operated
      subject_id: winch
      when:
        - {kind: installed, subject_id: crank_pin, object_id: winch}
      effects:
        - {kind: locked, subject_id: medicine_cabinet, value: false}
        - {kind: flag, subject_id: stock_released, value: true}
      once: true
      source_id: winch
      visual_description: 钢索绷紧，门闩向侧面缩回。
      sound_description: 齿轮转动后传来一声脆响。
      visibility: 1
      audibility: 1
```

trigger 仅支持 placement_changed/state_changed/operated。subject_id 可省略，表示不按具体对象筛选；否则是现有实体、Passage 或 flag ID。source_id 必须是现有实体/Passage。when 默认空（恒真），effects 默认空，once 默认true，感官描述默认空；visibility/audibility为0～1，默认1。

规则 id 必须唯一。同一条规则不能重复设置同一事实。when 中所有条件 AND，value:false 否定单个条件；无 OR、数值比较、变量绑定或任意表达式。需要 OR 时拆成多条触发/条件清楚的规则，并处理一次性行为。

| Predicate kind | 参数与语义 |
|---|---|
| inside / attached | subject_id、object_id、value默认true；只比较直接 placement，不表示祖先包含 |
| installed | subject_id为组件、object_id为兼容Slot；检查 connections |
| open / locked | subject_id必须有相应能力，不写object_id；value默认true |
| flag | subject_id须在flag_names，不写object_id；value默认true |

effects 仅支持 open/locked/flag，只有 kind、subject_id、必填value。不能移动物品、生成实体、销毁实体、发起LLM调用或写入角色记忆。物品安装/转移用角色动作；机制只处理当前支持的状态反应。

规则在显式事件信号到来时求值，不会因初态条件为真而自行启动。placement_changed 来自移动/物品位移，state_changed 来自开闭/锁态和机制实际变化，operated 来自 operate。一次事务内依声明顺序在最新草稿上匹配、追加即时反应，默认最多32次（world.max_reactions_per_action，可设1～1000）。不得构造没有稳定终点的重复规则环。多个 effects 共同应用后必须满足世界不变量，例如锁门同时需保证门关闭。

end_when 与 expected 使用同一 Predicate。所有必需收尾条件必须同时具备“角色可以获知的目标/线索”和“结束前实际可达的条件”。若 expected 是必需验收条件，应让 end_when 或设置最终 flag 的规则包含它，避免程序先结束而永远没有机会复锁/还钥匙。

尽量为成功和未满足前提的设备提供可理解的叙事线索；没有反应的 operate 是合法尝试，不自动返回“缺少哪枚秘密零件”的答案。不要通过 sound_description 暴露未获准的远处物品/角色身份。

## 7. 角色知识与 Router

```yaml
roles:
  guard:
    personality: 寡言守纪，见到实际凭据才下判断。
    private_goal: 收到原始账册后保管，询问异常出库记录。
    memories: [你曾核验过药柜库存。]
    known_entity_ids: [medicine_cabinet, manifest, seeker]
routing:
  strategy: weighted
  interests:
    guard: {manifest: 1.5, medicine_cabinet: 0.8}
cast:
  guard: {adapter: scripted}
```

RoleBrief 仅支持 personality/private_goal/memories/known_entity_ids，默认空。memories 是字符串列表；自然语言提及名字不自动授权可执行 ID。known_entity_ids 允许实体及 Passage，只授身份/静态描述，不授实时位置、锁态、内部物品或机关条件。每个人只收到自己的 RoleBrief。

关注表的角色/对象引用必须存在，数值0～2。关注 ID 不等于先验认识，未知对象可以先配置利害关系，但只有将来实际观察到才生效。不要人人对所有东西设2；为每人挑2～4个有职责、利益或关系依据的对象。角色私有目标用自然语言，Router 不读取/解析它们。

cast adapter 仅 scripted/human/llm；仅 llm 要求非空 profile，其他两种不得携带 profile。具体 profile 和后端凭据属于外部 RunConfig。覆盖顺序为 RunConfig.cast > scenario.cast > scripted 默认。网页启动表单最终决定绑定；当前网页优先 seeker（没有则首个角色）为默认人类，配置profile后其余可用LLM。

为了离线开箱可运行，样本可全部绑定 scripted；角色在故事中是玩家/NPC，与本次是否由Human/LLM/Scripted控制相互独立。RunConfig.cast 不可保留本幕不存在的上幕角色 ID。

## 8. 多幕衔接规范

- 为延续角色/物品保留稳定 ID，但每幕重新列出完整实体定义及所有本幕非 Room placement。
- 不盲目复制上幕 openings/locks/flags/connections：只迁移仍在本幕定义中的有效事实；变更实体能力后重新校验。迁移安装件时必须同时迁移插槽与对应 placement。
- 不复制旧 revision；新幕从0开始。旧 fired_rules 是上一幕即时机关账本，不默认继承；若需延续后果，用本幕显式 flag/初态表达，并决定哪些 once 规则已经视为触发。
- 私人记忆按角色实际 Observation、本人行动/收据和明确授权的幕间信息总结。公共摘要不得拼接各人全部私密信息；角色说过“门已开”只能是说法，不能盖过权威锁态。
- 原生 known_entity_ids 只能引用本幕实体。离场角色若仅需在记忆里提及，用文字历史；不能把未定义旧 ID 放入可执行先验或routing.interests。
- 历史位置应写成“上次见到在……”的记忆，不能伪装成新幕当前位置；新幕扫描会重新建立位置证据。
- Router 的关注、等待年龄与随机状态目前每幕重置。个性关注项可重新生成，但这不等于跨幕调度状态恢复。
- 尚未实现的幕间移动、丢失物品、死亡等变化应由上层明确提供事实和原因，再编译新初态；构建 AI 不得无授权改写已确认结局。

## 9. 生成后验收

```bash
.venv/bin/python -m token_odyssey validate scenarios/floodgate_dispatch.yaml
.venv/bin/python -m token_odyssey selftest --scenario scenarios/floodgate_dispatch.yaml --runs-dir /tmp/scenario-check
```

validate 通过只说明结构和初态合法。还要沿真实合法动作做可达性检查：钥匙不能永远锁在唯一能开自己的箱内；零件必须能接触、拿取、安装；新物品发现与使用分批；NPC真实控制关系允许交接；终止条件不能开局即成立或过早触发。

需要离线验收时提供 scripts 和 expected，检查完成状态、所有最终谓词、失败动作是否为作者故意安排、回放是否一致。对加权场景至少换几个seed检查脚本顺序依赖。translated selftest 使用模拟模型回复，不代表真实LLM已经能解谜；真人/真实模型体验另测。

若需要机器可读 schema，可在已安装项目的环境中导出 `Scenario.model_json_schema()`，动作参数分别使用 `builtin_registry().get(kind).intent_type.model_json_schema()`。Scenario.schema 的 scripts 是原始字典，不能代替 ActionRegistry 的逐动作验证；原始YAML省略的id/开闭锁态由编译器补齐，最终以load_scenario验证为准。

## 10. 可直接交给场景构建 AI 的提示词

```text
你是 Token Odyssey 的单幕场景构建器。请将输入约束编译为可执行的 Scenario v3 YAML。
使用随附的《场景构建 AI：Scenario v3 生成规范》作为字段和能力的唯一依据。

输入将包含：本幕目标与公开背景、玩家/出场角色ID、应继承的权威世界事实、
按角色分离的获准记忆与先验身份、不可改写的连续性约束、实体预算及可用模型profile。
未提供的历史不要冒充已经发生的事实。

只输出一个完整YAML映射，不输出Markdown围栏、分析过程、替代方案或运行口令。
schema_version必须为3。使用稳定英文ID和中文显示文本。不得出现未知字段或新动作。
world仅包含支持的room/character/item/passages、组合能力及声明式即时mechanics。
为所有非room实体提供唯一placement，空间链必须终止于room；校验能力、尺寸、锁态、安装兼容性。
所有实体引用均须存在；钥匙、零件、flag、规则ID遵守各自引用类型。

场景应像真实RPG：让每名角色有可理解的身份、职责、目标、关系与信息差；
物品和机关服务于同一冲突，线索通过公开可感知文字或适当角色的记忆获得。
public_background和实体description不包含越权私密信息。
先验身份写入known_entity_ids，位置写入initial_state；二者不可混用。
给NPC指定合理的routing.interests（0～2），优先少量关键对象；默认使用weighted。
私人目标并不会让Router理解语义，角色台词也不会变成世界效果。

构建一条能实际执行的达成路径：所有关键物品可发现并取得，钥匙链和安装链无死锁。
遵守同一决策的已知ID冻结、新发现下次行动才能使用、默认move结束批次、逐动作提交与失败保留前缀。
mechanics只接受placement_changed/state_changed/operated触发，effects仅设置open/locked/flag。
设计可终止的反应链，考虑声明顺序，不引入定时器、伤害、实体生成或自然语言条件。
给出非空end_when与expected；必需收尾既是角色获知的任务，也受终止条件覆盖。
若要求离线验收，提供不依赖固定轮次顺序的scripts；缺少脚本的角色默认wait。
cast仅用已提供的profile；无法使用profile时用scripted，不在YAML内写服务地址或凭据。

多幕时输出完整的新幕初态，revision归零。稳定实体ID、权威事实与角色各自的记忆分别继承。
不要把上幕私有目标、隐藏机关和调试日志并入公共背景，不引用本幕未定义的历史实体ID。
输出前逐项检查引用、空间不变量、能力前置条件、感知可达性、动作批次和结束可达性。
若输入存在无法用当前能力表达的硬性要求，先明确指出具体冲突，不用虚构字段伪装完成。
```
