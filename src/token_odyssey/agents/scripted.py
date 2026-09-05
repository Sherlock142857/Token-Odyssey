"""Deterministic scripts for offline scenarios and integration tests."""

from collections import deque

from token_odyssey.agents.contracts import Decision, DecisionRequest
from token_odyssey.kernel.actions.base import ActionBatch
from token_odyssey.kernel.actions.registry import ActionRegistry


class ScriptedAgent:
    def __init__(self, actor_id: str, batches: list[ActionBatch], registry: ActionRegistry):
        self.actor_id = actor_id
        self.batches = deque(batches)
        self.registry = registry

    def decide(self, request: DecisionRequest) -> Decision:
        if request.actor_id != self.actor_id:
            raise ValueError("ScriptedAgent cannot control another Character")
        batch = self.batches.popleft() if self.batches else self.registry.parse_batch({"actions": [{"kind": "wait"}]})
        return Decision(actor_id=self.actor_id, batch=batch)
