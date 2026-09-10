"""Pure normalization and validation for generated Campaign scenarios."""

from copy import deepcopy
from typing import Any

from token_odyssey.kernel.actions.registry import ActionRegistry
from token_odyssey.kernel.definitions import Entity, Item, Room
from token_odyssey.scenario import RoleBrief, Scenario, compile_scenario

from .models import ActBrief, CampaignBible, EntityCanon


def physical_continuity_world(world: Any) -> Any:
    """Copy physical definitions without transient character prose."""

    if not isinstance(world, dict):
        return world
    result = deepcopy(world)
    entities = result.get("entities", {})
    if isinstance(entities, dict):
        for entity in entities.values():
            if isinstance(entity, dict) and entity.get("kind") == "character":
                entity.pop("description", None)
                entity.pop("perception", None)
    return result


def normalize_campaign_scenario_raw(
    raw: dict[str, Any], brief: ActBrief, bible: CampaignBible | None
) -> dict[str, Any]:
    """Apply Campaign-owned character canon and harmless state-default repairs."""

    result = deepcopy(raw)
    world = result.get("world")
    if not isinstance(world, dict):
        return result
    entities = world.get("entities")
    if isinstance(entities, dict):
        for actor_id in brief.cast_ids:
            entity = entities.get(actor_id)
            canon = bible.character(actor_id) if bible else None
            if isinstance(entity, dict) and entity.get("kind") == "character" and canon is not None:
                entity["description"] = canon.description
                entity.pop("perception", None)

    objects: dict[str, Any] = {}
    for collection in ("entities", "passages"):
        values = world.get(collection)
        if isinstance(values, dict):
            objects.update(values)
    initial = result.get("initial_state")
    if not isinstance(initial, dict):
        return result
    for table, capability, implicit_default in (
        ("openings", "openable", True),
        ("locks", "lockable", False),
    ):
        facts = initial.get(table)
        if not isinstance(facts, dict):
            continue
        for object_id, value in list(facts.items()):
            obj = objects.get(object_id)
            if isinstance(obj, dict) and obj.get(capability) is None and value is implicit_default:
                del facts[object_id]
    return result


def validate_campaign_scenario(
    raw: dict[str, Any],
    brief: ActBrief,
    *,
    registry: ActionRegistry,
    bible: CampaignBible | None,
    protagonist_id: str,
    entity_canon: dict[str, EntityCanon],
    memories: dict[str, tuple[str, ...]],
    inner_states: dict[str, str],
) -> Scenario:
    """Compile a generated scenario and enforce Campaign continuity rules."""

    if raw.get("roles") or raw.get("cast") or raw.get("scripts"):
        raise ValueError("campaign-generated scenarios must leave roles, cast, and scripts empty")
    normalized = normalize_campaign_scenario_raw(raw, brief, bible)
    scenario = compile_scenario(normalized, registry)
    actors = set(scenario.world.character_ids)
    expected_actors = set(brief.cast_ids)
    if actors != expected_actors:
        raise ValueError(
            "scenario character IDs must exactly match act_brief.cast_ids; "
            f"extra={sorted(actors - expected_actors)}, missing={sorted(expected_actors - actors)}. "
            "Only cast_ids may use kind=character; a required_entity_id outside cast_ids "
            "(including an AI, spirit, will, or projection) must be an Item, Room, or Passage instead"
        )
    if protagonist_id not in actors:
        raise ValueError("every act must include the protagonist")
    if brief.act_number == 1 and len(actors) > 3:
        raise ValueError("the first act may contain at most three characters")
    if scenario.routing.strategy != "interaction":
        raise ValueError("campaign acts must use the updated interaction router")
    dark_rooms = [room.id for room in scenario.world.entities.values() if isinstance(room, Room) and room.light < 0.8]
    if dark_rooms:
        raise ValueError(
            "campaign room light must be at least 0.8 so characters can reliably identify speakers; "
            f"dark rooms: {sorted(dark_rooms)}"
        )
    objects = set(scenario.world.entities) | set(scenario.world.passages)
    missing = set(brief.required_entity_ids) - objects
    if missing:
        raise ValueError(f"required campaign entities are missing: {sorted(missing)}")
    if len(scenario.end_when) == 1:
        ending = scenario.end_when[0]
        if ending.kind == "open" and ending.value and ending.subject_id in scenario.world.passages:
            raise ValueError(
                f"opening Passage {ending.subject_id!r} cannot be the only Campaign end_when; "
                "the act would finish immediately after open, before the actor can cross it. "
                "Use an inside predicate for the destination Room or a flag set after the downstream objective"
            )

    duplicate_boundaries: list[str] = []
    physical_world = scenario.create_world()
    for item_id, item in scenario.world.entities.items():
        if not isinstance(item, Item) or not (item.openable or item.lockable):
            continue
        item_room = physical_world.room_of(item_id)
        item_name = item.name.strip().casefold()
        for passage_id, passage in scenario.world.passages.items():
            if (
                item_room in passage.rooms
                and item_name == passage.name.strip().casefold()
                and (passage.openable or passage.lockable)
            ):
                duplicate_boundaries.append(f"{item_id}/{passage_id}")
    if duplicate_boundaries:
        raise ValueError(
            "a physical doorway must be represented only by its Passage; remove duplicate Item/Passage pairs: "
            + ", ".join(sorted(duplicate_boundaries))
        )

    referenced = set(brief.required_entity_ids)
    for predicate in (*scenario.end_when, *scenario.expected):
        referenced.add(predicate.subject_id)
        if predicate.object_id:
            referenced.add(predicate.object_id)
    for rule in scenario.world.mechanics:
        referenced.add(rule.source_id)
        if rule.subject_id:
            referenced.add(rule.subject_id)
        for predicate in rule.when:
            referenced.add(predicate.subject_id)
            if predicate.object_id:
                referenced.add(predicate.object_id)
        for effect in rule.effects:
            referenced.add(effect.subject_id)

    missing_scan: list[str] = []
    missing_inspect: list[str] = []
    for item_id, obj in scenario.world.entities.items():
        if not isinstance(obj, Item):
            continue
        scan = obj.perception_for("scan").description.strip()
        if not scan:
            missing_scan.append(item_id)
        interactive = obj.portable or any((obj.container, obj.openable, obj.lockable, obj.slot, obj.operable))
        inspect = obj.perception_for("inspect").description.strip()
        if (interactive or item_id in referenced) and (not inspect or inspect == scan):
            missing_inspect.append(item_id)
    if missing_scan:
        raise ValueError(f"campaign Items require a scan appearance: {sorted(missing_scan)}")
    if missing_inspect:
        raise ValueError(
            "interactive or story-relevant campaign Items require a distinct inspect description: "
            f"{sorted(missing_inspect)}"
        )

    all_objects: dict[str, Entity] = {**scenario.world.entities, **scenario.world.passages}
    for object_id, canonical_obj in all_objects.items():
        previous = entity_canon.get(object_id)
        kind = getattr(canonical_obj, "kind", "passage")
        if previous and (previous.kind != kind or previous.name != canonical_obj.name):
            raise ValueError(f"stable entity {object_id} changed kind or name")

    roles = {}
    for actor_id in scenario.world.character_ids:
        canon = bible.character(actor_id) if bible else None
        inner = inner_states.get(actor_id, "")
        roles[actor_id] = RoleBrief(
            personality=canon.personality if canon else "",
            private_goal=inner or (canon.inner_life if canon else ""),
            memories=memories.get(actor_id, ()),
            known_entity_ids=(),
        )
    return scenario.model_copy(update={"roles": roles})
