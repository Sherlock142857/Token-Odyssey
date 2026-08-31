"""Agent adapters. They decide intent but never mutate the world."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from typing import Protocol

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, ValidationError

from airpg.context import AgentContext
from airpg.models import (
    ActionIntent,
    ActionKind,
    AgentDecision,
    DialogueIntent,
    HideActionIntent,
    MoveActionIntent,
    PerformanceMode,
    SearchActionIntent,
    ShowActionIntent,
    TakeActionIntent,
    TokenUsage,
    TurnIntent,
    WaitActionIntent,
)


class AgentError(RuntimeError):
    pass


class AgentUnavailableError(AgentError):
    """A transport, authentication or provider failure that retries cannot repair."""


class Agent(Protocol):
    def decide(self, context: AgentContext) -> AgentDecision: ...


class _LLMTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    private_thought: str = ""
    action: ActionIntent | None = None
    dialogue: DialogueIntent | None = None


class DeepSeekAgent:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-v4-flash",
        temperature: float = 0.9,
        max_tokens: int = 900,
        thinking_enabled: bool = False,
    ) -> None:
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.thinking_enabled = thinking_enabled

    def decide(self, context: AgentContext) -> AgentDecision:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=context.messages(),
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                response_format={"type": "json_object"},
                extra_body={
                    "thinking": {
                        "type": "enabled" if self.thinking_enabled else "disabled"
                    }
                },
            )
        except Exception as exc:  # SDK errors vary across compatible providers.
            raise AgentUnavailableError(f"LLM 请求失败：{exc}") from exc

        usage = _token_usage(response.usage)
        response_id = getattr(response, "id", None)
        response_model = getattr(response, "model", self.model)
        if not response.choices:
            return AgentDecision(
                actor_id=context.actor_id,
                raw_content="",
                output_error="模型响应没有 choices",
                usage=usage,
                model=response_model,
                response_id=response_id,
            )
        content = response.choices[0].message.content or ""
        if not content:
            return AgentDecision(
                actor_id=context.actor_id,
                raw_content=content,
                output_error="模型返回了空内容",
                usage=usage,
                model=response_model,
                response_id=response_id,
            )
        try:
            parsed = _LLMTurn.model_validate(json.loads(_extract_json(content)))
            intent = TurnIntent(actor_id=context.actor_id, **parsed.model_dump())
            return AgentDecision(
                actor_id=context.actor_id,
                raw_content=content,
                intent=intent,
                usage=usage,
                model=response_model,
                response_id=response_id,
            )
        except (json.JSONDecodeError, ValidationError, AttributeError) as exc:
            return AgentDecision(
                actor_id=context.actor_id,
                raw_content=content,
                output_error=f"模型输出不符合 TurnIntent JSON：{exc}",
                usage=usage,
                model=response_model,
                response_id=response_id,
            )


class ScriptedAgent:
    """A deterministic queue used by tests and authored reproductions."""

    def __init__(self, scripts: dict[str, list[TurnIntent]]) -> None:
        self.scripts = {actor_id: deque(intents) for actor_id, intents in scripts.items()}

    def decide(self, context: AgentContext) -> AgentDecision:
        queue = self.scripts.get(context.actor_id)
        if queue:
            intent = queue.popleft()
            intent = intent.model_copy(update={"actor_id": context.actor_id})
        else:
            intent = TurnIntent(
                actor_id=context.actor_id,
                private_thought="暂时观察局势。",
                action=WaitActionIntent(),
            )
        return _offline_decision(intent)


class DemoAgent:
    """Offline deterministic actor for exercising the harness without API cost."""

    def __init__(self) -> None:
        self.turn_counts: defaultdict[str, int] = defaultdict(int)

    def decide(self, context: AgentContext) -> AgentDecision:
        turn = self.turn_counts[context.actor_id]
        self.turn_counts[context.actor_id] += 1
        dialogue = None
        if context.colocated_actor_ids:
            dialogue = DialogueIntent(
                target_actor_ids=[context.colocated_actor_ids[turn % len(context.colocated_actor_ids)]],
                content="我想先确认这里究竟发生了什么。",
                mode=PerformanceMode.NORMAL,
            )

        action = None
        controlled = context.controlled_item_ids
        searchable = [
            item_id for item_id in context.known_container_ids if item_id in context.visible_item_ids
        ]
        loose = [
            item_id
            for item_id in context.visible_item_ids
            if item_id not in controlled and item_id not in context.known_container_ids
        ]
        phase = turn % 6
        if phase == 0 and searchable:
            action = SearchActionIntent(target_item_id=searchable[0])
        elif phase == 1 and loose:
            action = TakeActionIntent(target_item_id=loose[0])
        elif phase == 2 and controlled and context.colocated_actor_ids:
            action = ShowActionIntent(
                target_item_id=controlled[0],
                audience_ids=[context.colocated_actor_ids[0]],
            )
        elif phase == 3 and controlled:
            action = HideActionIntent(
                target_item_id=controlled[0],
                mode=PerformanceMode.SECRETIVE,
            )
        elif context.all_room_ids:
            candidates = [room_id for room_id in context.all_room_ids if room_id != context.current_room_id]
            action = (
                MoveActionIntent(destination_room_id=candidates[turn % len(candidates)])
                if candidates
                else WaitActionIntent()
            )
        else:
            action = WaitActionIntent()
        return _offline_decision(
            TurnIntent(
                actor_id=context.actor_id,
                private_thought="我需要依据眼前事实，而不是替别人完成愿望。",
                action=action,
                dialogue=dialogue,
            )
        )


class RecordedAgent:
    """Replays exact provider decisions without making any external request."""

    def __init__(self, decisions: dict[str, list[AgentDecision]]) -> None:
        self.decisions = {
            actor_id: deque(actor_decisions)
            for actor_id, actor_decisions in decisions.items()
        }

    def decide(self, context: AgentContext) -> AgentDecision:
        queue = self.decisions.get(context.actor_id)
        if not queue:
            raise AgentError(f"replay 中缺少角色 {context.actor_id} 的下一次决策")
        decision = queue.popleft()
        if decision.actor_id != context.actor_id:
            raise AgentError(
                f"replay 决策角色错位：期望 {context.actor_id}，记录为 {decision.actor_id}"
            )
        return decision


def _offline_decision(intent: TurnIntent) -> AgentDecision:
    payload = intent.model_dump(mode="json", exclude={"actor_id"})
    return AgentDecision(
        actor_id=intent.actor_id,
        raw_content=json.dumps(payload, ensure_ascii=False),
        intent=intent,
    )


def _token_usage(raw_usage) -> TokenUsage:
    if raw_usage is None:
        return TokenUsage()
    data = raw_usage.model_dump() if hasattr(raw_usage, "model_dump") else dict(raw_usage)
    prompt_tokens = int(data.get("prompt_tokens") or data.get("input_tokens") or 0)
    cache_details = data.get("input_tokens_details") or {}
    hit = int(
        data.get("prompt_cache_hit_tokens")
        or cache_details.get("cached_tokens")
        or 0
    )
    miss = int(data.get("prompt_cache_miss_tokens") or max(0, prompt_tokens - hit))
    completion = int(data.get("completion_tokens") or data.get("output_tokens") or 0)
    completion_details = data.get("completion_tokens_details") or data.get("output_tokens_details") or {}
    reasoning = int(completion_details.get("reasoning_tokens") or 0)
    return TokenUsage(
        prompt_tokens=prompt_tokens,
        prompt_cache_hit_tokens=hit,
        prompt_cache_miss_tokens=miss,
        completion_tokens=completion,
        reasoning_tokens=reasoning,
        total_tokens=int(data.get("total_tokens") or prompt_tokens + completion),
    )


def _extract_json(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        return stripped
    return stripped[start : end + 1]
