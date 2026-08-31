"""Action handler contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from airpg.harness.effects import ActionEffect
from airpg.harness.query import WorldQuery
from airpg.models import ActionIntent, WorldState


@dataclass(frozen=True)
class ActionContext:
    actor_id: str
    state: WorldState
    query: WorldQuery


class ActionHandler(Protocol):
    def validate(self, context: ActionContext, intent: ActionIntent) -> list[str]: ...
    def plan(self, context: ActionContext, intent: ActionIntent) -> ActionEffect: ...


def require_item(state: WorldState, item_id: str) -> tuple[object | None, list[str]]:
    item = state.items.get(item_id)
    if item is None:
        return None, [f"未知物品 {item_id!r}"]
    return item, []

