"""Item movement and installation. Spatial attachment is not installation."""

from typing import Literal

from token_odyssey.kernel.actions.base import (
    Action, ActionContext, EffectPlan, Intent, colocated, item, reachable, require,
)
from token_odyssey.kernel.definitions import Character, Item, Room
from token_odyssey.kernel.events import EventDraft, Issue
from token_odyssey.kernel.state import Placement, change_to


class TakeIntent(Intent):
    kind: Literal["take"] = "take"
    item_id: str


class GiveIntent(Intent):
    kind: Literal["give"] = "give"
    item_id: str
    recipient_id: str


class PlaceIntent(Intent):
    kind: Literal["place"] = "place"
    item_id: str
    destination_id: str
    relation: Literal["inside", "attached"]


class HideIntent(Intent):
    kind: Literal["hide"] = "hide"
    item_id: str


class InstallIntent(Intent):
    kind: Literal["install"] = "install"
    item_id: str
    slot_id: str


def relocation(action: Action, context: ActionContext, intent: Intent, item_id: str,
               placement: Placement, *, recipient_id: str | None = None,
               connection: str | None = None) -> EffectPlan:
    state, actor = context.world.state, context.actor_id
    if state.placements[item_id] == placement and state.connections.get(item_id) == connection:
        return EffectPlan(None, notices=(Issue(code="ALREADY_PLACED"),))
    changes = [change_to(state, "placements", item_id, placement.model_dump(mode="json"))]
    if state.connections.get(item_id) != connection:
        changes.append(change_to(state, "connections", item_id, connection))
    participants = (actor, recipient_id) if recipient_id else (actor,)
    data = {"item_id": item_id, "destination_id": placement.parent_id, "relation": placement.relation}
    if recipient_id:
        data["recipient_id"] = recipient_id
    # Raising general action visibility must not erase the explicit semantics
    # of a subtle transfer or the dedicated hide action. Keep roughly the same
    # chance of learning their exact object/recipient details while making the
    # coarse handling motion easier to notice.
    detail_threshold = 0.85 if intent.amplitude == "subtle" else 0.7 if action.kind == "hide" else 0.2
    # Coarse evidence does not name participants or objects. Detailed cues are
    # anchored on the Item, so hidden contents cannot be learned from actor sight.
    cues = (
        action.cue(intent, "handling", actor, {}, threshold=0.1),
        action.cue(intent, action.kind, item_id, {"actor_id": actor, **data}, moment="before",
                   threshold=detail_threshold, identifies=(actor, item_id), locates=()),
        action.cue(intent, "item_location", item_id, {"item_id": item_id}, threshold=detail_threshold,
                   identifies=(item_id,), locates=(item_id,)),
        action.cue(intent, action.kind, actor, {"actor_id": actor, **data},
                   certain_for=participants, only_for=participants,
                   identifies=(item_id,), locates=(item_id,)),
    )
    return EffectPlan(EventDraft(kind=action.kind, actor_id=actor, data=data,
                                 signals=("placement_changed",), subject_ids=(item_id,),
                                 changes=tuple(changes), cues=cues))


class RelocationAction(Action):
    def compose_observation(self, facts):
        facts = super().compose_observation(facts)
        if any(f.kind == self.kind for f in facts):
            return tuple(f for f in facts if f.kind not in {"handling", "item_location"})
        return facts


class Take(RelocationAction):
    kind, intent_type = "take", TakeIntent

    def check(self, context, intent):
        obj = item(context, intent.item_id)
        require(obj.portable, "NOT_PORTABLE", item_id=obj.id)
        require(obj.id not in context.world.path(context.actor_id), "PLACEMENT_CYCLE")

    def effects(self, context, intent):
        # Taking an installed component explicitly detaches its connection too.
        return relocation(self, context, intent, intent.item_id,
                          Placement(parent_id=context.actor_id, relation="attached"))


class Give(RelocationAction):
    kind, intent_type = "give", GiveIntent
    salience = {"subtle": 0.8, "normal": 2.5, "overt": 4.0}

    def check(self, context, intent):
        item(context, intent.item_id, held=True)
        colocated(context, intent.recipient_id)
        require(intent.item_id not in context.world.path(intent.recipient_id), "PLACEMENT_CYCLE")

    def effects(self, context, intent):
        return relocation(self, context, intent, intent.item_id,
                          Placement(parent_id=intent.recipient_id, relation="attached"),
                          recipient_id=intent.recipient_id)


class Place(RelocationAction):
    kind, intent_type = "place", PlaceIntent

    def check(self, context, intent):
        obj = item(context, intent.item_id, held=True)
        reachable(context, intent.destination_id)
        parent = context.world.definition.entities.get(intent.destination_id)
        require(parent is not None, "EXPECTED_PLACEMENT_PARENT")
        require(not isinstance(parent, Character), "USE_GIVE_OR_HIDE")
        require(intent.item_id not in context.world.path(intent.destination_id), "PLACEMENT_CYCLE")
        if intent.relation == "inside":
            require(isinstance(parent, Room) or isinstance(parent, Item) and parent.container is not None,
                    "NOT_CONTAINER", object_id=intent.destination_id)
            require(context.fluents.open(intent.destination_id), "CONTAINER_CLOSED", object_id=intent.destination_id)
            if isinstance(parent, Item):
                require(obj.size <= parent.container.capacity_size, "TOO_LARGE", item_id=obj.id)

    def effects(self, context, intent):
        return relocation(self, context, intent, intent.item_id,
                          Placement(parent_id=intent.destination_id, relation=intent.relation))


class Hide(RelocationAction):
    kind, intent_type = "hide", HideIntent
    salience = {"subtle": 0.3, "normal": 0.8, "overt": 2.5}
    clear_in_room = False

    def check(self, context, intent):
        obj = item(context, intent.item_id, held=True)
        actor = context.world.definition.entities[context.actor_id]
        require(obj.size <= actor.concealment_size, "TOO_LARGE", item_id=obj.id)

    def effects(self, context, intent):
        return relocation(self, context, intent, intent.item_id,
                          Placement(parent_id=context.actor_id, relation="inside"))


class Install(RelocationAction):
    kind, intent_type = "install", InstallIntent
    salience = {"subtle": 0.9, "normal": 2.5, "overt": 4.0}

    def check(self, context, intent):
        item(context, intent.item_id, held=True)
        slot = item(context, intent.slot_id)
        require(slot.slot is not None, "NOT_SLOT", slot_id=slot.id)
        require(intent.item_id in slot.slot.compatible_item_ids, "INCOMPATIBLE_COMPONENT")
        require(slot.id not in context.world.state.connections.values(), "SLOT_OCCUPIED")
        require(intent.item_id not in context.world.path(slot.id), "PLACEMENT_CYCLE")

    def effects(self, context, intent):
        return relocation(self, context, intent, intent.item_id,
                          Placement(parent_id=intent.slot_id, relation="attached"), connection=intent.slot_id)
