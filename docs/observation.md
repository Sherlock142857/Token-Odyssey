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

环境投影分成 `newly_visible` 和 `known_visible`。过去见过但当前不可观察的实体只将 `currently_observable` 更新为 false；不产生“消失”“不知道谁动过”等文字，也不进入当前环境列表。

full World Event 可以通过 `knowledge_entity_ids` 更新最后观察位置。动作可使用通用 `grant_knowledge` 或 `scan_environment` directive；Observation 不识别产生 directive 的动作类型。

Canonical World Log、各角色 Observation、角色 Knowledge 和 transcript 是四种不同用途的数据，不得互相替代。
