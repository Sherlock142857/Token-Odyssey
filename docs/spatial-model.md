# 空间模型与可见度

## Placement forest

所有非 Room 实体恰好拥有一条父边：

```text
child --inside/attached--> parent
```

- `inside` 要求父节点 `is_container=true`。
- `attached` 不要求父节点是容器。
- 每条父链必须无环并最终终止于 Room。
- `attached -> Character` 表示手持；`inside -> Character` 表示藏在身上。
- Character、Item 和 Room 都在统一 `entities` 表；边独立保存在 `placements`。

大小是等级而不是累积体积。Item 容器允许 `child.size_class <= parent.size_class`；Character 使用 `WorldRules.actor_concealment_size_limit`；Room 不限制大小。

## Room Graph

图边方向为 `observer_room -> source_room`。同 Room 为 1，不可达为 0，多跳使用最大乘积路径。图权重只表达空间传播，不承担实体父子关系。

## 可见度

```text
edge(attached) = 1
edge(child inside parent) = parent.container_visibility

Vbase(observer, target)
  = observer 到 Room 根的逐边乘积
  × RoomGraph(observer_room, target_room)
  × target 到 Room 根的逐边乘积
```

每条 inside 边都贡献一次乘数。同处 `container_visibility=0.5` 暗室的两个直接子节点之间为 `0.5 × 0.5 = 0.25`。

```text
Vitem  = clamp(Vbase × item.intrinsic_visibility)
Vevent = clamp(max(anchor Vbase) × action.intrinsic_visibility × amplitude)
```

Action amplitude 为 `subtle=0.3`、`normal=1.0`、`overt=2.0`。VisibilityService 按 state identity、revision 和查询端点缓存结果。
