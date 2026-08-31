"""Atomic state mutations planned by action handlers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from airpg.models import Location, WorldState


@dataclass(frozen=True)
class StateMutation:
    target_type: str
    target_id: str
    field_name: str
    before: Any
    after: Any

    def current_value(self, state: WorldState) -> Any:
        target = state.actors[self.target_id] if self.target_type == "actor" else state.items[self.target_id]
        return getattr(target, self.field_name)

    def validate_precondition(self, state: WorldState) -> None:
        current = self.current_value(state)
        if current != self.before:
            raise RuntimeError(
                f"state changed after validation: {self.target_type} {self.target_id} "
                f"{self.field_name} expected {self.before!r}, got {current!r}"
            )

    def commit(self, state: WorldState) -> None:
        target = state.actors[self.target_id] if self.target_type == "actor" else state.items[self.target_id]
        setattr(target, self.field_name, self.after)


@dataclass
class ActionEffect:
    data: dict[str, Any] = field(default_factory=dict)
    mutations: list[StateMutation] = field(default_factory=list)
    detail_visibility: float = 1.0
    direct_observer_ids: list[str] = field(default_factory=list)

    def commit(self, state: WorldState) -> None:
        for mutation in self.mutations:
            mutation.validate_precondition(state)
        for mutation in self.mutations:
            mutation.commit(state)


def location_payload(location: Location) -> dict[str, str]:
    return location.model_dump(mode="json")
