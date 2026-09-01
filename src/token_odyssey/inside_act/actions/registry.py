"""Frozen action registry and two-stage TurnPlan parser."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import Field

from token_odyssey.inside_act.actions.contracts import (
    ActionFrame,
    ActionSpec,
    BaseActionIntent,
    TurnPlan,
)
from token_odyssey.inside_act.domain.common import StrictModel
from token_odyssey.inside_act.domain.events import WorldEvent
from token_odyssey.inside_act.domain.spatial import WorldState


class RegistryError(ValueError):
    pass


class _RawFrame(StrictModel):
    commands: list[dict[str, Any]] = Field(min_length=1, max_length=2)


class _RawTurnPlan(StrictModel):
    private_thought: str = ""
    frames: list[_RawFrame] = Field(min_length=1, max_length=2)


class ActionRegistry:
    def __init__(self, specs: Iterable[ActionSpec]) -> None:
        mapping: dict[str, ActionSpec] = {}
        for spec in specs:
            if spec.kind in mapping:
                raise RegistryError(f"duplicate Action kind {spec.kind!r}")
            mapping[spec.kind] = spec
        if not mapping:
            raise RegistryError("Action registry cannot be empty")
        self._specs = mapping

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(self._specs)

    def spec(self, kind: str) -> ActionSpec:
        try:
            return self._specs[kind]
        except KeyError as exc:
            raise RegistryError(f"unknown Action kind {kind!r}") from exc

    def parse_command(self, raw: dict[str, Any]) -> BaseActionIntent:
        kind = raw.get("kind")
        if not isinstance(kind, str):
            raise RegistryError("Action command requires a string kind")
        return self.spec(kind).intent_model.model_validate(raw)

    def parse_plan(self, raw: dict[str, Any] | str) -> TurnPlan:
        import json

        payload = json.loads(raw) if isinstance(raw, str) else raw
        parsed = _RawTurnPlan.model_validate(payload)
        frames = [
            ActionFrame(commands=[self.parse_command(command) for command in frame.commands])
            for frame in parsed.frames
        ]
        plan = TurnPlan(private_thought=parsed.private_thought, frames=frames)
        shape_reasons = self.validate_shape(plan)
        if shape_reasons:
            raise RegistryError("；".join(shape_reasons))
        return plan

    def validate_shape(self, plan: TurnPlan) -> list[str]:
        commands = [command for frame in plan.frames for command in frame.commands]
        say_count = sum(command.kind == "say" for command in commands)
        physical_count = len(commands) - say_count
        reasons: list[str] = []
        if say_count > 1:
            reasons.append("每回合最多一个 say")
        if physical_count > 1:
            reasons.append("每回合最多一个非 say Action")
        if len(commands) > 2:
            reasons.append("每回合最多两个命令")
        return reasons

    def known_references(self, command: BaseActionIntent) -> set[str]:
        return self.spec(command.kind).known_reference_extractor(command)

    def render(self, state: WorldState, event: WorldEvent, *, full: bool) -> str:
        spec = self.spec(event.action_kind)
        renderer = spec.render_full if full else spec.render_partial
        return renderer(state, event)

    def prompt_catalog(self) -> str:
        lines = []
        for spec in self._specs.values():
            schema = spec.intent_model.model_json_schema()
            properties = schema.get("properties", {})
            fields = [name for name in properties if name not in {"kind", "amplitude"}]
            suffix = f"；字段：{', '.join(fields)}" if fields else "；无额外字段"
            lines.append(f"- {spec.kind}: {spec.prompt_usage}{suffix}")
        return "\n".join(lines)
