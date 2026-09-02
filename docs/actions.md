# Action v3

Character 只提交结构化意图。Harness 负责知识、空间、控制权、尺寸和机制判定，并以原子方式提交整个计划。

## TurnPlan

模型侧协议只有顺序 `actions`，不暴露内部 frame：

```json
{
  "private_thought": "先确认盒中内容",
  "actions": [
    {"kind": "search", "target_id": "bag"},
    {"kind": "take", "target_id": "letter"}
  ]
}
```

- `actions` 必须包含 1–5 项，并严格按数组顺序执行。
- 任一 action 非法时默认拒绝整份计划。若 move 后的现场交互失效，Runner 可以只提交截至最后一个有效 move 的前缀。
- `wait` 必须是唯一 action。
- `amplitude` 可为 `subtle`、`normal` 或 `overt`；省略时自动使用 `normal`。
- 格式校验先于世界校验，并一次返回全部可识别 action 的格式错误及正确模板。

## Builtin actions

| Action | 参数 | 主要行为 |
|---|---|---|
| `say` | `target_ids`, `content` | 向同 Room 的一个或多个角色说话 |
| `move` | `target_id` | 移动到 Room，并重新扫描环境 |
| `search` | `target_id` | 搜索已知、可接触的容器并确认直接子物品 |
| `take` | `target_id` | 取走物品；`inside:自己` 转为公开拿持，`attached:自己` 静默成功 |
| `give` | `item_id`, `target_ids` | 把控制中的物品交给唯一接收者 |
| `place` | `item_id`, `target_id`, `relation` | 以 `attached` 或 `inside` 放置物品 |
| `show` | `item_id`, `target_ids` | 向同 Room 角色展示可接触物品，不改变位置 |
| `hide` | `target_id` | 把控制中的小型物品变为 `inside:自己` |
| `install` | `component_id`, `target_id` | 按 Scenario 安装规则连接组件与设备 |
| `operate` | `target_id` | 操作物品；无机制容器转为 search，其他无机制物品由 WORLD 返回无效果 |
| `wait` | 无 | 放弃本次行动权，不产生事件 |

`target_ids` 始终表示角色目标列表；`give` 要求列表恰好一项。Scenario 中 mechanics 的作者字段仍使用 `target_entity_id`，不属于角色 action 协议。

## 扩展约束

新增 action 应通过 `ActionSpec` 注册自己的 intent、验证、effect、renderer、知识引用和提示元数据。Action 只能返回声明式 `ActionEffect`，只有 Harness 可以提交 mutation。提示目录和错误模板都从 intent schema 自动生成。
