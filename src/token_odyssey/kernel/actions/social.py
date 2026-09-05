"""Speech, directed showing, deliberate search, operation, and waiting."""

from typing import Literal

from pydantic import Field

from token_odyssey.kernel.actions.base import Action, EffectPlan, Intent, colocated, item, require
from token_odyssey.kernel.events import EventDraft


class SayIntent(Intent):
    kind: Literal["say"] = "say"
    content: str = Field(min_length=1, max_length=4000)
    listener_ids: tuple[str, ...] = ()


class ShowIntent(Intent):
    kind: Literal["show"] = "show"
    item_id: str
    observer_ids: tuple[str, ...] = Field(min_length=1)


class SearchIntent(Intent):
    kind: Literal["search"] = "search"
    container_id: str


class OperateIntent(Intent):
    kind: Literal["operate"] = "operate"
    device_id: str


class WaitIntent(Intent):
    kind: Literal["wait"] = "wait"


class Say(Action[SayIntent]):
    kind, intent_type = "say", SayIntent
    salience = {"subtle": 0.2, "normal": 1, "overt": 3}

    def references(self, intent):
        return set(intent.listener_ids)

    def check(self, context, intent):
        for listener in set(intent.listener_ids):
            colocated(context, listener)

    def effects(self, context, intent):
        actor = context.actor_id
        listeners = tuple(listener for listener in intent.listener_ids
                          if context.fluents.transmission(listener, actor, "audio") > 0)
        cues = (
            self.cue(intent, "voice", actor, {}, channel="audio", threshold=0.1),
            self.cue(intent, "speech", actor, {"content": intent.content}, channel="audio", threshold=0.3),
            self.cue(intent, "speaker", actor, {"actor_id": actor}, threshold=0.5, identifies=(actor,)),
            self.cue(intent, "speech", actor, {"content": intent.content, "actor_id": actor},
                     channel="audio", certain_for=(actor, *listeners), only_for=(actor, *listeners)),
        )
        return EffectPlan(EventDraft(kind=self.kind, actor_id=actor, data={"content": intent.content,
                                     "listener_ids": list(intent.listener_ids)}, cues=cues))


class Show(Action[ShowIntent]):
    kind, intent_type = "show", ShowIntent
    salience = {"subtle": 0.5, "normal": 1, "overt": 2}

    def references(self, intent):
        return {intent.item_id, *intent.observer_ids}

    def check(self, context, intent):
        item(context, intent.item_id, held=True)
        for observer in set(intent.observer_ids):
            colocated(context, observer)
            require(context.fluents.transmission(observer, context.actor_id) > 0, "CANNOT_SEE_SHOW")

    def effects(self, context, intent):
        participants = tuple(dict.fromkeys((context.actor_id, *intent.observer_ids)))
        cue = self.cue(intent, "show", context.actor_id,
                       {"actor_id": context.actor_id, "item_id": intent.item_id},
                       certain_for=participants, only_for=participants,
                       identifies=(intent.item_id,), locates=(intent.item_id,))
        return EffectPlan(EventDraft(kind=self.kind, actor_id=context.actor_id,
                                     data={"item_id": intent.item_id, "observer_ids": list(intent.observer_ids)}, cues=(cue,)))


class Search(Action[SearchIntent]):
    kind, intent_type = "search", SearchIntent
    salience = {"subtle": 0.4, "normal": 0.9, "overt": 1.8}

    def check(self, context, intent):
        obj = item(context, intent.container_id)
        require(obj.container is not None, "NOT_CONTAINER", object_id=obj.id)
        require(context.fluents.open(obj.id), "CONTAINER_CLOSED", object_id=obj.id)

    def effects(self, context, intent):
        actor = context.actor_id
        cues = [self.cue(intent, "search", intent.container_id,
                         {"actor_id": actor, "object_id": intent.container_id}, identifies=(intent.container_id,))]
        for entity_id in context.world.state.placements:
            if intent.container_id not in context.world.path(entity_id)[1:]:
                continue
            if context.fluents.transmission(actor, entity_id) <= 0:
                continue
            cues.append(self.cue(intent, "discovery", entity_id, {"entity_id": entity_id},
                                 certain_for=(actor,), only_for=(actor,), identifies=(entity_id,), locates=(entity_id,)))
        return EffectPlan(EventDraft(kind=self.kind, actor_id=actor, data={"container_id": intent.container_id},
                                     cues=tuple(cues)))


class Operate(Action[OperateIntent]):
    kind, intent_type = "operate", OperateIntent
    salience = {"subtle": 0.5, "normal": 1, "overt": 2}

    def check(self, context, intent):
        obj = item(context, intent.device_id)
        require(obj.operable, "NOT_OPERABLE", device_id=obj.id)

    def effects(self, context, intent):
        actor = context.actor_id
        cue = self.cue(intent, "operate", intent.device_id, {"actor_id": actor, "object_id": intent.device_id},
                       certain_for=(actor,), identifies=(intent.device_id,))
        # A valid operation is an actual attempt. Whether it causes a reaction is
        # solely a mechanic decision; there is no operate-to-search substitution.
        return EffectPlan(EventDraft(kind=self.kind, actor_id=actor, data={"device_id": intent.device_id},
                                     signals=("operated",), subject_ids=(intent.device_id,), cues=(cue,)))


class Wait(Action[WaitIntent]):
    kind, intent_type = "wait", WaitIntent

    def check(self, context, intent):
        pass

    def effects(self, context, intent):
        return EffectPlan(EventDraft(kind=self.kind, actor_id=context.actor_id))
