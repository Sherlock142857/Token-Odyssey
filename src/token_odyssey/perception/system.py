"""Numerical evidence -> authorized facts -> character memory.

Environment scans never enumerate another room's Items or Characters. Event
projection may cross room boundaries using the event's own sensory channel.
Sampling outcomes are journaled so replay does not depend on RNG call order.
"""

import random
from collections.abc import Callable

from token_odyssey.kernel.definitions import Room
from token_odyssey.kernel.events import ActionResult, Fact
from token_odyssey.kernel.fluents import Fluents
from token_odyssey.kernel.state import World
from token_odyssey.perception.models import ActorView, EntityView, ExitView, KnownEntity, Memory, Observation


class ObservationSystem:
    def __init__(self, actor_ids: tuple[str, ...], seed: int,
                 on_observation: Callable[[Observation], None] | None = None,
                 on_sample: Callable[[dict], None] | None = None):
        self.memories = {actor: Memory() for actor in actor_ids}
        self.rng = random.Random(seed)
        self.log: list[Observation] = []
        self.on_observation = on_observation or (lambda observation: None)
        self.on_sample = on_sample or (lambda sample: None)

    def initialize_known(self, world: World, actor_id: str, entity_ids: tuple[str, ...]) -> None:
        # Authored prior identity knowledge is not omniscient location knowledge.
        for entity_id in dict.fromkeys((actor_id, *entity_ids)):
            if entity_id in self.memories[actor_id].known:
                continue
            obj = world.definition.object(entity_id)
            view = EntityView(id=obj.id, name=obj.name, kind=getattr(obj, "kind", "passage"), description=obj.description,
                              basis="prior")
            self.memories[actor_id].known[entity_id] = KnownEntity(view=view)

    def known_ids(self, actor_id: str) -> frozenset[str]:
        return frozenset(self.memories[actor_id].known)

    def _remember(self, world: World, actor_id: str, entity_id: str, *, locate: bool, basis: str) -> EntityView:
        memory = self.memories[actor_id]
        obj = world.definition.object(entity_id)
        previous = memory.known.get(entity_id)
        placement = None
        signature = previous.location_signature if previous else None
        if locate and entity_id in world.state.placements:
            signature = world.location_signature(entity_id)
            edge = world.state.placements[entity_id]
            if edge.parent_id in memory.known or isinstance(world.definition.entities[edge.parent_id], Room):
                placement = edge
        elif previous:
            placement = previous.view.placement
        capabilities = tuple(name for name in ("container", "openable", "lockable", "slot", "operable")
                             if getattr(obj, name, None))
        view = EntityView(id=obj.id, name=obj.name, kind=getattr(obj, "kind", "passage"),
                          description=obj.description if previous is None else None,
                          placement=placement, capabilities=capabilities,
                          is_open=world.state.openings.get(entity_id), basis=basis)
        # Identification alone does not reveal dynamic state. is_open is visible
        # in scans, or when localization proves direct perceptual contact.
        if not locate:
            view = view.model_copy(update={"is_open": previous.view.is_open if previous else None})
        memory.known[entity_id] = KnownEntity(view=view, location_signature=signature,
                                            observed_revision=world.state.revision)
        return view

    def _record(self, actor_id: str, revision: int, source: str, *, event_sequence: int | None = None,
                facts: tuple[Fact, ...] = (), entities: tuple[EntityView, ...] = (), labels: dict | None = None) -> Observation:
        observation = Observation(sequence=len(self.log) + 1, observer_id=actor_id, world_revision=revision,
                                  source=source, source_event_sequence=event_sequence, facts=facts,
                                  entities=entities, labels=labels or {})
        self.log.append(observation)
        self.memories[actor_id].inbox.append(observation)
        self.on_observation(observation)
        return observation

    def project(self, result: ActionResult) -> None:
        if result.transaction is None:
            return
        for frame in result.frames:
            event = frame.event
            for actor_id in self.memories:
                facts, views, labels, evidence = [], {}, {}, {}
                for cue in event.cues:
                    if cue.only_for is not None and actor_id not in cue.only_for:
                        continue
                    world = frame.before if cue.moment == "before" else frame.after
                    key = (cue.anchor_id, cue.moment, cue.channel, cue.salience, cue.requires)
                    if actor_id in cue.certain_for:
                        score, roll, quality = 1.0, None, 1.0
                    else:
                        if key not in evidence:
                            scores = [Fluents(world).transmission(actor_id, cue.anchor_id, cue.channel)]
                            for required in cue.requires:
                                required_world = frame.before if required.moment == "before" else frame.after
                                scores.append(Fluents(required_world).transmission(actor_id, required.object_id, cue.channel))
                            score = min(1.0, min(scores) * cue.salience)
                            roll = self.rng.random() if score > 0 else None
                            # score is detection probability. Conditional quality
                            # gives the action's thresholds a continuous domain.
                            quality = max(0.0, 1 - roll / score) if score > 0 else 0.0
                            evidence[key] = score, roll, quality
                        score, roll, quality = evidence[key]
                    allowed = quality > 0 and quality >= cue.threshold
                    self.on_sample({"source": "event", "event_sequence": event.sequence,
                                    "observer_id": actor_id, "anchor_id": cue.anchor_id,
                                    "moment": cue.moment, "channel": cue.channel,
                                    "score": score, "roll": roll, "quality": quality,
                                    "threshold": cue.threshold, "allowed": allowed})
                    if not allowed:
                        continue
                    if cue.fact not in facts:
                        facts.append(cue.fact)
                    # Names below are only looked up for already authorized fields.
                    for field, value in cue.fact.fields.items():
                        if field.endswith("_id") and isinstance(value, str):
                            if value in world.definition.entities or value in world.definition.passages:
                                labels[value] = world.definition.object(value).name
                    for entity_id in dict.fromkeys((*cue.identifies, *cue.locates)):
                        views[entity_id] = self._remember(world, actor_id, entity_id,
                                                         locate=entity_id in cue.locates, basis="event")
                if facts or views:
                    self._record(actor_id, result.transaction.after_revision, "event", event_sequence=event.sequence,
                                 facts=tuple(facts), entities=tuple(views.values()), labels=labels)

    def scan(self, world: World, actor_id: str) -> tuple[EntityView, ...]:
        f, memory = Fluents(world), self.memories[actor_id]
        room_id = world.room_of(actor_id)
        visible = []
        for entity_id, entity in world.definition.entities.items():
            if entity_id == actor_id or isinstance(entity, Room) or world.room_of(entity_id) != room_id:
                continue
            edge = world.state.placements[entity_id]
            direct = edge.parent_id == actor_id
            score = f.transmission(actor_id, entity_id)
            roll = None if direct or score <= 0 else self.rng.random()
            identified = direct or (roll is not None and roll < score)
            previous = memory.known.get(entity_id)
            retained = (not identified and score > 0 and previous is not None
                        and previous.location_signature == world.location_signature(entity_id))
            basis = "inventory" if direct else "scan" if identified else "continuity" if retained else "none"
            self.on_sample({"source": "scan", "observer_id": actor_id, "entity_id": entity_id,
                            "world_revision": world.state.revision, "score": score, "roll": roll, "basis": basis})
            if identified:
                view = self._remember(world, actor_id, entity_id, locate=True, basis=basis)
            elif retained:
                # Weak continued localization does not refresh unobserved dynamic
                # attributes (e.g. a lock or opening changed without being seen).
                view = previous.view.model_copy(update={"basis": "continuity", "description": None})
            else:
                continue
            visible.append(view)
            self._record(actor_id, world.state.revision, basis, entities=(view,))
        return tuple(visible)

    def view(self, world: World, actor_id: str, *, max_actions: int, continue_after_move: bool) -> ActorView:
        memory, f = self.memories[actor_id], Fluents(world)
        room_id = world.room_of(actor_id)
        room = world.definition.entities[room_id]
        if room_id not in memory.known:
            self.initialize_known(world, actor_id, (room_id,))
        visible = self.scan(world, actor_id)
        exits = []
        for passage in world.definition.passages.values():
            if room_id not in passage.rooms or f.transmission(actor_id, passage.id) <= 0:
                continue
            destination = next(r for r in passage.rooms if r != room_id)
            # Adjacent exits are part of the room description, separate from the
            # Item/Character scan restriction. Hidden remote contents stay absent.
            for object_id in (passage.id, destination):
                if object_id not in memory.known:
                    self.initialize_known(world, actor_id, (object_id,))
            exit_view = ExitView(passage_id=passage.id, name=passage.name, destination_room_id=destination,
                                 destination_name=world.definition.entities[destination].name,
                                 is_open=f.open(passage.id),
                                 allows_travel=f.can_traverse(actor_id, passage.id, destination))
            exits.append(exit_view)
        # The room/exits are also a perception result, not an unjournaled shortcut
        # from WorldState into the participant's context.
        self._record(actor_id, world.state.revision, "room",
                     facts=(Fact(kind="location", fields={"room_id": room_id}),
                            *(Fact(kind="exit", fields=exit_view.model_dump(mode="json")) for exit_view in exits)),
                     labels={room_id: room.name})
        inventory, items, characters = [], [], []
        for entity in visible:
            edge = world.state.placements[entity.id]
            target = inventory if edge.parent_id == actor_id else characters if entity.kind == "character" else items
            target.append(entity)
        view = ActorView(actor_id=actor_id, room_id=room_id, room_name=room.name, room_description=room.description,
                         exits=tuple(exits), inventory=tuple(inventory), items=tuple(items), characters=tuple(characters),
                         observations=tuple(memory.inbox), feedback=tuple(memory.feedback),
                         max_actions=max_actions, continue_after_move=continue_after_move)
        memory.inbox.clear()
        memory.feedback.clear()
        return view
