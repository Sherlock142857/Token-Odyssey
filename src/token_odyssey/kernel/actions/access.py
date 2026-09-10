"""Openable and Lockable actions work for both Items and room Passages."""

from typing import Literal

from pydantic import JsonValue

from token_odyssey.kernel.actions.base import Action, ActionContext, EffectPlan, Intent, item, reachable, require
from token_odyssey.kernel.events import EventDraft, Issue
from token_odyssey.kernel.state import StateTable, change_to


class OpenIntent(Intent):
    kind: Literal["open"] = "open"
    openable_id: str


class CloseIntent(Intent):
    kind: Literal["close"] = "close"
    openable_id: str


class LockIntent(Intent):
    kind: Literal["lock"] = "lock"
    lockable_id: str
    key_item_id: str


class UnlockIntent(Intent):
    kind: Literal["unlock"] = "unlock"
    lockable_id: str
    key_item_id: str


AccessIntent = OpenIntent | CloseIntent | LockIntent | UnlockIntent


class AccessAction(Action[AccessIntent]):
    table: StateTable
    value: bool
    capability: str

    def target(self, intent: AccessIntent) -> str:
        if isinstance(intent, (OpenIntent, CloseIntent)):
            return intent.openable_id
        return intent.lockable_id

    def check(self, context: ActionContext, intent: AccessIntent) -> None:
        target = self.target(intent)
        reachable(context, target)
        obj = context.world.definition.object(target)
        require(getattr(obj, self.capability, None) is not None, "MISSING_CAPABILITY", capability=self.capability)
        if self.table == "openings" and self.value:
            require(not context.fluents.locked(target), "LOCKED", object_id=target)
        if self.table == "locks":
            assert isinstance(intent, (LockIntent, UnlockIntent))
            item(context, intent.key_item_id, held=True)
            lockable = getattr(obj, "lockable", None)
            assert lockable is not None
            require(intent.key_item_id in lockable.key_item_ids, "WRONG_KEY", object_id=target)
            if self.value:
                require(not context.fluents.open(target), "CLOSE_BEFORE_LOCK", object_id=target)

    def effects(self, context: ActionContext, intent: AccessIntent) -> EffectPlan:
        target, actor = self.target(intent), context.actor_id
        if getattr(context.world.state, self.table)[target] == self.value:
            return EffectPlan(None, notices=(Issue(code="ALREADY_SET", details={"action": self.kind}),))
        data: dict[str, JsonValue] = {"object_id": target, "value": self.value}
        # Seeing a closed box does not reveal whether its lock is engaged.
        # Lock results are disclosed to the operator; other observers see the act.
        visible = self.cue(
            intent, self.kind, target, {"actor_id": actor, "object_id": target}, threshold=0.2, identifies=(target,)
        )
        own = self.cue(
            intent,
            self.kind,
            target,
            {"actor_id": actor, **data},
            certain_for=(actor,),
            only_for=(actor,),
            identifies=(target,),
        )
        return EffectPlan(
            EventDraft(
                kind=self.kind,
                actor_id=actor,
                data=data,
                signals=("state_changed",),
                subject_ids=(target,),
                changes=(change_to(context.world.state, self.table, target, self.value),),
                cues=(visible, own),
            ),
            rescan_actor=self.table == "openings",
        )


class Open(AccessAction):
    kind, intent_type = "open", OpenIntent
    table, value, capability = "openings", True, "openable"
    salience = {"subtle": 0.9, "normal": 2.5, "overt": 4.0}


class Close(AccessAction):
    kind, intent_type = "close", CloseIntent
    table, value, capability = "openings", False, "openable"
    salience = {"subtle": 0.9, "normal": 2.5, "overt": 4.0}


class Lock(AccessAction):
    kind, intent_type = "lock", LockIntent
    table, value, capability = "locks", True, "lockable"
    salience = {"subtle": 0.7, "normal": 2.2, "overt": 3.5}


class Unlock(AccessAction):
    kind, intent_type = "unlock", UnlockIntent
    table, value, capability = "locks", False, "lockable"
    salience = {"subtle": 0.7, "normal": 2.2, "overt": 3.5}
