from __future__ import annotations

from typing import Literal

import pytest

from token_odyssey.inside_act.actions.contracts import ActionEffect, ActionSpec, BaseActionIntent
from token_odyssey.inside_act.actions.registry import ActionRegistry, RegistryError
from token_odyssey.inside_act.domain.events import ActionEventData, AcceptedTurn, RejectedTurn
from token_odyssey.inside_act.harness import WorldHarness


def plan(registry, frames, thought=""):
    return registry.parse_plan({"private_thought": thought, "frames": frames})


def test_builtin_registry_is_complete_and_prompt_is_generated(registry):
    assert registry.kinds == ("say", "move", "search", "take", "give", "place", "show", "hide", "wait")
    assert "target_entity_id" in registry.prompt_catalog()
    assert "destination_room_id" in registry.prompt_catalog()
    assert "判定：" in registry.prompt_catalog()
    assert "效果：" in registry.prompt_catalog()


def test_unknown_duplicate_and_unrelated_action_fields_are_rejected(registry):
    with pytest.raises(RegistryError, match="未知 action"):
        registry.parse_command({"kind": "invented"})
    with pytest.raises(RegistryError, match="duplicate"):
        ActionRegistry([registry.spec("wait"), registry.spec("wait")])
    with pytest.raises(ValueError, match="不接受的字段"):
        registry.parse_command({"kind": "wait", "target_entity_id": "invented"})


def test_one_registered_test_action_parses_executes_and_renders(scenario):
    class PingIntent(BaseActionIntent):
        kind: Literal["ping"] = "ping"
        message: str

    class PingEventData(ActionEventData):
        message: str

    spec = ActionSpec(
        kind="ping", intent_model=PingIntent, validate=lambda _c, _i: [],
        event_model=PingEventData,
        plan=lambda _c, i: ActionEffect(data=PingEventData(message=i.message)),
        known_reference_extractor=lambda _i: set(), intrinsic_visibility=0.5,
        render_full=lambda _s, e: f"ping:{e.data.message}",
        render_partial=lambda _s, _e: "ping", prompt_usage="test action",
    )
    registry = ActionRegistry([spec])
    harness = WorldHarness(scenario.world, registry)
    result = harness.resolve("shen_lan", registry.parse_plan({"frames": [{"commands": [{"kind": "ping", "message": "ok"}]}]}), 1)
    assert isinstance(result, AcceptedTurn)
    assert registry.render(result.final_state, result.events[0], full=True) == "ping:ok"


def test_later_invalid_frame_rejects_entire_plan(scenario, registry):
    harness = WorldHarness(scenario.world, registry)
    result = harness.resolve("shen_lan", plan(registry, [
        {"commands": [{"kind": "say", "target_character_ids": ["qiao_man"], "content": "你好"}]},
        {"commands": [{"kind": "move", "destination_room_id": "missing_room"}]},
    ]), 1)
    assert isinstance(result, RejectedTurn)
    assert harness.state.root_room_of("shen_lan") == "drawing_room"
    assert harness.world_log == []


def test_simultaneous_say_and_move_use_pre_frame_state(scenario, registry):
    harness = WorldHarness(scenario.world, registry)
    result = harness.resolve("shen_lan", plan(registry, [{"commands": [
        {"kind": "say", "target_character_ids": ["qiao_man"], "content": "我去书房"},
        {"kind": "move", "destination_room_id": "study"},
    ]}]), 1)
    assert isinstance(result, AcceptedTurn)
    assert [event.frame_index for event in result.events] == [0, 0]
    assert harness.state.root_room_of("shen_lan") == "study"


def test_move_then_say_validates_against_draft_state(scenario, registry):
    raw = scenario.world.model_dump(mode="python")
    raw["placements"]["qiao_man"] = {"relation": "inside", "parent_id": "study"}
    from token_odyssey.inside_act.domain.spatial import WorldState
    harness = WorldHarness(WorldState.model_validate(raw), registry)
    result = harness.resolve("shen_lan", plan(registry, [
        {"commands": [{"kind": "move", "destination_room_id": "study"}]},
        {"commands": [{"kind": "say", "target_character_ids": ["qiao_man"], "content": "到了"}]},
    ]), 1)
    assert isinstance(result, AcceptedTurn)
    assert [event.frame_index for event in result.events] == [0, 1]


def test_take_give_hide_search_show_and_wait_behaviors(scenario, registry):
    harness = WorldHarness(scenario.world, registry)
    take = harness.resolve("shen_lan", plan(registry, [{"commands": [{"kind": "take", "target_entity_id": "ebony_box"}]}]), 1)
    assert isinstance(take, AcceptedTurn)
    show = harness.resolve("shen_lan", plan(registry, [{"commands": [{"kind": "show", "target_entity_id": "ebony_box", "audience_ids": ["qiao_man"]}]}]), 1)
    assert isinstance(show, AcceptedTurn)
    give = harness.resolve("shen_lan", plan(registry, [{"commands": [{"kind": "give", "target_entity_id": "ebony_box", "recipient_id": "qiao_man"}]}]), 1)
    assert isinstance(give, AcceptedTurn)
    search = harness.resolve("qiao_man", plan(registry, [{"commands": [{"kind": "search", "target_entity_id": "ebony_box"}]}]), 1)
    assert isinstance(search, AcceptedTurn)
    hide = harness.resolve("qiao_man", plan(registry, [{"commands": [{"kind": "hide", "target_entity_id": "ebony_box"}]}]), 1)
    assert isinstance(hide, AcceptedTurn)
    wait = harness.resolve("luo_wen", plan(registry, [{"commands": [{"kind": "wait"}]}]), 1)
    assert isinstance(wait, AcceptedTurn)


def test_take_can_extract_a_nested_item_from_a_controlled_container(scenario, registry):
    raw = scenario.world.model_dump(mode="python")
    raw["placements"]["ebony_box"] = {"relation": "attached", "parent_id": "shen_lan"}
    from token_odyssey.inside_act.domain.spatial import WorldState
    harness = WorldHarness(WorldState.model_validate(raw), registry)
    result = harness.resolve(
        "shen_lan",
        plan(registry, [{"commands": [{"kind": "take", "target_entity_id": "brass_key"}]}]),
        1,
    )
    assert isinstance(result, AcceptedTurn)
    assert harness.state.placements["brass_key"].parent_id == "shen_lan"
    assert harness.state.placements["brass_key"].relation.value == "attached"


def test_turn_allows_five_mixed_actions_and_rejects_the_sixth(scenario, registry):
    harness = WorldHarness(scenario.world, registry)
    result = harness.resolve("shen_lan", plan(registry, [
        {"commands": [{"kind": "take", "target_entity_id": "ebony_box"}]},
        {"commands": [
            {"kind": "show", "target_entity_id": "ebony_box", "audience_ids": ["qiao_man"]},
            {"kind": "say", "target_character_ids": ["qiao_man"], "content": "请看。"},
        ]},
        {"commands": [{"kind": "hide", "target_entity_id": "ebony_box"}]},
        {"commands": [{"kind": "wait"}]},
    ]), 1)
    assert isinstance(result, AcceptedTurn)
    assert len(result.events) == 5

    with pytest.raises(RegistryError, match="计划共有 6 个 action"):
        registry.parse_plan({"frames": [
            {"commands": [{"kind": "wait"}, {"kind": "wait"}]},
            {"commands": [{"kind": "wait"}]},
            {"commands": [{"kind": "wait"}]},
            {"commands": [{"kind": "wait"}]},
            {"commands": [{"kind": "wait"}]},
        ]})


def test_multiple_say_actions_are_allowed(scenario, registry):
    harness = WorldHarness(scenario.world, registry)
    result = harness.resolve("shen_lan", plan(registry, [{"commands": [
        {"kind": "say", "target_character_ids": ["qiao_man"], "content": "一"},
        {"kind": "say", "target_character_ids": ["luo_wen"], "content": "二"},
    ]}]), 1)
    assert isinstance(result, AcceptedTurn)


def test_search_knowledge_is_available_only_to_later_frames(scenario, registry):
    sequential = WorldHarness(scenario.world, registry)
    accepted = sequential.resolve(
        "shen_lan",
        plan(registry, [
            {"commands": [{"kind": "search", "target_entity_id": "ebony_box"}]},
            {"commands": [{"kind": "take", "target_entity_id": "brass_key"}]},
        ]),
        1,
        known_entity_ids={"ebony_box"},
    )
    assert isinstance(accepted, AcceptedTurn)
    assert sequential.state.placements["brass_key"].parent_id == "shen_lan"

    simultaneous = WorldHarness(scenario.world, registry)
    rejected = simultaneous.resolve(
        "shen_lan",
        plan(registry, [{"commands": [
            {"kind": "search", "target_entity_id": "ebony_box"},
            {"kind": "take", "target_entity_id": "brass_key"},
        ]}]),
        1,
        known_entity_ids={"ebony_box"},
    )
    assert isinstance(rejected, RejectedTurn)
    assert rejected.validation_issues[0].code == "unknown_to_actor"
    assert "后续 frame" in rejected.validation_issues[0].message


def test_later_invalid_frame_discards_temporary_knowledge_and_state(scenario, registry):
    harness = WorldHarness(scenario.world, registry)
    result = harness.resolve(
        "shen_lan",
        plan(registry, [
            {"commands": [{"kind": "search", "target_entity_id": "ebony_box"}]},
            {"commands": [{"kind": "take", "target_entity_id": "brass_key"}]},
            {"commands": [{"kind": "move", "destination_room_id": "missing_room"}]},
        ]),
        1,
        known_entity_ids={"ebony_box"},
    )
    assert isinstance(result, RejectedTurn)
    assert harness.state.placements["brass_key"].parent_id == "ebony_box"
    assert harness.world_log == []


def test_same_frame_mutation_conflict_is_actionable_and_atomic(scenario, registry):
    harness = WorldHarness(scenario.world, registry)
    result = harness.resolve("shen_lan", plan(registry, [{"commands": [
        {"kind": "take", "target_entity_id": "ebony_box"},
        {"kind": "take", "target_entity_id": "ebony_box"},
    ]}]), 1)
    assert isinstance(result, RejectedTurn)
    assert result.validation_issues[0].code == "simultaneous_conflict"
    assert "不同 frame" in result.validation_issues[0].message
    assert harness.state.placements["ebony_box"].parent_id == "drawing_room"
    assert harness.world_log == []


def test_hide_then_take_reveals_item_and_duplicate_public_take_is_silent(scenario, registry):
    harness = WorldHarness(scenario.world, registry)
    revealed = harness.resolve("qiao_man", plan(registry, [
        {"commands": [{"kind": "hide", "target_entity_id": "silver_ring"}]},
        {"commands": [{"kind": "take", "target_entity_id": "silver_ring"}]},
    ]), 1)
    assert isinstance(revealed, AcceptedTurn)
    placement = harness.state.placements["silver_ring"]
    assert placement.parent_id == "qiao_man"
    assert placement.relation.value == "attached"

    duplicate = harness.resolve(
        "qiao_man",
        plan(registry, [{"commands": [{"kind": "take", "target_entity_id": "silver_ring"}]}]),
        2,
    )
    assert isinstance(duplicate, AcceptedTurn)
    assert duplicate.events == ()
    assert len(harness.world_log) == 2


def test_move_to_current_room_is_silent(scenario, registry):
    harness = WorldHarness(scenario.world, registry)
    result = harness.resolve(
        "shen_lan",
        plan(registry, [{"commands": [{"kind": "move", "destination_room_id": "drawing_room"}]}]),
        1,
    )
    assert isinstance(result, AcceptedTurn)
    assert result.events == ()
    assert result.observation_directives == ()
    assert harness.world_log == []


def test_place_requires_relation_and_supports_attached_and_inside(scenario, registry):
    with pytest.raises(RegistryError, match="relation"):
        registry.parse_plan({"frames": [{"commands": [{
            "kind": "place", "target_entity_id": "ebony_box", "container_id": "drawing_room"
        }]}]})

    harness = WorldHarness(scenario.world, registry)
    taken = harness.resolve(
        "shen_lan",
        plan(registry, [{"commands": [{"kind": "take", "target_entity_id": "ebony_box"}]}]),
        1,
    )
    assert isinstance(taken, AcceptedTurn)
    attached = harness.resolve(
        "shen_lan",
        plan(registry, [{"commands": [{
            "kind": "place", "target_entity_id": "ebony_box",
            "container_id": "drawing_room", "relation": "attached",
        }]}]),
        2,
    )
    assert isinstance(attached, AcceptedTurn)
    assert harness.state.placements["ebony_box"].relation.value == "attached"
    assert harness.state.controller_of("ebony_box") is None
    assert "放在了" in registry.render(attached.final_state, attached.events[0], full=True)

    inside = harness.resolve(
        "qiao_man",
        plan(registry, [{"commands": [{
            "kind": "place", "target_entity_id": "silver_ring",
            "container_id": "ebony_box", "relation": "inside",
        }]}]),
        3,
    )
    assert isinstance(inside, AcceptedTurn)
    assert harness.state.placements["silver_ring"].relation.value == "inside"
    assert "放进了" in registry.render(inside.final_state, inside.events[0], full=True)

    character_target = WorldHarness(scenario.world, registry).resolve(
        "qiao_man",
        plan(registry, [{"commands": [{
            "kind": "place", "target_entity_id": "silver_ring",
            "container_id": "shen_lan", "relation": "attached",
        }]}]),
        1,
    )
    assert isinstance(character_target, RejectedTurn)
    assert "give" in character_target.validation_issues[0].message


def test_show_supports_public_items_but_not_other_character_control(scenario, registry):
    harness = WorldHarness(scenario.world, registry)
    before = harness.state.placements["ebony_box"]
    public = harness.resolve("shen_lan", plan(registry, [{"commands": [{
        "kind": "show", "target_entity_id": "ebony_box", "audience_ids": ["qiao_man"]
    }]}]), 1)
    assert isinstance(public, AcceptedTurn)
    assert harness.state.placements["ebony_box"] == before

    controlled = harness.resolve("shen_lan", plan(registry, [{"commands": [{
        "kind": "show", "target_entity_id": "silver_ring", "audience_ids": ["luo_wen"]
    }]}]), 2)
    assert isinstance(controlled, RejectedTurn)
    assert "其他角色控制" in controlled.validation_issues[0].message


def test_harness_snapshots_cannot_mutate_canonical_state(scenario, registry):
    harness = WorldHarness(scenario.world, registry)
    result = harness.resolve(
        "shen_lan",
        plan(registry, [{"commands": [{"kind": "take", "target_entity_id": "ebony_box"}]}]),
        1,
    )
    assert isinstance(result, AcceptedTurn)
    result.final_state.placements["ebony_box"].parent_id = "study"
    result.committed_frames[-1].after_state.placements["ebony_box"].parent_id = "foyer"
    exposed = harness.state
    exposed.placements["ebony_box"].parent_id = "drawing_room"
    assert harness.state.placements["ebony_box"].parent_id == "shen_lan"
    result.events[0].action_kind = "changed"
    exposed_log = harness.world_log
    exposed_log.clear()
    assert len(harness.world_log) == 1
    assert harness.world_log[0].action_kind == "take"


def test_registry_errors_are_concise_and_actionable(registry):
    six_frames = {"frames": [
        {"commands": [{"kind": "wait"}]} for _ in range(6)
    ]}
    with pytest.raises(RegistryError) as too_many:
        registry.parse_plan(six_frames)
    message = str(too_many.value)
    assert "最多允许 5 项" in message
    assert "input_value" not in message
    assert "errors.pydantic.dev" not in message

    with pytest.raises(RegistryError) as extra_field:
        registry.parse_plan({"frames": [{"commands": [{
            "kind": "take", "target_entity_id": "ebony_box", "container_id": "x"
        }]}]})
    message = str(extra_field.value)
    assert "frames[0].commands[0]" in message
    assert "container_id" in message
    assert "可用字段" in message
    assert "errors.pydantic.dev" not in message


def test_action_implementation_errors_propagate_without_mutating_world(scenario):
    class BrokenIntent(BaseActionIntent):
        kind: Literal["broken"] = "broken"

    def broken_validate(_context, _intent):
        raise RuntimeError("validator bug")

    spec = ActionSpec(
        kind="broken",
        intent_model=BrokenIntent,
        event_model=ActionEventData,
        validate=broken_validate,
        plan=lambda _context, _intent: ActionEffect(data=ActionEventData()),
        known_reference_extractor=lambda _intent: set(),
        intrinsic_visibility=0.0,
        render_full=lambda _state, _event: "broken",
        render_partial=lambda _state, _event: "broken",
        prompt_usage="broken",
    )
    registry = ActionRegistry([spec])
    harness = WorldHarness(scenario.world, registry)
    before = harness.state
    with pytest.raises(RuntimeError, match="validator bug"):
        harness.resolve(
            "shen_lan",
            registry.parse_plan({"frames": [{"commands": [{"kind": "broken"}]}]}),
            1,
        )
    assert harness.state == before
    assert harness.world_log == []
