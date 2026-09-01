# Action 判定与效果

本文描述角色和场景作者需要理解的算法语义。Action 模块的编程扩展接口放在文末。

## 通用概念

- **已知**：角色已经通过环境投影、World Event 或搜索结果确认过实体。角色不能直接引用未知实体 id。
- **可接触**：实体与角色在同一 Room，且没有被其他角色控制。当前模型不表达锁、门或操作权限。
- **控制**：实体的 placement 父链经过某个 Character，该 Character 控制整条子树。
- `attached:character` 表示公开拿持；`inside:character` 表示藏在身上。
- `inside:container` 表示放在容器内，通常会让原角色失去控制；`attached` 不降低空间可见度，`inside` 会乘父容器的可见度。

Action 的 `amplitude` 只能是 `subtle`、`normal` 或 `overt`。它只影响别人观察到事件的概率，不会绕过合法性、尺寸、控制权或知识判定。

## TurnPlan、frame 与原子性

每次行动权提交 1—4 个 action。`say` 与其他 action 使用同一预算，不限制各自数量。

- 同一 frame 的命令共享 frame 前状态并视为同时发生，不能依赖同 frame 其他命令产生的位置或知识。
- 不同 frame 按顺序在 draft state 上规划；后一 frame 可以使用前一 frame 确定产生的状态和知识。
- 同 frame 若有两个命令修改同一实体，计划会被拒绝并要求拆分 frame。
- 任意 frame 非法会拒绝整份 TurnPlan；canonical WorldState、角色知识和 World Log 都不会部分提交。
- `wait` 可以和其他动作组合以保持兼容，但通常没有必要。

例如，先搜索再取出已确认物品必须拆成两个 frame：

```json
{
  "frames": [
    {"commands": [{"kind": "search", "target_entity_id": "bag"}]},
    {"commands": [{"kind": "take", "target_entity_id": "letter"}]}
  ]
}
```

## 内置 Action 总表

| Action | 主要字段 | 合法条件 | 确定效果 | 常见误用 |
| --- | --- | --- | --- | --- |
| `say` | `target_character_ids`, `content` | 至少一个目标，目标都与自己同 Room | 目标保证听清；其他角色按可见度判定 | 对自己或其他 Room 直接说话 |
| `move` | `destination_room_id` | 目标是不同于当前位置的 Room | Character 及携带子树一起移动，随后重新扫描环境 | 假定 move 会自动 search 或操作设施 |
| `search` | `target_entity_id` | 目标已知、是容器且可接触 | 确认直接子物品并加入自己的知识 | 在同 frame 中引用搜索结果 |
| `take` | `target_entity_id` | 物品已知、可接触且未由他人控制 | 变为 `attached:自己`；`inside:自己` 会被公开取出 | 对已经手持的物品重复 take |
| `give` | `target_entity_id`, `recipient_id` | 自己控制物品，接收者同 Room | 变为 `attached:接收者`，接收者保证观察到 | 未先取得公共物品就 give |
| `place` | `target_entity_id`, `container_id` | 自己控制物品；容器可接触、可容纳且不形成循环 | 变为 `inside:容器`，通常失去控制 | 用 place 冒充安装、启动或使用设备 |
| `show` | `target_entity_id`, `audience_ids` | 物品由自己控制，或处于可接触的公共位置；观众同 Room | 观众看清物品并获得知识；placement 不变 | 为展示公共物品先做多余的 take；展示他人控制物 |
| `hide` | `target_entity_id` | 自己控制物品且尺寸不超过藏匿上限 | 变为 `inside:自己`；之后可用 take 公开取出 | 把 hide 当作销毁或绝对不可见 |
| `wait` | 无 | 始终合法 | 状态不变，产生停留事件 | 与其他 action 冗余组合 |

当前没有 `use`、`operate`、`open` 或 `close`。操作中继机、开锁等行为不能用 `take` 或 `place` 冒充；这些动作需要后续 Scenario 声明前置条件和状态效果后再加入。

## 新增 Action 编程规范

1. 在 `token_odyssey.inside_act.actions.builtin` 新增一个模块。
2. 定义继承 `BaseActionIntent` 的严格 schema，`kind` 使用唯一 Literal。
3. 在同一模块实现 `validate`、`plan`、known reference extractor、full/partial renderer。
4. 导出一个 `ACTION = ActionSpec(...)`，同时填写 prompt usage、requirements、effect 和 misuses。
5. 只在 builtin 的显式 `BUILTIN_ACTIONS` 列表注册一次。

禁止为新动作修改 Runner、Harness、Observation、LLMAgent prompt 或集中 renderer。`plan` 只能返回声明式 `ActionEffect`；只有 Harness 可以提交 mutation。Renderer 必须描述可观察的正面事实，Partial renderer 不得泄漏对象 ID、内容或目标等未授权细节。
