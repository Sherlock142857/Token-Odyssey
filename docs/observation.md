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

环境扫描对 full 结果记录 `new`、`moved` 或 `unchanged`，ContextProjector 再把 NPC 和 Item 分别组装为“本次确认”“当前可信的同房记忆”和“其他记忆”。第二组不要求本次随机扫描成功，但要求上次 placement 仍与当前状态一致、与角色同 Room 且有效可见度大于零。其他记忆只暴露上次观察快照，不泄漏 canonical 当前位置。

full World Event 可以通过 `knowledge_entity_ids` 更新最后观察位置。动作可使用通用 `grant_knowledge` 或 `scan_environment` directive；Observation 不识别产生 directive 的动作类型。

Canonical World Log、各角色 Observation、角色 Knowledge 和 transcript 是四种不同用途的数据，不得互相替代。
