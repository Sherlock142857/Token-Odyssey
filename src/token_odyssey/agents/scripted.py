"""Offline participant implementations for tests, demos, and replay."""

from __future__ import annotations

import json
from collections import defaultdict, deque

from token_odyssey.agents.contracts import AgentDecision, AgentError, DecisionRequest
from token_odyssey.inside_act.actions.contracts import TurnPlan
from token_odyssey.inside_act.actions.registry import ActionRegistry


class ScriptedAgent:
    def __init__(self, scripts: dict[str, list[TurnPlan]], registry: ActionRegistry) -> None:
        self.scripts = {actor_id: deque(plans) for actor_id, plans in scripts.items()}
        self.registry = registry

    def decide(self, request: DecisionRequest) -> AgentDecision:
        queue = self.scripts.get(request.actor_id)
        plan = queue.popleft() if queue else self.registry.parse_plan(
            {"private_thought": "暂时观察局势。", "frames": [{"commands": [{"kind": "wait"}]}]}
        )
        return _offline_decision(request.actor_id, plan)


class DemoAgent:
    def __init__(self, registry: ActionRegistry) -> None:
        self.registry = registry
        self.turn_counts: defaultdict[str, int] = defaultdict(int)

    def decide(self, request: DecisionRequest) -> AgentDecision:
        if request.context is None:
            return _offline_decision(
                request.actor_id,
                self.registry.parse_plan(
                    {"frames": [{"commands": [{"kind": "wait"}]}]}
                ),
            )
        context = request.context
        turn = self.turn_counts[request.actor_id]
        self.turn_counts[request.actor_id] += 1
        command: dict = {"kind": "wait"}
        visible_items = [
            view.id
            for view in [*context.known_visible, *context.newly_visible]
            if view.kind.value == "item"
        ]
        if turn % 3 == 0 and visible_items:
            command = {"kind": "take", "target_entity_id": visible_items[0]}
        elif turn % 3 == 1 and context.available_room_ids:
            destinations = [room for room in context.available_room_ids if room != context.room_id]
            if destinations:
                command = {"kind": "move", "destination_room_id": destinations[0]}
        commands = [command]
        if context.colocated_character_ids:
            commands.insert(
                0,
                {
                    "kind": "say",
                    "target_character_ids": [context.colocated_character_ids[0]],
                    "content": "我先确认眼前能够核实的情况。",
                },
            )
        plan = self.registry.parse_plan(
            {
                "private_thought": "只依据已经投影给我的事实行动。",
                "frames": [{"commands": commands}],
            }
        )
        return _offline_decision(request.actor_id, plan)


class ReplayAgent:
    def __init__(self, decisions: dict[str, list[AgentDecision]]) -> None:
        self.decisions = {
            actor_id: deque(actor_decisions)
            for actor_id, actor_decisions in decisions.items()
        }

    def decide(self, request: DecisionRequest) -> AgentDecision:
        queue = self.decisions.get(request.actor_id)
        if not queue:
            raise AgentError(f"replay 中缺少角色 {request.actor_id} 的下一次决策")
        decision = queue.popleft()
        if decision.actor_id != request.actor_id:
            raise AgentError(
                f"replay 决策角色错位：期望 {request.actor_id}，记录为 {decision.actor_id}"
            )
        return decision


def _offline_decision(actor_id: str, plan: TurnPlan) -> AgentDecision:
    return AgentDecision(
        actor_id=actor_id,
        raw_content=json.dumps(plan.model_dump(mode="json"), ensure_ascii=False),
        plan=plan,
    )
