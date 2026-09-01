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


def test_unknown_duplicate_and_unrelated_action_fields_are_rejected(registry):
    with pytest.raises(RegistryError, match="unknown"):
        registry.parse_command({"kind": "invented"})
    with pytest.raises(RegistryError, match="duplicate"):
        ActionRegistry([registry.spec("wait"), registry.spec("wait")])
    with pytest.raises(ValueError, match="extra_forbidden"):
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
        {"commands": [{"kind": "move", "destination_room_id": "drawing_room"}]},
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


def test_turn_shape_limits_one_say_and_one_physical(registry):
    with pytest.raises(ValueError, match="每回合最多一个非 say"):
        registry.parse_plan({"frames": [
            {"commands": [{"kind": "wait"}]},
            {"commands": [{"kind": "move", "destination_room_id": "study"}]},
        ]})
