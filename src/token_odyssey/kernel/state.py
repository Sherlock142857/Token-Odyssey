"""Dynamic facts and their only update vocabulary.

Recorded changes are data, never callable mutations. The exact same changes
update a transaction draft and replay a committed log.
"""

from typing import Literal

from pydantic import Field, JsonValue, StrictBool

from token_odyssey.common import FrozenModel, Model
from token_odyssey.kernel.definitions import Character, Item, Room, WorldDefinition


class Placement(FrozenModel):
    parent_id: str
    relation: Literal["inside", "attached"] = "inside"


class WorldState(Model):
    placements: dict[str, Placement]
    openings: dict[str, StrictBool] = Field(default_factory=dict)
    locks: dict[str, StrictBool] = Field(default_factory=dict)
    connections: dict[str, str] = Field(default_factory=dict)
    flags: dict[str, StrictBool] = Field(default_factory=dict)
    fired_rules: dict[str, StrictBool] = Field(default_factory=dict)
    revision: int = Field(default=0, ge=0)


class Change(FrozenModel):
    table: Literal["placements", "openings", "locks", "connections", "flags", "fired_rules"]
    key: str
    before: JsonValue
    after: JsonValue


def value_at(state: WorldState, table: str, key: str) -> JsonValue:
    value = getattr(state, table).get(key)
    return value.model_dump(mode="json") if isinstance(value, Placement) else value


def change_to(state: WorldState, table: str, key: str, after: JsonValue) -> Change:
    return Change(table=table, key=key, before=value_at(state, table, key), after=after)


def apply_changes(state: WorldState, changes: tuple[Change, ...] | list[Change]) -> None:
    """Mutates a private draft only. Caller validates and owns the commit boundary."""
    for change in changes:
        if value_at(state, change.table, change.key) != change.before:
            raise ValueError(f"stale change: {change.table}.{change.key}")
        table = getattr(state, change.table)
        if change.after is None:
            table.pop(change.key, None)
        else:
            value = Placement.model_validate(change.after) if change.table == "placements" else change.after
            table[change.key] = value


class World:
    """Read facade for a definition/state pair; Harness owns the authoritative pair.

    Actions receive isolated snapshots, so even accidental mutation cannot write
    canonical state. All public Harness properties also return isolated copies.
    """

    def __init__(self, definition: WorldDefinition, state: WorldState):
        self.definition = definition
        self.state = state

    def snapshot(self) -> "World":
        return World(self.definition.model_copy(deep=True), self.state.model_copy(deep=True))

    def path(self, entity_id: str) -> tuple[str, ...]:
        path = [entity_id]
        while path[-1] in self.state.placements:
            parent = self.state.placements[path[-1]].parent_id
            if parent in path:
                raise ValueError(f"placement cycle at {parent}")
            path.append(parent)
        return tuple(path)

    def room_of(self, entity_id: str) -> str:
        return self.path(entity_id)[-1]

    def location_signature(self, entity_id: str) -> tuple[tuple[str, str, str], ...]:
        # Include ancestor edges: moving a box also moves everything in it.
        return tuple(
            (node, self.state.placements[node].relation, self.state.placements[node].parent_id)
            for node in self.path(entity_id) if node in self.state.placements
        )

    def validate(self) -> None:
        d, s = self.definition, self.state
        # Revalidate dictionary values, because in-place dict changes do not invoke
        # Pydantic's assignment validation.
        WorldState.model_validate(s.model_dump(mode="python"))
        placed = set(d.entities) - set(d.room_ids)
        if set(s.placements) != placed:
            raise ValueError("every non-Room needs exactly one placement; Rooms have none")
        for child_id, placement in s.placements.items():
            parent = d.entities.get(placement.parent_id)
            if parent is None:
                raise ValueError(f"{child_id}: unknown placement parent")
            if self.room_of(child_id) not in d.room_ids:
                raise ValueError(f"{child_id}: placement must terminate at a Room")
            if placement.relation == "inside":
                if isinstance(parent, Item) and parent.container is None:
                    raise ValueError(f"{child_id}: inside a non-container")
                child = d.entities[child_id]
                limit = (parent.container.capacity_size if isinstance(parent, Item)
                         else parent.concealment_size if isinstance(parent, Character) else 10)
                if not isinstance(parent, Room) and getattr(child, "size", 1) > limit:
                    raise ValueError(f"{child_id}: exceeds container size limit")
        objects = {**d.entities, **d.passages}
        openable = {k for k, v in objects.items() if getattr(v, "openable", None)}
        lockable = {k for k, v in objects.items() if getattr(v, "lockable", None)}
        if set(s.openings) != openable or set(s.locks) != lockable:
            raise ValueError("open/lock facts must exactly match declared capabilities")
        if any(s.openings[k] and locked for k, locked in s.locks.items()):
            raise ValueError("a locked object cannot be open")
        if set(s.flags) != set(d.flag_names):
            raise ValueError("state flags must match declared flag_names")
        once_rules = {r.id for r in d.mechanics if r.once}
        if set(s.fired_rules) - once_rules or not all(s.fired_rules.values()):
            raise ValueError("fired_rules may only contain fired once-only mechanics")
        occupied: set[str] = set()
        for item_id, slot_id in s.connections.items():
            slot = d.entities.get(slot_id)
            if not isinstance(slot, Item) or not slot.slot or item_id not in slot.slot.compatible_item_ids:
                raise ValueError("incompatible installation connection")
            if slot_id in occupied:
                raise ValueError("a Slot accepts one installed component")
            occupied.add(slot_id)
            if s.placements.get(item_id) != Placement(parent_id=slot_id, relation="attached"):
                raise ValueError("an installed component must be attached to its Slot")
