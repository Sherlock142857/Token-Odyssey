"""One registry for typed parsing and execution; no language templates here."""

import json
from collections.abc import Iterable

from pydantic import ValidationError

from token_odyssey.kernel.actions.base import Action, ActionBatch, Intent


class ActionRegistry:
    def __init__(self, actions: Iterable[Action]):
        self._actions = {}
        for action in actions:
            if action.kind in self._actions:
                raise ValueError(f"duplicate action {action.kind}")
            self._actions[action.kind] = action

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(self._actions)

    def get(self, kind: str) -> Action:
        if kind not in self._actions:
            raise ValueError(f"unknown action {kind!r}; available: {', '.join(self.kinds)}")
        return self._actions[kind]

    def parse_intent(self, raw: dict) -> Intent:
        if not isinstance(raw, dict):
            raise ValueError("each action must be an object")
        return self.get(raw.get("kind")).intent_type.model_validate(raw)

    def parse_batch(self, raw: dict | str) -> ActionBatch:
        data = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(data, dict) or set(data) - {"actions", "private_thought"}:
            raise ValueError("batch requires actions and optional private_thought")
        if not isinstance(data.get("actions"), list) or not data["actions"]:
            raise ValueError("actions must be a non-empty array")
        # Shape errors are detected before execution; world-dependent Poss is
        # deliberately evaluated only immediately before each individual action.
        try:
            actions = tuple(self.parse_intent(raw) for raw in data["actions"])
            return ActionBatch(actions=actions, private_thought=data.get("private_thought", ""))
        except ValidationError as exc:
            messages = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
                        for e in exc.errors(include_url=False, include_input=False)]
            raise ValueError("; ".join(messages)) from exc


def builtin_registry() -> ActionRegistry:
    from token_odyssey.kernel.actions.access import Close, Lock, Open, Unlock
    from token_odyssey.kernel.actions.items import Give, Hide, Install, Place, Take
    from token_odyssey.kernel.actions.movement import Move
    from token_odyssey.kernel.actions.social import Operate, Say, Search, Show, Wait

    return ActionRegistry([Move(), Take(), Give(), Place(), Hide(), Show(), Say(),
                           Search(), Open(), Close(), Lock(), Unlock(), Install(), Operate(), Wait()])
