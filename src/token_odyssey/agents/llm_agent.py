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
from token_odyssey.inside_act.context import EntityView, TurnContext
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
Action amplitude 只能是 subtle、normal、overt。每回合最多一个 say 和一个非 say Action。相同 frame 同时发生，不同 frame 按顺序发生；空计划非法，无事可做使用 wait。
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
        observations = "\n".join(
            f"- [第{observation.round_number}轮/{observation.level.value}] {observation.text}"
            for observation in context.new_observations
        ) or "- 无"
        known = "\n".join(_render_view(view) for view in context.known_visible) or "- 无"
        new = "\n".join(_render_view(view) for view in context.newly_visible) or "- 无"
        return f"""【第 {context.round_number} 轮：获得行动权】
位置：{context.room_name} (id: {context.room_id})

【自上次行动权以来的新观察】
{observations}

【此前见过且当前仍可观察】
{known}

【本轮首次观察到】
{new}

同处且已确认的角色 id：{context.colocated_character_ids or '无'}
已知且由你控制的实体 id：{context.controlled_entity_ids or '无'}
可移动 Room id：{context.available_room_ids}

请根据完整追加式对话历史和本轮投影输出 TurnPlan JSON。"""

    @staticmethod
    def _render_feedback(issues) -> str:
        reasons = "；".join(issue.message for issue in issues)
        return (
            "【World Harness 私有反馈】\n"
            "刚才提交的计划没有在世界中发生，也没有产生 World Event。\n"
            f"拒绝原因：{reasons}\n请只修正不合法部分并重新输出完整 TurnPlan JSON。"
        )


def _render_view(view: EntityView) -> str:
    placement = (
        f"{view.placement.relation.value}:{view.placement.parent_id}"
        if view.placement is not None
        else "root"
    )
    return f"- {view.name} (id: {view.id}, kind: {view.kind.value}, placement: {placement})：{view.description}"


def _extract_json(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines.pop()
        stripped = "\n".join(lines).strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    return stripped if start < 0 or end < start else stripped[start : end + 1]
