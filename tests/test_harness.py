from __future__ import annotations

import pytest

from airpg.harness import IntentRejected, WorldHarness
from airpg.models import (
    DialogueIntent,
    GiveActionIntent,
    HideActionIntent,
    LocationKind,
    MoveActionIntent,
    PlaceActionIntent,
    SearchActionIntent,
    ShowActionIntent,
    TakeActionIntent,
    TurnIntent,
    WaitActionIntent,
)


def test_invalid_cross_room_dialogue_is_atomic(scenario):
    harness = WorldHarness(scenario.world)
    intent = TurnIntent(
        actor_id="shen_lan",
        action=MoveActionIntent(destination_room_id="study"),
        dialogue=DialogueIntent(target_actor_ids=["qiao_man"], content="跟我来。"),
    )
    scenario.world.actors["qiao_man"].room_id = "foyer"

    with pytest.raises(IntentRejected, match="不在同一房间"):
        harness.execute(intent, 1)

    assert scenario.world.actors["shen_lan"].room_id == "drawing_room"
    assert harness.world_log == []


def test_dialogue_then_move_have_ordered_events(scenario):
    harness = WorldHarness(scenario.world)
    events = harness.execute(
        TurnIntent(
            actor_id="shen_lan",
            dialogue=DialogueIntent(target_actor_ids=["qiao_man"], content="我去书房。"),
            action=MoveActionIntent(destination_room_id="study"),
        ),
        1,
    )

    assert [event.sequence for event in events] == [1, 2]
    assert events[0].kind.value == "dialogue"
    assert events[0].data["source_room_id"] == "drawing_room"
    assert scenario.world.actors["shen_lan"].room_id == "study"


def test_take_give_hide_and_capacity_validation(scenario):
    harness = WorldHarness(scenario.world)
    fits = TurnIntent(
        actor_id="qiao_man",
        action=PlaceActionIntent(
            target_item_id="silver_ring",
            container_id="ebony_box",
        ),
    )
    # The key already uses one unit; ring fits in the remaining two.
    assert harness.validate(fits) == []

    harness.execute(
        TurnIntent(
            actor_id="luo_wen",
            action=TakeActionIntent(target_item_id="ebony_box"),
        ),
        1,
    )
    assert scenario.world.items["ebony_box"].location.kind == LocationKind.HELD

    too_large = TurnIntent(
        actor_id="luo_wen",
        action=HideActionIntent(target_item_id="ebony_box"),
    )
    assert "超过角色藏匿容量" not in "；".join(harness.validate(too_large))

    scenario.world.actors["luo_wen"].hide_capacity = 2
    assert "超过角色藏匿容量" in "；".join(harness.validate(too_large))


def test_item_held_by_another_actor_is_not_accessible(scenario):
    harness = WorldHarness(scenario.world)
    intent = TurnIntent(
        actor_id="shen_lan",
        action=TakeActionIntent(target_item_id="silver_ring"),
    )

    assert "无法接触" in "；".join(harness.validate(intent))


def test_search_give_show_and_wait_handlers(scenario):
    harness = WorldHarness(scenario.world)
    search = harness.execute(
        TurnIntent(
            actor_id="shen_lan",
            action=SearchActionIntent(target_item_id="ebony_box"),
        ),
        1,
    )[0]
    assert search.action_kind.value == "search"

    give = harness.execute(
        TurnIntent(
            actor_id="qiao_man",
            action=GiveActionIntent(
                target_item_id="silver_ring",
                recipient_id="shen_lan",
            ),
        ),
        1,
    )[0]
    assert scenario.world.items["silver_ring"].location.target_id == "shen_lan"
    assert give.direct_observer_ids == ["shen_lan"]

    show = harness.execute(
        TurnIntent(
            actor_id="shen_lan",
            action=ShowActionIntent(
                target_item_id="silver_ring",
                audience_ids=["luo_wen"],
            ),
        ),
        1,
    )[0]
    assert show.direct_observer_ids == ["luo_wen"]

    wait_event = harness.execute(
        TurnIntent(actor_id="luo_wen", action=WaitActionIntent()),
        1,
    )[0]
    assert wait_event.action_kind.value == "wait"


def test_action_specific_schema_rejects_unrelated_fields():
    with pytest.raises(ValueError, match="extra_forbidden"):
        TurnIntent.model_validate(
            {
                "actor_id": "someone",
                "action": {
                    "kind": "wait",
                    "mode": "normal",
                    "target_item_id": "invented",
                },
            }
        )


def test_place_handler_reports_capacity(scenario):
    scenario.world.items["ebony_box"].container_capacity = 1
    harness = WorldHarness(scenario.world)
    intent = TurnIntent(
        actor_id="qiao_man",
        action=PlaceActionIntent(
            target_item_id="silver_ring",
            container_id="ebony_box",
        ),
    )

    assert "容量不足" in "；".join(harness.validate(intent))
