"""Compile concise authored Scenario v2 YAML into canonical runtime state."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from token_odyssey.inside_act.domain.scenario import Scenario
from token_odyssey.inside_act.domain.spatial import WorldRules


def load_scenario(path: str | Path) -> Scenario:
    scenario_path = Path(path)
    raw: Any = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"scenario root must be a mapping: {scenario_path}")
    compiled = compile_scenario(raw)
    return Scenario.model_validate(compiled)


def compile_scenario(raw: dict[str, Any]) -> dict[str, Any]:
    data = deepcopy(raw)
    if data.get("schema_version") != 2:
        raise ValueError("Scenario schema_version must be 2")
    world = data.setdefault("world", {})
    rules = WorldRules.model_validate(world.get("rules", {}))
    world["rules"] = rules.model_dump(mode="json")
    world.setdefault("mechanics", {"installations": [], "operations": []})
    world.setdefault("revision", 0)
    world.setdefault("room_graph", {"edges": {}})
    entities = world.get("entities")
    if not isinstance(entities, dict):
        raise ValueError("Scenario v2 world.entities must be a mapping")
    for entity_id, entity in entities.items():
        if not isinstance(entity, dict):
            raise ValueError(f"entity {entity_id!r} must be a mapping")
        entity.setdefault("id", entity_id)
        entity.setdefault("description", "")
        entity.setdefault("protector_id", None)
        kind = entity.get("kind")
        if kind == "room":
            entity["is_container"] = True
            entity.setdefault(
                "container_visibility", rules.default_room_container_visibility
            )
        elif kind == "character":
            entity["is_container"] = True
            entity["container_visibility"] = rules.actor_container_visibility
            entity.setdefault("size_class", rules.actor_size_class)
            if not entity.get("description"):
                entity["description"] = entity.get("appearance", "")
        elif kind == "item":
            entity.setdefault("is_container", False)
            entity.setdefault("container_visibility", 1.0)
            entity.setdefault("intrinsic_visibility", 1.0)
        else:
            raise ValueError(f"entity {entity_id!r} has unknown kind {kind!r}")
    return data


def read_api_key(path: str | Path) -> str:
    key_path = Path(path)
    lines = [
        line.strip()
        for line in key_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(lines) != 1:
        raise ValueError(f"API key file must contain exactly one non-empty line: {key_path}")
    return lines[0]
