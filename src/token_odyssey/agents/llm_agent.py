"""LLM participant with a permanent append-only private conversation."""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import ValidationError

from token_odyssey.agents.contracts import (
    AgentDecision,
    AgentUnavailableError,
    ChatMessage,
    ChatRole,
    DecisionRequest,
)
from token_odyssey.inside_act.actions.registry import ActionRegistry, RegistryError
from token_odyssey.inside_act.context import (
    EntityMemoryGroups,
    EntityView,
    InteractionStatus,
    ScanObservationStatus,
    TurnContext,
)
from token_odyssey.llm.contracts import LLMRequest
from token_odyssey.llm.registry import LLMBackendRegistry, LLMProfileRegistry


@dataclass(frozen=True)
class AgentIdentity:
    actor_id: str
    name: str
    personality: str
    appearance: str
    traits: tuple[str, ...]
    pre_act_memory: str
    act_memories: tuple[str, ...]
    private_goal: str
    world_history: str
    act_background: str
    action_guidance: str
    room_catalog: tuple[tuple[str, str], ...]


class LLMAgent:
    def __init__(
        self,
        *,
        identity: AgentIdentity,
        mode: str,
        action_registry: ActionRegistry,
        backend_registry: LLMBackendRegistry,
        profile_registry: LLMProfileRegistry,
    ) -> None:
        self.identity = identity
        self.mode = mode
        self.action_registry = action_registry
        self.backend_registry = backend_registry
        self.profile_registry = profile_registry
        self.messages: list[ChatMessage] = []
        self.requests: list[list[dict[str, str]]] = []

    def decide(self, request: DecisionRequest) -> AgentDecision:
        if request.actor_id != self.identity.actor_id:
            raise ValueError("LLMAgent cannot decide for a different Character")
        if not self.messages:
            self.messages.append(ChatMessage(role=ChatRole.SYSTEM, content=self._system_prompt()))
        if request.context is not None:
            content = self._render_context(request.context)
        else:
            assert request.feedback is not None
            content = self._render_feedback(request.feedback.issues)
        self.messages.append(ChatMessage(role=ChatRole.USER, content=content))
        profile = self.profile_registry.get(self.mode)
        backend = self.backend_registry.get(profile.backend_id)
        self.requests.append([message.model_dump(mode="json") for message in self.messages])
        try:
            response = backend.complete(LLMRequest(profile=profile, messages=list(self.messages)))
        except Exception as exc:
            raise AgentUnavailableError(f"LLM 请求失败：{exc}") from exc
        self.messages.append(ChatMessage(role=ChatRole.ASSISTANT, content=response.content))
        if not response.content:
            return AgentDecision(
                actor_id=request.actor_id,
                raw_content=response.content,
                output_error="模型返回了空内容",
                usage=response.usage,
                model=response.model,
                response_id=response.response_id,
            )
        try:
            plan = self.action_registry.parse_plan(_extract_json(response.content))
            return AgentDecision(
                actor_id=request.actor_id,
                raw_content=response.content,
                plan=plan,
                usage=response.usage,
                model=response.model,
                response_id=response.response_id,
            )
        except (json.JSONDecodeError, ValidationError, RegistryError, ValueError) as exc:
            return AgentDecision(
                actor_id=request.actor_id,
                raw_content=response.content,
                output_error=f"模型输出不符合 TurnPlan JSON：{exc}",
                usage=response.usage,
                model=response.model,
                response_id=response.response_id,
            )

    def _system_prompt(self) -> str:
        identity = self.identity
        rooms = "\n".join(f"- {name} (id: {room_id})" for room_id, name in identity.room_catalog)
        return f"""你是话剧式 RPG 世界中的一名角色。你只能根据系统投影给你的事实提出意图；程序负责世界结算与其他角色能否观察。
不要虚构未观察到的实体、位置或事件，也不要假定其他参与者由人类或模型控制。

【世界历史】
{identity.world_history}

【当前 Act 背景】
{identity.act_background}

【Room】
{rooms}

【Action Registry】
{self.action_registry.prompt_catalog()}
Action amplitude 只能是 subtle、normal、overt。每次行动权总计最多 5 个 action，say 也计数但不限种类数量；通常将计划控制在 2–3 个 action，避免一次执行过多。相同 frame 同时发生，不能依赖同 frame 其他命令的结果；不同 frame 按顺序发生，后一 frame 可以依赖前一 frame 的状态与确定获得的知识。已经公开由你控制的物品不用再 take；如果 take 后不打算继续控制物品，请在后续 frame 用 place 将它放到 Room 或合适实体上/内以释放控制权。移动到其他 Room 时，优先让 move 成为本回合最后一个 action，等待下次 context 再操作目的 Room 内的实体。计划通常原子提交；若 move 后的环境交互因现场状态变化失败，系统可能只执行到更早 frame 中最后一个有效 move，并取消其后 action。空计划非法，无事可做使用 wait。
{identity.action_guidance}

只输出一个 JSON 对象，不要 Markdown：
{{"private_thought":"私有想法","frames":[{{"commands":[{{"kind":"wait","amplitude":"normal"}}]}}]}}

【你的角色】
姓名：{identity.name}（id: {identity.actor_id}）
性格：{identity.personality}
外貌：{identity.appearance}
特性：{'、'.join(identity.traits) if identity.traits else '无'}
Act 前记忆：{identity.pre_act_memory or '无'}
已完成 Act 记忆：{'；'.join(identity.act_memories) if identity.act_memories else '无'}
私人目标：{identity.private_goal or '依据角色立场行动。'}"""

    @staticmethod
    def _render_context(context: TurnContext) -> str:
        sections = [
            "【获得行动权】\n"
            f"当前位置：{context.room_name} (id: {context.room_id})"
        ]
        if context.new_observations:
            observations = "\n".join(
                f"- {observation.text}" for observation in context.new_observations
            )
            sections.append(f"【新观察到的 World Log 发展】\n{observations}")
        npc_section = _render_memory_groups(context.npcs)
        if npc_section:
            sections.append("【NPC】\n" + npc_section)
        item_section = _render_memory_groups(context.items)
        if item_section:
            sections.append("【Item】\n" + item_section)
        sections.append("请根据完整追加式对话历史和以上增量投影输出 TurnPlan JSON。")
        return "\n\n".join(sections)

    @staticmethod
    def _render_feedback(issues) -> str:
        reasons = []
        for issue in issues:
            location = []
            if issue.frame_index is not None:
                location.append(f"frame {issue.frame_index + 1}")
            if issue.command_index is not None:
                location.append(f"command {issue.command_index + 1}")
            prefix = f" ({' / '.join(location)})" if location else ""
            reasons.append(f"- [{issue.code}]{prefix} {issue.message}")
        return (
            "【World Harness 私有反馈】\n"
            "刚才提交的计划没有在世界中发生，也没有产生 World Event。\n"
            "请逐项修正：\n"
            + "\n".join(reasons)
            + "\n请重新输出完整 TurnPlan JSON；有先后依赖的命令必须拆到不同 frame。"
        )


def _render_view(view: EntityView) -> str:
    placement = (
        f"{view.placement.relation.value}:{view.placement.parent_id}"
        if view.placement is not None
        else "root"
    )
    status = {
        ScanObservationStatus.NEW: "首次确认",
        ScanObservationStatus.MOVED: "位置变化",
        ScanObservationStatus.UNCHANGED: "位置未变",
        None: "已知",
    }[view.observation_status]
    return (
        f"- {view.name} (id: {view.id}, placement: {placement}, {status}, "
        f"{_render_interaction(view)})：{view.description}"
    )


def _render_compact_view(view: EntityView) -> str:
    placement = (
        f"{view.placement.relation.value}:{view.placement.parent_id}"
        if view.placement is not None
        else "root"
    )
    round_text = (
        f", last_observed_round: {view.last_observed_round}"
        if view.last_observed_round is not None
        else ""
    )
    return (
        f"- {view.name} (id: {view.id}, placement: {placement}{round_text}, "
        f"{_render_interaction(view)})"
    )


def _render_memory_groups(groups: EntityMemoryGroups) -> str:
    sections = []
    if groups.observed_this_turn:
        sections.append(
            "【本次确认】\n"
            + "\n".join(_render_view(view) for view in groups.observed_this_turn)
        )
    if groups.trusted_same_room:
        sections.append(
            "【当前可信的同房记忆】\n"
            + "\n".join(_render_compact_view(view) for view in groups.trusted_same_room)
        )
    if groups.other_memories:
        sections.append(
            "【其他记忆（不保证当前可交互）】\n"
            + "\n".join(_render_compact_view(view) for view in groups.other_memories)
        )
    return "\n".join(sections)


def _render_interaction(view: EntityView) -> str:
    if view.interaction_status == InteractionStatus.AVAILABLE:
        return "interaction: available"
    if view.interaction_status == InteractionStatus.CONTROLLED_BY_OTHER:
        return f"interaction: blocked, controller: {view.controller_id}"
    return "interaction: not_guaranteed"


def _extract_json(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines.pop()
        stripped = "\n".join(lines).strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    return stripped if start < 0 or end < start else stripped[start : end + 1]
