"""Travel through an adjacent passage; no teleportation or implied interaction."""

from typing import Literal

from token_odyssey.kernel.actions.base import Action, ActionContext, EffectPlan, Intent, require
from token_odyssey.kernel.definitions import Room
from token_odyssey.kernel.events import EventDraft, Fact, Issue
from token_odyssey.kernel.state import Placement, change_to


class MoveIntent(Intent):
    kind: Literal["move"] = "move"
    destination_room_id: str
    passage_id: str | None = None


class Move(Action[MoveIntent]):
    kind, intent_type = "move", MoveIntent
    salience = {"subtle": 1.0, "normal": 3.0, "overt": 5.0}

    def compose_observation(self, facts):
        facts = super().compose_observation(facts)
        departure = next((f for f in facts if f.kind == "departure"), None)
        arrival = next((f for f in facts if f.kind == "arrival"), None)
        if departure and arrival:
            return (Fact(kind="move", fields={**departure.fields, **arrival.fields}),)
        return facts

    def passage(self, context: ActionContext, intent: MoveIntent) -> str | None:
        candidates = ([intent.passage_id] if intent.passage_id else context.world.definition.passages)
        return next((p for p in candidates if p in context.world.definition.passages
                     and context.fluents.can_traverse(context.actor_id, p, intent.destination_room_id)), None)

    def check(self, context: ActionContext, intent: MoveIntent) -> None:
        require(isinstance(context.world.definition.entities.get(intent.destination_room_id), Room),
                "EXPECTED_ROOM", room_id=intent.destination_room_id)
        if context.world.room_of(context.actor_id) != intent.destination_room_id:
            require(self.passage(context, intent) is not None, "NO_OPEN_PASSAGE", room_id=intent.destination_room_id)

    def effects(self, context: ActionContext, intent: MoveIntent) -> EffectPlan:
        actor = context.actor_id
        origin = context.world.room_of(actor)
        destination = intent.destination_room_id
        if origin == destination:
            return EffectPlan(None, notices=(Issue(code="ALREADY_THERE"),))
        witnesses = tuple(c for c in context.world.definition.character_ids if c != actor)
        event = EventDraft(
            kind=self.kind, actor_id=actor,
            data={"from_room_id": origin, "destination_room_id": destination, "passage_id": self.passage(context, intent)},
            signals=("placement_changed",), subject_ids=(actor,),
            changes=(change_to(context.world.state, "placements", actor,
                               Placement(parent_id=destination).model_dump(mode="json")),),
            cues=(
                self.cue(intent, "departure", actor, {"actor_id": actor, "from_room_id": origin},
                         moment="before", identifies=(actor, origin), only_for=witnesses),
                self.cue(intent, "arrival", actor, {"actor_id": actor, "destination_room_id": destination},
                         moment="after", identifies=(actor, destination), locates=(actor,), only_for=witnesses),
                self.cue(intent, "travel_result", actor, {"room_id": destination},
                         certain_for=(actor,), only_for=(actor,)),
            ),
        )
        return EffectPlan(event, rescan_actor=True, ends_batch=True)
