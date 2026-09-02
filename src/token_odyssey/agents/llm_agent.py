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
from token_odyssey.inside_act.context import TurnContext
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
        rooms = json.dumps(
            [{"id": room_id, "name": name} for room_id, name in identity.room_catalog],
            ensure_ascii=False,
        )
        return f"""你是话剧式 RPG 世界中的一名角色。你只能根据系统投影给你的事实提出意图；程序负责世界结算与其他角色能否观察。
不要虚构未观察到的实体、位置或事件，也不要假定其他参与者由人类或模型控制。

[世界历史]
{identity.world_history}

[当前 Act 背景]
{identity.act_background}

[Room]
{rooms}

[Action Registry]
{self.action_registry.prompt_catalog()}
actions 严格按数组顺序执行，前一个 action 的确定结果可供后一个 action 使用。每次行动权最多 5 个 action，通常控制在 2–3 个。amplitude 省略时自动使用 normal；只有刻意降低或提高显眼程度时才填写 subtle 或 overt。移动到其他 Room 时，优先让 move 成为最后一个 action，等待下次 context 再操作目的地实体。计划通常原子提交；若 move 后的环境交互因现场变化失败，系统可能只执行到最后一个有效 move，并取消其后 action。空 actions 非法；确实无事可做时使用 wait，且 wait 必须是唯一 action。
{identity.action_guidance}

只输出一个 JSON 对象，不要 Markdown。示例：
等待：{{"private_thought":"暂时观察","actions":[{{"kind":"wait"}}]}}
顺序搜索并取出：{{"private_thought":"先确认内容","actions":[{{"kind":"search","target_id":"box_id"}},{{"kind":"take","target_id":"item_id"}}]}}
低声说话：{{"private_thought":"避免引人注意","actions":[{{"kind":"say","target_ids":["character_id"],"content":"跟我来。","amplitude":"subtle"}}]}}

[你的角色]
姓名：{identity.name}（id: {identity.actor_id}）
性格：{identity.personality}
外貌：{identity.appearance}
特性：{'、'.join(identity.traits) if identity.traits else '无'}
Act 前记忆：{identity.pre_act_memory or '无'}
已完成 Act 记忆：{'；'.join(identity.act_memories) if identity.act_memories else '无'}
私人目标：{identity.private_goal or '依据角色立场行动。'}"""

    @staticmethod
    def _render_context(context: TurnContext) -> str:
        return json.dumps(
            context.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            indent=2,
        )

    @staticmethod
    def _render_feedback(issues) -> str:
        return json.dumps(
            {
                "action_rejected": True,
                "errors": [
                    {
                        "code": issue.code,
                        **(
                            {"action_index": issue.action_index}
                            if issue.action_index is not None
                            else {}
                        ),
                        "message": issue.message,
                    }
                    for issue in issues
                ],
                "instruction": "刚才提交的 actions 没有发生。请一次性修正全部错误并重新输出完整 JSON。",
            },
            ensure_ascii=False,
            indent=2,
        )


def _extract_json(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines.pop()
        stripped = "\n".join(lines).strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    return stripped if start < 0 or end < start else stripped[start : end + 1]
