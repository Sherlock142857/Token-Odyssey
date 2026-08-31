from __future__ import annotations

import pytest

from airpg.models import Location, LocationKind, WorldState


def test_carried_item_effective_room_follows_actor(scenario):
    state = scenario.world
    assert state.effective_room_of_item("silver_ring") == "drawing_room"

    state.actors["qiao_man"].room_id = "study"

    assert state.effective_room_of_item("silver_ring") == "study"


def test_nested_exposure_multiplies_container_visibility(scenario):
    state = scenario.world
    assert state.item_exposure("brass_key") == 0.0
    assert state.item_exposure("sealed_will") == pytest.approx(0.405)


def test_containment_cycle_is_rejected(scenario):
    raw = scenario.world.model_dump(mode="json")
    raw["items"]["ebony_box"]["location"] = {
        "kind": "container",
        "target_id": "brass_key",
    }
    raw["items"]["brass_key"]["container_capacity"] = 5

    with pytest.raises(ValueError, match="containment cycle"):
        WorldState.model_validate(raw)


def test_location_is_a_single_relation(scenario):
    item = scenario.world.items["guest_ledger"]
    item.location = Location(kind=LocationKind.HELD, target_id="luo_wen")

    assert item.location.kind == LocationKind.HELD
    assert item.location.target_id == "luo_wen"

