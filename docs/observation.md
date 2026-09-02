# Observation 与角色记忆

Observation 分为四层：

- `VisibilityService` 计算连续空间分数。
- `ObservationPolicy` 使用独立 seeded RNG 得出 full、partial 或 none。
- `KnowledgeProjector` 保存角色最后实际观察到的实体信息。
- Action renderer 将已经获准的字段转成客观文本。

判定规则：

```text
roll <= score                          → full
roll <= min(1, score × partial_factor) → partial
otherwise                              → none
```

环境扫描对 full 结果记录 `new`、`moved` 或 `unchanged`。ContextProjector 将 Character 和 Item 分别组装为 `new_or_changed`、`visible_same_location` 和 `memories`。第二组不要求本次随机扫描成功，但要求上次 placement 仍与当前状态一致、与角色同 Room 且有效可见度大于零。`memories` 只暴露上次观察快照，不泄漏 canonical 当前位置。

角色直接 `attached` 和 `inside` 的物品从普通 Item 中移入 `inventory` 对应数组；只包含直接子物品，不递归泄漏随身容器内部。角色自己的 ACTION event 不回灌为下一次观察，但 WORLD 结果、知识授予 directive 和 execution feedback 仍保留。

partial 投影只给模糊感官文字，不暴露实体 name、id、placement 或 description，也不创建可供 action 引用的结构化知识。环境 full，以及 `search`、`show` 等明确的 full 授予，会在首次看清时附加一次实体感官描述；可信记忆之后只保留紧凑索引。

full World Event 可以通过 `knowledge_entity_ids` 更新最后观察位置。动作可使用通用 `grant_knowledge` 或 `scan_environment` directive；Observation 不识别产生 directive 的动作类型。

Canonical World Log、各角色 Observation、execution notice、角色 Knowledge 和 transcript 是不同用途的数据，不得互相替代。Item description 只描述可直接感知的外观、状态、声音或可读文字；用途、归属、兼容结论和情节判断属于角色记忆或隐藏 mechanics。面向参与者的 TurnContext 使用英文键 JSON，不包含 controller、interaction status 或观察轮次等可由分组/placement 推导的重复字段。
