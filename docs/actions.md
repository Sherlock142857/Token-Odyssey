# 新增 Action 规范

## 扩展步骤

1. 在 `token_odyssey.inside_act.actions.builtin` 新增一个模块。
2. 定义继承 `BaseActionIntent` 的严格 schema，`kind` 使用唯一 Literal。
3. 在同一模块实现 `validate`、`plan`、known reference extractor、full/partial renderer。
4. 导出一个 `ACTION = ActionSpec(...)`。
5. 只在 builtin 的显式 `BUILTIN_ACTIONS` 列表注册一次。

禁止为新动作修改 Runner、Harness、Observation、LLMAgent prompt 或集中 renderer。

```python
class InspectIntent(BaseActionIntent):
    kind: Literal["inspect"] = "inspect"
    target_entity_id: str

class InspectEventData(ActionEventData):
    target_entity_id: str


ACTION = ActionSpec(
    kind="inspect",
    intent_model=InspectIntent,
    event_model=InspectEventData,
    validate=validate,
    plan=plan,
    known_reference_extractor=lambda intent: {intent.target_entity_id},
    intrinsic_visibility=0.5,
    render_full=render_full,
    render_partial=render_partial,
    prompt_usage="检查一个已知实体",
)
```

`plan` 只能返回声明式 `ActionEffect`：placement mutations、event data、visibility anchors、guaranteed observers、knowledge facts 和 observation directives。只有 Harness 可以提交 mutation。

Renderer 必须描述可观察的正面事实，不推断意图、原因或情绪。Partial renderer 不得泄漏对象 ID、内容或目标等未授权细节。

## Turn frames

每回合最多一个 `say` 和一个非 `say` Action。相同 frame 共享 frame 前状态并表示同时发生；两个 frame 按顺序在 draft state 上规划。任一命令失败会拒绝整份计划。
