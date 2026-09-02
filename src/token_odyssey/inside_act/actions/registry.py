"""Frozen action registry and two-stage TurnPlan parser."""

from __future__ import annotations

from collections.abc import Iterable
import json
from typing import Any

from pydantic import Field, ValidationError

from token_odyssey.inside_act.actions.contracts import (
    ActionFrame,
    ActionSpec,
    BaseActionIntent,
    MAX_ACTIONS_PER_TURN,
    TurnPlan,
)
from token_odyssey.inside_act.domain.common import StrictModel
from token_odyssey.inside_act.domain.events import EventSource, WorldEvent
from token_odyssey.inside_act.domain.spatial import WorldState


class RegistryError(ValueError):
    pass


class _RawFrame(StrictModel):
    commands: list[dict[str, Any]] = Field(
        min_length=1, max_length=MAX_ACTIONS_PER_TURN
    )


class _RawTurnPlan(StrictModel):
    private_thought: str = ""
    frames: list[_RawFrame] = Field(min_length=1, max_length=MAX_ACTIONS_PER_TURN)


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
            raise RegistryError("action 缺少字符串字段 kind")
        try:
            spec = self.spec(kind)
        except RegistryError as exc:
            choices = "、".join(self.kinds)
            raise RegistryError(f"未知 action {kind!r}；可用 action：{choices}") from exc
        try:
            return spec.intent_model.model_validate(raw)
        except ValidationError as exc:
            allowed = set(spec.intent_model.model_json_schema().get("properties", {}))
            raise RegistryError(
                _format_validation_error(exc, prefix=kind, allowed_fields=allowed)
            ) from exc

    def parse_plan(self, raw: dict[str, Any] | str) -> TurnPlan:
        if isinstance(raw, str):
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RegistryError(
                    f"JSON 无法解析：第 {exc.lineno} 行第 {exc.colno} 列附近语法错误"
                ) from exc
        else:
            payload = raw
        try:
            parsed = _RawTurnPlan.model_validate(payload)
        except ValidationError as exc:
            raise RegistryError(_format_validation_error(exc, prefix="TurnPlan")) from exc

        frames = []
        for frame_index, frame in enumerate(parsed.frames):
            commands = []
            for command_index, command in enumerate(frame.commands):
                try:
                    commands.append(self.parse_command(command))
                except RegistryError as exc:
                    raise RegistryError(
                        f"frames[{frame_index}].commands[{command_index}]：{exc}"
                    ) from exc
            frames.append(ActionFrame(commands=commands))
        plan = TurnPlan(private_thought=parsed.private_thought, frames=frames)
        shape_reasons = self.validate_shape(plan)
        if shape_reasons:
            raise RegistryError("；".join(shape_reasons))
        return plan

    def validate_shape(self, plan: TurnPlan) -> list[str]:
        commands = [command for frame in plan.frames for command in frame.commands]
        reasons: list[str] = []
        if len(commands) > MAX_ACTIONS_PER_TURN:
            reasons.append(
                f"计划共有 {len(commands)} 个 action；每次行动权最多提交 "
                f"{MAX_ACTIONS_PER_TURN} 个，请删减或留到下一次行动权"
            )
        exclusive = [
            command
            for command in commands
            if self.spec(command.kind).must_be_exclusive
        ]
        if exclusive and len(commands) != 1:
            kinds = "、".join(dict.fromkeys(command.kind for command in exclusive))
            reasons.append(f"{kinds} 必须是整份 TurnPlan 中唯一的 action")
        return reasons

    def known_references(self, command: BaseActionIntent) -> set[str]:
        return self.spec(command.kind).known_reference_extractor(command)

    def render(self, state: WorldState, event: WorldEvent, *, full: bool) -> str:
        if event.source != EventSource.ACTION or event.action_kind is None:
            raise ValueError("ActionRegistry can only render action-sourced events")
        spec = self.spec(event.action_kind)
        renderer = spec.render_full if full else spec.render_partial
        return renderer(state, event)

    def prompt_catalog(self) -> str:
        lines = []
        for spec in self._specs.values():
            schema = spec.intent_model.model_json_schema()
            properties = schema.get("properties", {})
            required = set(schema.get("required", []))
            fields = [
                f"{name}{'' if name in required else '?'}"
                for name in properties
                if name not in {"kind", "amplitude"}
            ]
            signature = f"{spec.kind}({', '.join(fields)})"
            lines.append(f"- {signature}: {spec.prompt_usage}")
            if spec.prompt_requirements:
                lines.append(f"  判定：{'；'.join(spec.prompt_requirements)}")
            if spec.prompt_effect:
                lines.append(f"  效果：{spec.prompt_effect}")
            if spec.prompt_misuses:
                lines.append(f"  避免：{'；'.join(spec.prompt_misuses)}")
        return "\n".join(lines)


def _format_validation_error(
    exc: ValidationError,
    *,
    prefix: str,
    allowed_fields: set[str] | None = None,
) -> str:
    messages: list[str] = []
    for error in exc.errors(include_url=False):
        location = _json_path(error.get("loc", ()))
        path = f"{prefix}{location}"
        error_type = str(error.get("type", "invalid"))
        value = error.get("input")
        context = error.get("ctx") or {}
        if error_type == "missing":
            message = f"{path} 缺少必填字段"
        elif error_type == "too_long":
            maximum = context.get("max_length", MAX_ACTIONS_PER_TURN)
            actual = len(value) if isinstance(value, (list, tuple, dict, str)) else "超限"
            message = f"{path} 最多允许 {maximum} 项，实际为 {actual} 项"
        elif error_type == "too_short":
            minimum = context.get("min_length", 1)
            message = f"{path} 至少需要 {minimum} 项"
        elif error_type == "extra_forbidden":
            field_name = str(error.get("loc", ("未知字段",))[-1])
            suffix = ""
            if allowed_fields is not None:
                suffix = f"；可用字段：{'、'.join(sorted(allowed_fields))}"
            message = f"{path} 是 {prefix} 不接受的字段 {field_name!r}{suffix}"
        elif error_type in {"string_too_short", "list_type", "string_type", "dict_type"}:
            message = f"{path} 的类型或长度不符合要求"
        elif error_type == "literal_error":
            expected = context.get("expected", "规定值")
            message = f"{path} 必须是 {expected}"
        else:
            message = f"{path} 的输入格式不正确"
        messages.append(message)
    return "；".join(messages) or f"{prefix} 输入格式不正确"


def _json_path(location: tuple[Any, ...]) -> str:
    result = ""
    for part in location:
        result += f"[{part}]" if isinstance(part, int) else f".{part}"
    return result
