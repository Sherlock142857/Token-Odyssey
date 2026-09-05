from typing import Literal

import pytest

from token_odyssey.kernel.actions.base import Action, EffectPlan, Intent
from token_odyssey.kernel.actions.registry import ActionRegistry
from token_odyssey.kernel.events import EventDraft
from token_odyssey.kernel.harness import WorldExecutionError, WorldHarness
from token_odyssey.kernel.state import Placement, change_to
from token_odyssey.scenario import compile_scenario


def executor(scenario, registry):
    harness = WorldHarness(scenario.create_world(), registry)
    known = frozenset((*scenario.world.entities, *scenario.world.passages))

    def act(kind, actor="alice", **params):
        return harness.execute(actor, registry.parse_intent({"kind": kind, **params}), known_ids=known)
    return harness, act


def take_gem(act):
    assert act("unlock", lockable_id="box", key_item_id="key").accepted
    assert act("open", openable_id="box").accepted
    assert act("take", item_id="gem").accepted


def test_give_uses_one_recipient_and_rejects_cross_room_without_changes(scenario, registry):
    harness, act = executor(scenario, registry)
    before = harness.world.state
    result = act("give", item_id="key", recipient_id="eve")
    assert result.issues[0].code == "NOT_COLOCATED"
    assert harness.world.state == before and not harness.world_log
    result = act("give", item_id="key", recipient_id="bob")
    assert result.accepted
    assert harness.world.state.placements["key"] == Placement(parent_id="bob", relation="attached")
    with pytest.raises(ValueError):
        registry.parse_intent({"kind": "give", "item_id": "key", "target_ids": ["bob"]})


def test_unlock_and_open_are_distinct_and_wrong_key_is_private_rejection(scenario, registry):
    harness, act = executor(scenario, registry)
    assert act("open", openable_id="box").issues[0].code == "LOCKED"
    assert act("unlock", lockable_id="box", key_item_id="wrong_key").issues[0].code == "WRONG_KEY"
    assert not harness.world_log
    assert act("unlock", lockable_id="box", key_item_id="key").accepted
    assert not harness.world.state.openings["box"]
    assert act("open", openable_id="box").accepted
    assert act("lock", lockable_id="box", key_item_id="key").issues[0].code == "CLOSE_BEFORE_LOCK"
    assert act("close", openable_id="box").accepted
    assert act("lock", lockable_id="box", key_item_id="key").accepted


def test_search_cannot_open_a_box_and_operate_does_not_become_search(scenario, registry):
    harness, act = executor(scenario, registry)
    assert act("search", container_id="box").issues[0].code == "CONTAINER_CLOSED"
    assert act("operate", device_id="box").issues[0].code == "NOT_OPERABLE"
    assert not harness.world_log
    result = act("operate", device_id="socket")
    assert result.accepted and len(result.transaction.events) == 1
    assert not harness.world.state.flags["powered"]


def test_take_ancestor_rejects_as_poss_instead_of_crashing_transaction(scenario_data, registry):
    scenario_data["initial_state"]["placements"]["alice"] = {"parent_id": "box"}
    harness, act = executor(compile_scenario(scenario_data), registry)
    assert act("take", item_id="box").issues[0].code == "PLACEMENT_CYCLE"
    assert not harness.world_log


def test_installation_is_separate_from_attachment_and_take_disconnects(scenario_data, registry):
    scenario_data["world"]["mechanics"] = [{
        "id": "power", "trigger": "operated", "subject_id": "socket", "source_id": "socket",
        "when": [{"kind": "installed", "subject_id": "gem", "object_id": "socket"}],
        "effects": [{"kind": "flag", "subject_id": "powered", "value": True}],
    }]
    harness, act = executor(compile_scenario(scenario_data), registry)
    take_gem(act)
    act("place", item_id="gem", destination_id="socket", relation="attached")
    act("operate", device_id="socket")
    assert not harness.world.state.flags["powered"]
    assert not harness.world.state.connections
    act("take", item_id="gem")
    act("install", item_id="gem", slot_id="socket")
    assert harness.world.state.connections == {"gem": "socket"}
    act("operate", device_id="socket")
    assert harness.world.state.flags["powered"]
    act("take", item_id="gem")
    assert not harness.world.state.connections


def chain_rules():
    return [
        {"id": "seat", "trigger": "placement_changed", "subject_id": "gem", "source_id": "table",
         "when": [{"kind": "attached", "subject_id": "gem", "object_id": "table"}],
         "effects": [{"kind": "flag", "subject_id": "powered", "value": True}]},
        {"id": "release", "trigger": "state_changed", "subject_id": "powered", "source_id": "gate",
         "when": [{"kind": "flag", "subject_id": "powered"}],
         "effects": [{"kind": "open", "subject_id": "gate", "value": True}]},
    ]


def test_reaction_chain_uses_updated_state_and_one_transaction(scenario_data, registry):
    scenario_data["world"]["mechanics"] = chain_rules()
    harness, act = executor(compile_scenario(scenario_data), registry)
    take_gem(act)
    result = act("place", item_id="gem", destination_id="table", relation="attached")
    assert [e.kind for e in result.transaction.events] == ["place", "mechanism", "mechanism"]
    root, first, second = result.transaction.events
    assert first.caused_by == root.sequence and second.caused_by == first.sequence
    assert len({e.transaction_id for e in result.transaction.events}) == 1
    assert harness.world.state.openings["gate"]
    assert result.transaction.after_revision == result.transaction.before_revision + 1
    act("take", item_id="gem")
    repeated = act("place", item_id="gem", destination_id="table", relation="attached")
    assert len(repeated.transaction.events) == 1  # Once-only conditions do not refire.


def test_invalid_reaction_rolls_back_current_action_not_successful_history(scenario_data, registry):
    scenario_data["world"]["mechanics"] = chain_rules()
    scenario_data["initial_state"]["locks"]["gate"] = True
    harness, act = executor(compile_scenario(scenario_data), registry)
    take_gem(act)
    before, log = harness.world.state, harness.world_log
    with pytest.raises(WorldExecutionError, match="locked object cannot be open"):
        act("place", item_id="gem", destination_id="table", relation="attached")
    assert harness.world.state == before and harness.world_log == log
    assert harness.world.state.placements["gem"].parent_id == "alice"


def test_reaction_cycle_is_bounded_and_never_partially_commits(scenario_data, registry):
    scenario_data["world"]["max_reactions_per_action"] = 4
    scenario_data["world"]["mechanics"] = [
        {"id": "start", "trigger": "operated", "source_id": "socket",
         "effects": [{"kind": "flag", "subject_id": "powered", "value": True}]},
        {"id": "off", "trigger": "state_changed", "source_id": "socket", "once": False,
         "when": [{"kind": "flag", "subject_id": "powered"}],
         "effects": [{"kind": "flag", "subject_id": "powered", "value": False}]},
        {"id": "on", "trigger": "state_changed", "source_id": "socket", "once": False,
         "when": [{"kind": "flag", "subject_id": "powered", "value": False}],
         "effects": [{"kind": "flag", "subject_id": "powered", "value": True}]},
    ]
    harness, act = executor(compile_scenario(scenario_data), registry)
    with pytest.raises(WorldExecutionError, match="reaction limit"):
        act("operate", device_id="socket")
    assert not harness.world_log and not harness.world.state.flags["powered"]


def test_custom_action_extends_registry_without_harness_branches(scenario, registry):
    class IlluminateIntent(Intent):
        kind: Literal["illuminate"] = "illuminate"

    class Illuminate(Action[IlluminateIntent]):
        kind, intent_type = "illuminate", IlluminateIntent

        def check(self, context, intent): pass

        def effects(self, context, intent):
            return EffectPlan(EventDraft(kind=self.kind, actor_id=context.actor_id,
                                         changes=(change_to(context.world.state, "flags", "powered", True),)))

    custom = ActionRegistry([*(registry.get(k) for k in registry.kinds), Illuminate()])
    harness, act = executor(scenario, custom)
    assert act("illuminate").accepted
    assert harness.world.state.flags["powered"]
    snapshot = harness.world
    snapshot.state.flags["powered"] = False
    assert harness.world.state.flags["powered"]
