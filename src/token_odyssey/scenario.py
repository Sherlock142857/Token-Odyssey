"""Compile authored YAML into a validated, fixed world and participant setup.

This is a deterministic compiler, not a natural-language world generator. A
future text translator should produce this same reviewable input format.
"""

from copy import deepcopy
from pathlib import Path
from typing import Literal

from pydantic import Field, JsonValue, model_validator

from token_odyssey.common import FrozenModel
from token_odyssey.config.models import ParticipantConfig
from token_odyssey.config.yaml import load_mapping
from token_odyssey.kernel.actions.registry import ActionRegistry, builtin_registry
from token_odyssey.kernel.definitions import Predicate, WorldDefinition
from token_odyssey.kernel.state import World, WorldState
from token_odyssey.runtime.routing_policy import RoutingPolicy


class TurnPolicy(FrozenModel):
    max_actions: int = Field(default=5, ge=1, le=50)
    continue_after_move: bool = False
    max_retries: int = Field(default=2, ge=0, le=10)


class RoleBrief(FrozenModel):
    personality: str = ""
    private_goal: str = ""
    memories: tuple[str, ...] = ()
    known_entity_ids: tuple[str, ...] = ()


class Scenario(FrozenModel):
    schema_version: Literal[3] = 3
    id: str
    title: str
    public_background: str = ""
    world: WorldDefinition
    initial_state: WorldState
    roles: dict[str, RoleBrief] = Field(default_factory=dict)
    cast: dict[str, ParticipantConfig] = Field(default_factory=dict)
    scripts: dict[str, tuple[dict[str, JsonValue], ...]] = Field(default_factory=dict)
    turn_policy: TurnPolicy = Field(default_factory=TurnPolicy)
    routing: RoutingPolicy = Field(default_factory=RoutingPolicy)
    seed: int = 7
    max_rounds: int = Field(default=20, ge=1, le=10000)
    end_when: tuple[Predicate, ...] = ()
    expected: tuple[Predicate, ...] = ()

    def create_world(self) -> World:
        return World(self.world.model_copy(deep=True), self.initial_state.model_copy(deep=True))

    @model_validator(mode="after")
    def references(self):
        self.create_world().validate()
        if self.initial_state.revision != 0:
            raise ValueError("a Scenario starts at revision 0")
        actors = set(self.world.character_ids)
        for mapping in (self.roles, self.cast, self.scripts):
            if set(mapping) - actors:
                raise ValueError("role/cast/script references an unknown Character")
        objects = set(self.world.entities) | set(self.world.passages)
        if set(self.routing.interests) - actors:
            raise ValueError("routing interests reference an unknown Character")
        for interests in self.routing.interests.values():
            if set(interests) - objects:
                raise ValueError("routing interests reference an unknown object")
        for brief in self.roles.values():
            if set(brief.known_entity_ids) - objects:
                raise ValueError("role references unknown prior identity")
        for predicate in (*self.end_when, *self.expected):
            self.world.validate_atom(predicate)
        return self


def compile_scenario(raw: dict, registry: ActionRegistry | None = None) -> Scenario:
    data = deepcopy(raw)
    if data.get("schema_version") != 3:
        raise ValueError("Scenario schema_version must be 3; legacy scenarios are not supported")
    definition = data.get("world", {})
    if not isinstance(definition, dict):
        raise ValueError("world must be a mapping")
    for collection in ("entities", "passages"):
        objects = definition.get(collection, {})
        if not isinstance(objects, dict):
            raise ValueError(f"world.{collection} must be a mapping")
        for object_id, obj in objects.items():
            if not isinstance(obj, dict):
                raise ValueError(f"{object_id}: definition must be a mapping")
            obj.setdefault("id", object_id)
    world_definition = WorldDefinition.model_validate(definition)
    initial = data.setdefault("initial_state", {})
    if not isinstance(initial, dict):
        raise ValueError("initial_state must be a mapping")
    objects = {**world_definition.entities, **world_definition.passages}
    for table, capability in (("openings", "openable"), ("locks", "lockable")):
        values = initial.setdefault(table, {})
        if not isinstance(values, dict):
            raise ValueError(f"initial_state.{table} must be a mapping")
        for key, obj in objects.items():
            if getattr(obj, capability, None):
                values.setdefault(key, False)
    for flag in world_definition.flag_names:
        initial.setdefault("flags", {}).setdefault(flag, False)
    scenario = Scenario.model_validate(data)
    actions = registry or builtin_registry()
    for actor, batches in scenario.scripts.items():
        for index, raw_batch in enumerate(batches):
            batch = actions.parse_batch(raw_batch)
            if len(batch.actions) > scenario.turn_policy.max_actions:
                raise ValueError(f"{actor} script {index}: too many actions")
            for intent in batch.actions:
                refs = actions.get(intent.kind).references(intent)
                if refs - set(objects):
                    raise ValueError(f"{actor} script {index}: unknown references {sorted(refs - set(objects))}")
    return scenario


def load_scenario(path: str | Path, registry: ActionRegistry | None = None) -> Scenario:
    return compile_scenario(load_mapping(path), registry)
