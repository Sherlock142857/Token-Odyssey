from __future__ import annotations

import pytest

from token_odyssey.inside_act.domain.entities import Character, Item, Room
from token_odyssey.inside_act.domain.spatial import Placement, RoomVisibilityGraph, WorldState
from token_odyssey.inside_act.visibility import VisibilityService


def test_scenario_compiles_to_unified_canonical_state(scenario):
    assert scenario.schema_version == 2
    assert len(scenario.world.entities) == 12
    assert scenario.world.character("shen_lan").container_visibility == 0.4
    assert scenario.world.room("drawing_room").is_container
    assert "shen_lan" in scenario.world.placements


def test_parent_chains_terminate_at_rooms_and_nested_controller_is_inherited(scenario):
    state = scenario.world
    assert state.root_room_of("brass_key") == "drawing_room"
    assert state.controller_of("brass_key") is None
    state.placements["ebony_box"] = Placement(relation="attached", parent_id="shen_lan")
    validated = WorldState.model_validate(state.model_dump(mode="python"))
    assert validated.controller_of("brass_key") == "shen_lan"


def test_inside_visibility_multiplies_every_edge_including_same_room():
    state = WorldState(
        entities={
            "dark": Room(id="dark", name="暗室", container_visibility=0.5),
            "a": Character(id="a", name="A", container_visibility=0.4, size_class=6, personality=""),
            "i": Item(id="i", name="I", is_container=False, container_visibility=1, size_class=1),
        },
        placements={
            "a": Placement(relation="inside", parent_id="dark"),
            "i": Placement(relation="inside", parent_id="dark"),
        },
    )
    assert VisibilityService().base_visibility(state, "a", "i") == pytest.approx(0.25)


def test_attached_edge_has_visibility_factor_one(scenario):
    assert scenario.world.edge_product_to_room("silver_ring") == pytest.approx(1.0)


def test_nested_item_visibility_uses_container_and_intrinsic_factors(scenario):
    state = scenario.world.model_copy(deep=True)
    state.placements["shen_lan"] = Placement(relation="inside", parent_id="study")
    state = WorldState.model_validate(state.model_dump(mode="python"))
    assert VisibilityService().item_visibility(state, "shen_lan", "sealed_will") == pytest.approx(0.405)


def test_room_graph_uses_directed_max_product_path():
    graph = RoomVisibilityGraph(edges={"a": {"b": 0.8, "c": 0.5}, "b": {"c": 0.9}, "c": {"a": 0.1}})
    assert graph.visibility("a", "c") == pytest.approx(0.72)
    assert graph.visibility("c", "b") == pytest.approx(0.08)
    assert graph.visibility("b", "a") == pytest.approx(0.09)
    assert graph.visibility("a", "a") == 1.0


def test_placement_cycle_is_rejected(scenario):
    raw = scenario.world.model_dump(mode="python")
    raw["placements"]["ebony_box"] = {"relation": "inside", "parent_id": "brass_key"}
    raw["entities"]["brass_key"]["is_container"] = True
    raw["entities"]["brass_key"]["size_class"] = 3
    with pytest.raises(ValueError, match="cycle"):
        WorldState.model_validate(raw)


def test_room_placement_and_inside_non_container_are_rejected(scenario):
    raw = scenario.world.model_dump(mode="python")
    raw["placements"]["drawing_room"] = {"relation": "inside", "parent_id": "study"}
    with pytest.raises(ValueError, match="Room"):
        WorldState.model_validate(raw)
    raw = scenario.world.model_dump(mode="python")
    raw["placements"]["ebony_box"] = {"relation": "inside", "parent_id": "guest_ledger"}
    with pytest.raises(ValueError, match="non-container"):
        WorldState.model_validate(raw)


def test_size_class_is_per_child_not_additive(scenario):
    raw = scenario.world.model_dump(mode="python")
    raw["placements"]["silver_ring"] = {"relation": "inside", "parent_id": "ebony_box"}
    state = WorldState.model_validate(raw)
    assert set(state.children_of("ebony_box")) == {"brass_key", "silver_ring"}


def test_oversized_child_and_unknown_protector_are_rejected(scenario):
    raw = scenario.world.model_dump(mode="python")
    raw["placements"]["document_case"] = {"relation": "inside", "parent_id": "ebony_box"}
    with pytest.raises(ValueError, match="exceeds"):
        WorldState.model_validate(raw)
    raw = scenario.world.model_dump(mode="python")
    raw["entities"]["ebony_box"]["protector_id"] = "nobody"
    with pytest.raises(ValueError, match="protector"):
        WorldState.model_validate(raw)
