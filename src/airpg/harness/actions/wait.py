from __future__ import annotations

from airpg.harness.actions.base import ActionContext
from airpg.harness.effects import ActionEffect
from airpg.models import WaitActionIntent


class WaitHandler:
    def validate(self, context: ActionContext, intent: WaitActionIntent) -> list[str]:
        return []

    def plan(self, context: ActionContext, intent: WaitActionIntent) -> ActionEffect:
        return ActionEffect()

