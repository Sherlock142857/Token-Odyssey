"""Openable and Lockable actions work for both Items and room Passages."""

from typing import Literal

from token_odyssey.kernel.actions.base import Action, EffectPlan, Intent, item, reachable, require
from token_odyssey.kernel.events import EventDraft, Issue
from token_odyssey.kernel.state import change_to


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


class AccessAction(Action):
    table: str
    value: bool
    capability: str

    def target(self, intent):
        return intent.openable_id if self.table == "openings" else intent.lockable_id

    def check(self, context, intent):
        target = self.target(intent)
        reachable(context, target)
        obj = context.world.definition.object(target)
        require(getattr(obj, self.capability, None) is not None, "MISSING_CAPABILITY", capability=self.capability)
        if self.table == "openings" and self.value:
            require(not context.fluents.locked(target), "LOCKED", object_id=target)
        if self.table == "locks":
            item(context, intent.key_item_id, held=True)
            require(intent.key_item_id in obj.lockable.key_item_ids, "WRONG_KEY", object_id=target)
            if self.value:
                require(not context.fluents.open(target), "CLOSE_BEFORE_LOCK", object_id=target)

    def effects(self, context, intent):
        target, actor = self.target(intent), context.actor_id
        if getattr(context.world.state, self.table)[target] == self.value:
            return EffectPlan(None, notices=(Issue(code="ALREADY_SET", details={"action": self.kind}),))
        data = {"object_id": target, "value": self.value}
        # Seeing a closed box does not reveal whether its lock is engaged.
        # Lock results are disclosed to the operator; other observers see the act.
        visible = self.cue(intent, self.kind, target, {"actor_id": actor, "object_id": target},
                           threshold=0.55, identifies=(target,))
        own = self.cue(intent, self.kind, target, {"actor_id": actor, **data},
                       certain_for=(actor,), only_for=(actor,), identifies=(target,))
        return EffectPlan(EventDraft(kind=self.kind, actor_id=actor, data=data,
                                     signals=("state_changed",), subject_ids=(target,),
                                     changes=(change_to(context.world.state, self.table, target, self.value),),
                                     cues=(visible, own)), rescan_actor=self.table == "openings")


class Open(AccessAction):
    kind, intent_type = "open", OpenIntent
    table, value, capability = "openings", True, "openable"
    salience = {"subtle": 0.4, "normal": 1, "overt": 2}


class Close(AccessAction):
    kind, intent_type = "close", CloseIntent
    table, value, capability = "openings", False, "openable"
    salience = {"subtle": 0.4, "normal": 1, "overt": 2}


class Lock(AccessAction):
    kind, intent_type = "lock", LockIntent
    table, value, capability = "locks", True, "lockable"
    salience = {"subtle": 0.2, "normal": 0.8, "overt": 1.5}


class Unlock(AccessAction):
    kind, intent_type = "unlock", UnlockIntent
    table, value, capability = "locks", False, "lockable"
    salience = {"subtle": 0.2, "normal": 0.8, "overt": 1.5}
