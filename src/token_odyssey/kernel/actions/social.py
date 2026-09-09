"""Speech, directed showing, deliberate search, operation, and waiting."""

from typing import Literal

from pydantic import Field

from token_odyssey.kernel.actions.base import Action, EffectPlan, Intent, colocated, item, require
from token_odyssey.kernel.definitions import Character, Item, Room
from token_odyssey.kernel.events import EventDraft, Fact


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


class InspectIntent(Intent):
    kind: Literal["inspect"] = "inspect"
    target_id: str


class OperateIntent(Intent):
    kind: Literal["operate"] = "operate"
    device_id: str


class WaitIntent(Intent):
    kind: Literal["wait"] = "wait"


class Say(Action[SayIntent]):
    kind, intent_type = "say", SayIntent
    salience = {"subtle": 0.2, "normal": 1, "overt": 3}

    def compose_observation(self, facts):
        facts = super().compose_observation(facts)
        speech = next((f for f in facts if f.kind == "speech"), None)
        speaker = next((f for f in facts if f.kind == "speaker"), None)
        if speech:
            fields = {**(speaker.fields if speaker else {}), **speech.fields}
            return (Fact(kind="speech", fields=fields),)
        if speaker:
            return (speaker,)
        return facts

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
    salience = {"subtle": 0.4, "normal": 1.0, "overt": 1.8}

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


class Inspect(Action[InspectIntent]):
    """Deliberate visual observation of an object and what it visibly carries.

    Description layers come from the target's authored ``perception`` modes.
    Discovery remains cue-driven, so this action does not add knowledge state or
    bypass containers, concealment, lighting, or the observation sampler.
    """

    kind, intent_type = "inspect", InspectIntent
    salience = {"subtle": 0.4, "normal": 1.0, "overt": 1.8}

    def check(self, context, intent):
        obj = context.world.definition.entities.get(intent.target_id)
        require(isinstance(obj, (Character, Item)) and not isinstance(obj, Room),
                "NOT_INSPECTABLE", object_id=intent.target_id)
        require(context.fluents.same_room(context.actor_id, intent.target_id),
                "NOT_ACCESSIBLE", object_id=intent.target_id)
        controller = context.fluents.controller(intent.target_id)
        require(controller in {None, context.actor_id}, "CONTROLLED_BY_OTHER", object_id=intent.target_id)
        require(context.fluents.transmission(context.actor_id, intent.target_id) > 0,
                "NOT_VISIBLE", object_id=intent.target_id)

    def effects(self, context, intent):
        actor, target_id = context.actor_id, intent.target_id
        target = context.world.definition.entities[target_id]
        target_mode = target.perception_for(self.kind)
        fields = {"actor_id": actor, "object_id": target_id}
        cues = [
            self.cue(intent, self.kind, target_id, fields, threshold=0.5,
                     identifies=(actor, target_id)),
            self.cue(intent, self.kind, target_id, fields, certain_for=(actor,), only_for=(actor,),
                     identifies=(target_id,), locates=(target_id,)),
        ]
        if target_mode.description:
            cues.append(self.cue(
                intent, self.kind, target_id, fields, threshold=target_mode.threshold,
                salience=target_mode.salience, clear_in_room=True, only_for=(actor,),
                identifies=(target_id,), locates=(target_id,),
                describes={target_id: target_mode.description},
            ))
        # Inspecting a Character (or a transparent/open container) can disclose
        # descendants, but each candidate keeps its own configured evidence
        # threshold and the ordinary spatial transmission chain.
        for entity_id, entity in context.world.definition.entities.items():
            if entity_id in {actor, target_id} or isinstance(entity, Room):
                continue
            if target_id not in context.world.path(entity_id)[1:]:
                continue
            if context.fluents.transmission(actor, entity_id) <= 0:
                continue
            mode = entity.perception_for(self.kind)
            cues.append(self.cue(
                intent, "discovery", entity_id, {"entity_id": entity_id},
                threshold=mode.threshold, salience=mode.salience, clear_in_room=True,
                only_for=(actor,), identifies=(entity_id,), locates=(entity_id,),
                describes={entity_id: mode.description} if mode.description else {},
            ))
        return EffectPlan(EventDraft(kind=self.kind, actor_id=actor,
                                     data={"object_id": target_id}, cues=tuple(cues)))


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
