"""Natural-language input and JSON intent output for model participants."""

import json

from token_odyssey.agents.contracts import DecisionRequest
from token_odyssey.common import FrozenModel
from token_odyssey.kernel.actions.registry import ActionRegistry
from token_odyssey.perception.models import EntityView
from token_odyssey.translators.language import ACTION_HELP, render_fact, render_issue


class LLMIdentity(FrozenModel):
    actor_id: str
    name: str
    public_background: str = ""
    personality: str = ""
    private_goal: str = ""
    memories: tuple[str, ...] = ()
    known_entities: tuple[EntityView, ...] = ()


class LLMTranslator:
    def __init__(self, registry: ActionRegistry, identity: LLMIdentity, action_help: dict[str, str] | None = None):
        self.registry, self.identity = registry, identity
        self.action_help = {**ACTION_HELP, **(action_help or {})}
        self.labels = {entity.id: entity.name for entity in identity.known_entities}
        self.labels[identity.actor_id] = identity.name

    def system_prompt(self) -> str:
        identity = self.identity
        catalog = []
        for kind in self.registry.kinds:
            schema = self.registry.get(kind).intent_type.model_json_schema()
            required = set(schema.get("required", []))
            fields = [name + ("" if name in required else "?")
                      for name in schema["properties"] if name not in {"kind", "amplitude"}]
            catalog.append(f"{kind}({', '.join(fields)})：{self.action_help.get(kind, '')}")
        prior = "\n".join(f"{e.name} [{e.id}]：{e.description or ''}" for e in identity.known_entities)
        return f"""你在一个由程序维护真实状态的 RPG 世界中扮演角色。你只能提出动作意图。
仅依据得到的信息行动；角色发言、猜测和私人目标都不能直接改写世界。
一次回复可提交多个动作，严格逐项执行。某一步失败会停止队列，已经成功的动作保留。
不得猜测新对象 ID。先搜索或打开容器，收到新上下文后再引用新发现对象。
amplitude 可省略（normal），刻意隐秘用 subtle，刻意张扬用 overt。

[公共背景]
{identity.public_background}

[你的角色]
{identity.name} [{identity.actor_id}]
性格：{identity.personality}
私人目标：{identity.private_goal}
记忆：{'；'.join(identity.memories)}
事先认识的对象（不代表知道当前位置）：
{prior}

[可提交动作，? 表示可选参数]
{chr(10).join(catalog)}

只输出一个 JSON 对象：
{{"private_thought":"你的私有想法，可省略","actions":[{{"kind":"wait"}}]}}
物品交付示例：{{"actions":[{{"kind":"give","item_id":"known_item","recipient_id":"known_character"}}]}}
不要复制示例 ID；使用实际获知的 ID。actions 不能为空。"""

    def render_request(self, request: DecisionRequest) -> str:
        if request.issues:
            return "[本次提交未执行任何动作，请修正后重新提交]\n" + "\n".join(render_issue(x) for x in request.issues)
        view = request.view
        for entity in (*view.inventory, *view.items, *view.characters):
            self.labels[entity.id] = entity.name
        for observation in view.observations:
            self.labels.update(observation.labels)
            self.labels.update({entity.id: entity.name for entity in observation.entities})
        self.labels[view.room_id] = view.room_name
        for exit_view in view.exits:
            self.labels[exit_view.passage_id] = exit_view.name
            self.labels[exit_view.destination_room_id] = exit_view.destination_name
        lines = [f"[当前位置] {view.room_name} [{view.room_id}]", view.room_description,
                 f"本次最多 {view.max_actions} 个动作；" + ("move 后可以继续。" if view.continue_after_move else "move 成功后结束队列。")]
        lines.append("[出口]")
        lines.extend(f"{e.name} [{e.passage_id}] → {e.destination_name} [{e.destination_room_id}]，"
                     f"{'打开' if e.is_open else '关闭'}，{'可通行' if e.allows_travel else '不可通行'}" for e in view.exits)
        for title, entities in (("随身物品", view.inventory), ("当前人物", view.characters), ("当前物品", view.items)):
            lines.append(f"[{title}]")
            lines.extend(self._entity(entity) for entity in entities)
            if not entities:
                lines.append("无")
        lines.append("[自上次行动以来]")
        described = set()
        for observation in view.observations:
            if observation.source == "event":
                for fact in observation.facts:
                    lines.append(render_fact(fact, self.labels))
            for entity in observation.entities:
                if entity.description and entity.id not in described:
                    lines.append(f"辨认：{entity.name} [{entity.id}]，{entity.description}")
                    described.add(entity.id)
        if view.feedback:
            lines.append("[执行反馈]")
            lines.extend(render_issue(x) for x in view.feedback)
        return "\n".join(lines)

    def _entity(self, entity: EntityView) -> str:
        parts = [f"{entity.name} [{entity.id}]"]
        if entity.placement:
            parent = self.labels.get(entity.placement.parent_id, entity.placement.parent_id)
            relation = "在内部" if entity.placement.relation == "inside" else "附着/放在表面"
            parts.append(f"{parent}：{relation}")
        if entity.is_open is not None:
            parts.append("打开" if entity.is_open else "关闭")
        if entity.capabilities:
            terms = {"container": "容器", "openable": "可开闭", "lockable": "有锁", "slot": "安装插槽", "operable": "可操作"}
            parts.append("、".join(terms[x] for x in entity.capabilities))
        return "；".join(parts)

    def parse_response(self, content: str):
        stripped = content.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if not lines[-1].strip() == "```":
                raise ValueError("unclosed JSON code fence")
            stripped = "\n".join(lines[1:-1])
        return self.registry.parse_batch(json.loads(stripped))
