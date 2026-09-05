"""Immutable authored definitions. Dynamic values live exclusively in WorldState.

Capabilities are composed rather than represented by a container/door/device
inheritance tree. Passages are room boundaries, not doubly placed Items.
"""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from token_odyssey.common import FrozenModel

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")]
Coefficient = Annotated[float, Field(ge=0, le=1)]


class Openable(FrozenModel):
    open_visibility: Coefficient = 1
    closed_visibility: Coefficient = 0
    closed_sound: Coefficient = 0.3


class Lockable(FrozenModel):
    key_item_ids: tuple[Identifier, ...] = Field(min_length=1)


class Container(FrozenModel):
    capacity_size: int = Field(default=10, ge=1, le=10)
    visibility: Coefficient = 1


class Slot(FrozenModel):
    compatible_item_ids: tuple[Identifier, ...] = Field(min_length=1)


class Entity(FrozenModel):
    id: Identifier
    name: str = Field(min_length=1)
    description: str = ""


class Room(Entity):
    kind: Literal["room"] = "room"
    light: Coefficient = 1


class Character(Entity):
    kind: Literal["character"] = "character"
    size: int = Field(default=6, ge=1, le=10)
    concealment_size: int = Field(default=3, ge=1, le=10)
    concealed_visibility: Coefficient = 0.3


class Item(Entity):
    kind: Literal["item"] = "item"
    size: int = Field(default=2, ge=1, le=10)
    portable: bool = True
    visibility: Coefficient = 1
    container: Container | None = None
    openable: Openable | None = None
    lockable: Lockable | None = None
    slot: Slot | None = None
    operable: bool = False

    @model_validator(mode="after")
    def capabilities(self):
        if self.lockable and not self.openable:
            raise ValueError(f"{self.id}: lockable requires openable")
        if self.openable and not self.container:
            raise ValueError(f"{self.id}: an openable Item requires a container")
        return self


SpatialEntity = Annotated[Room | Character | Item, Field(discriminator="kind")]


class Passage(Entity):
    rooms: tuple[Identifier, Identifier]
    # Movement and perception have separate directions and coefficients.
    forward_travel: bool = True
    reverse_travel: bool = True
    forward_visibility: Coefficient = 1
    reverse_visibility: Coefficient = 1
    sound: Coefficient = 1
    openable: Openable | None = None
    lockable: Lockable | None = None

    @model_validator(mode="after")
    def endpoints(self):
        if self.rooms[0] == self.rooms[1]:
            raise ValueError(f"{self.id}: passage must join two distinct rooms")
        if self.lockable and not self.openable:
            raise ValueError(f"{self.id}: lockable requires openable")
        return self


class Predicate(FrozenModel):
    """Small declarative vocabulary. value=False negates an atom."""

    kind: Literal["inside", "attached", "installed", "open", "locked", "flag"]
    subject_id: Identifier
    object_id: Identifier | None = None
    value: bool = True

    @model_validator(mode="after")
    def arity(self):
        relational = self.kind in {"inside", "attached", "installed"}
        if relational != (self.object_id is not None):
            raise ValueError(f"{self.kind}: invalid object_id")
        return self


class Effect(FrozenModel):
    """Mechanics set explicit facts; arbitrary code is not accepted in YAML."""

    kind: Literal["open", "locked", "flag"]
    subject_id: Identifier
    value: bool


class MechanicRule(FrozenModel):
    id: Identifier
    trigger: Literal["placement_changed", "state_changed", "operated"]
    subject_id: Identifier | None = None
    when: tuple[Predicate, ...] = ()
    effects: tuple[Effect, ...] = ()
    once: bool = True
    source_id: Identifier
    # Authored sensory content, like an entity description; not a renderer.
    visual_description: str = ""
    sound_description: str = ""
    visibility: Coefficient = 1
    audibility: Coefficient = 1

    @model_validator(mode="after")
    def unique_effects(self):
        keys = [(effect.kind, effect.subject_id) for effect in self.effects]
        if len(set(keys)) != len(keys):
            raise ValueError(f"{self.id}: duplicate effects for the same fact")
        return self


class WorldDefinition(FrozenModel):
    entities: dict[Identifier, SpatialEntity]
    passages: dict[Identifier, Passage] = Field(default_factory=dict)
    flag_names: tuple[Identifier, ...] = ()
    mechanics: tuple[MechanicRule, ...] = ()
    max_reactions_per_action: int = Field(default=32, ge=1, le=1000)

    @property
    def character_ids(self) -> tuple[str, ...]:
        return tuple(k for k, v in self.entities.items() if isinstance(v, Character))

    @property
    def room_ids(self) -> tuple[str, ...]:
        return tuple(k for k, v in self.entities.items() if isinstance(v, Room))

    def object(self, object_id: str) -> SpatialEntity | Passage:
        return self.entities[object_id] if object_id in self.entities else self.passages[object_id]

    @model_validator(mode="after")
    def references(self):
        if not self.room_ids or not self.character_ids:
            raise ValueError("world requires at least one Room and Character")
        if set(self.entities) & set(self.passages):
            raise ValueError("entity and passage IDs must be disjoint")
        for key, obj in {**self.entities, **self.passages}.items():
            if key != obj.id:
                raise ValueError(f"mapping key {key} differs from object id {obj.id}")
            lock = getattr(obj, "lockable", None)
            for item_id in lock.key_item_ids if lock else ():
                if not isinstance(self.entities.get(item_id), Item):
                    raise ValueError(f"{key}: unknown key Item {item_id}")
            slot = getattr(obj, "slot", None)
            for item_id in slot.compatible_item_ids if slot else ():
                if not isinstance(self.entities.get(item_id), Item) or item_id == key:
                    raise ValueError(f"{key}: invalid compatible Item {item_id}")
        for passage in self.passages.values():
            if not set(passage.rooms) <= set(self.room_ids):
                raise ValueError(f"{passage.id}: unknown Room endpoint")
        if len(set(self.flag_names)) != len(self.flag_names):
            raise ValueError("duplicate flag name")
        if len({r.id for r in self.mechanics}) != len(self.mechanics):
            raise ValueError("duplicate mechanic id")
        objects = set(self.entities) | set(self.passages)
        for rule in self.mechanics:
            if rule.source_id not in objects:
                raise ValueError(f"{rule.id}: unknown source {rule.source_id}")
            if rule.subject_id and rule.subject_id not in objects | set(self.flag_names):
                raise ValueError(f"{rule.id}: unknown trigger subject")
            for atom in (*rule.when, *rule.effects):
                self.validate_atom(atom)
        return self

    def validate_atom(self, atom: Predicate | Effect) -> None:
        if atom.kind == "flag":
            if atom.subject_id not in self.flag_names:
                raise ValueError(f"unknown flag {atom.subject_id}")
            return
        try:
            obj = self.object(atom.subject_id)
        except KeyError as exc:
            raise ValueError(f"unknown predicate/effect subject {atom.subject_id}") from exc
        capability = {"open": "openable", "locked": "lockable"}.get(atom.kind)
        if capability and not getattr(obj, capability, None):
            raise ValueError(f"{atom.subject_id} lacks {capability}")
        if isinstance(atom, Predicate) and atom.object_id:
            parent = self.entities.get(atom.object_id)
            if atom.subject_id not in self.entities or isinstance(obj, Room) or parent is None:
                raise ValueError("spatial predicate requires a placed entity and an entity parent")
            if atom.kind == "installed" and (
                not isinstance(obj, Item) or not isinstance(parent, Item) or not parent.slot
            ):
                raise ValueError("installed predicate requires an Item and a Slot")
            if atom.kind == "installed" and atom.subject_id not in parent.slot.compatible_item_ids:
                raise ValueError("installed predicate references an incompatible component")
            if atom.kind == "inside" and isinstance(parent, Item) and not parent.container:
                raise ValueError("inside predicate requires a container parent")
