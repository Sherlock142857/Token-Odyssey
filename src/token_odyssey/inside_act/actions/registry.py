"""Frozen action registry and two-stage TurnPlan parser."""

from __future__ import annotations

from collections.abc import Iterable
import json
from typing import Any

from pydantic import Field, ValidationError

from token_odyssey.inside_act.actions.contracts import (
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


class _RawTurnPlan(StrictModel):
    private_thought: str = ""
    actions: list[Any] = Field(min_length=1, max_length=MAX_ACTIONS_PER_TURN)


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
                    f"JSON 无法解析：第 {exc.lineno} 行第 {exc.colno} 列附近语法错误\n"
                    f"正确格式示例：{self.plan_template()}"
                ) from exc
        else:
            payload = raw

        envelope_errors: list[str] = []
        try:
            _RawTurnPlan.model_validate(payload)
        except ValidationError as exc:
            envelope_errors.extend(
                _validation_messages(exc, prefix="TurnPlan")
            )

        actions_raw = payload.get("actions", []) if isinstance(payload, dict) else []
        actions: list[BaseActionIntent] = []
        action_errors: list[str] = []
        if isinstance(actions_raw, list):
            for action_index, action_raw in enumerate(actions_raw):
                prefix = f"actions[{action_index}]"
                if not isinstance(action_raw, dict):
                    action_errors.append(
                        f"{prefix} 必须是 JSON 对象\n正确格式示例：{self.plan_template()}"
                    )
                    continue
                kind = action_raw.get("kind")
                if not isinstance(kind, str):
                    action_errors.append(
                        f"{prefix}.kind 缺少字符串字段\n正确格式示例：{self.plan_template()}"
                    )
                    continue
                try:
                    spec = self.spec(kind)
                except RegistryError:
                    choices = "、".join(self.kinds)
                    action_errors.append(
                        f"{prefix}.kind 是未知 action {kind!r}；可用 action：{choices}"
                    )
                    continue
                try:
                    actions.append(spec.intent_model.model_validate(action_raw))
                except ValidationError as exc:
                    allowed = set(spec.intent_model.model_json_schema().get("properties", {}))
                    messages = _validation_messages(
                        exc, prefix=prefix, allowed_fields=allowed
                    )
                    action_errors.append(
                        "\n".join([*messages, f"正确的 {kind} 格式：{self.action_template(kind)}"])
                    )

        if envelope_errors or action_errors:
            raise RegistryError("\n".join([*envelope_errors, *action_errors]))

        assert isinstance(payload, dict)
        plan = TurnPlan(
            private_thought=payload.get("private_thought", ""),
            actions=actions,
        )
        shape_reasons = self.validate_shape(plan)
        if shape_reasons:
            raise RegistryError("；".join(shape_reasons))
        return plan

    def validate_shape(self, plan: TurnPlan) -> list[str]:
        actions = plan.actions
        reasons: list[str] = []
        if len(actions) > MAX_ACTIONS_PER_TURN:
            reasons.append(
                f"计划共有 {len(actions)} 个 action；每次行动权最多提交 "
                f"{MAX_ACTIONS_PER_TURN} 个，请删减或留到下一次行动权"
            )
        exclusive = [
            action
            for action in actions
            if self.spec(action.kind).must_be_exclusive
        ]
        if exclusive and len(actions) != 1:
            kinds = "、".join(dict.fromkeys(action.kind for action in exclusive))
            reasons.append(f"{kinds} 必须是整个 actions 数组中唯一的 action")
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
        for index, spec in enumerate(self._specs.values(), start=1):
            schema = spec.intent_model.model_json_schema()
            properties = schema.get("properties", {})
            required = set(schema.get("required", []))
            fields = [
                f"{name}{'' if name in required else '?'}"
                for name in properties
                if name not in {"kind", "amplitude"}
            ]
            signature = f"{spec.kind}({', '.join(fields)})"
            lines.append(f"{index}. {signature}: {spec.prompt_usage}")
            if spec.prompt_requirements:
                lines.append(f"  判定：{'；'.join(spec.prompt_requirements)}")
            if spec.prompt_effect:
                lines.append(f"  效果：{spec.prompt_effect}")
            if spec.prompt_misuses:
                lines.append(f"  避免：{'；'.join(spec.prompt_misuses)}")
        return "\n".join(lines)

    def action_template(self, kind: str) -> str:
        spec = self.spec(kind)
        schema = spec.intent_model.model_json_schema()
        required = set(schema.get("required", []))
        template: dict[str, Any] = {"kind": kind}
        for name, property_schema in schema.get("properties", {}).items():
            if name in {"kind", "amplitude"} or name not in required:
                continue
            template[name] = _field_example(name, property_schema)
        return json.dumps(template, ensure_ascii=False, separators=(",", ":"))

    def plan_template(self) -> str:
        return json.dumps(
            {
                "private_thought": "私有想法",
                "actions": [{"kind": "wait"}],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )


def _validation_messages(
    exc: ValidationError,
    *,
    prefix: str,
    allowed_fields: set[str] | None = None,
) -> list[str]:
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
    return messages or [f"{prefix} 输入格式不正确"]


def _format_validation_error(
    exc: ValidationError,
    *,
    prefix: str,
    allowed_fields: set[str] | None = None,
) -> str:
    return "；".join(
        _validation_messages(exc, prefix=prefix, allowed_fields=allowed_fields)
    )


def _field_example(name: str, schema: dict[str, Any]) -> Any:
    if name == "content":
        return "说话内容"
    if name == "relation":
        return "inside"
    if name == "target_ids":
        return ["character_id"]
    if name.endswith("_id"):
        return f"{name.removesuffix('_id')}_id"
    if schema.get("type") == "array":
        return ["value"]
    if schema.get("type") == "string":
        return "value"
    return "value"


def _json_path(location: tuple[Any, ...]) -> str:
    result = ""
    for part in location:
        result += f"[{part}]" if isinstance(part, int) else f".{part}"
    return result
