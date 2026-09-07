import pytest

from token_odyssey.kernel.events import ActionResult, Cue, EventFrame, EvidenceAnchor, Fact, Transaction, WorldEvent
from token_odyssey.kernel.harness import WorldHarness
from token_odyssey.kernel.state import Placement
from token_odyssey.perception.system import ObservationSystem
from token_odyssey.scenario import compile_scenario
from token_odyssey.translators.language import render_observation


def observer(world, *, roll=0.0):
    system = ObservationSystem(world.definition.character_ids, seed=3)
    system.rng.random = lambda: roll
    return system


def test_scan_is_same_room_even_when_remote_visibility_is_perfect(scenario_data):
    scenario_data["world"]["passages"]["gate"]["openable"]["open_visibility"] = 1
    scenario_data["initial_state"]["openings"] = {"gate": True}
    world = compile_scenario(scenario_data).create_world()
    system = observer(world)
    visible = {v.id for v in system.scan(world, "alice")}
    assert "secret" not in visible and "eve" not in visible
    assert "secret" not in system.known_ids("alice")
    assert "bob" in visible


def test_direct_inventory_is_known_without_revealing_nested_contents(scenario_data):
    scenario_data["world"]["entities"]["alice"]["concealed_visibility"] = 0
    scenario_data["world"]["entities"]["key"]["visibility"] = 0
    scenario_data["initial_state"]["placements"]["key"]["relation"] = "inside"
    scenario_data["initial_state"]["placements"]["box"] = {"parent_id": "alice", "relation": "attached"}
    world = compile_scenario(scenario_data).create_world()
    system = observer(world, roll=0.99)
    view = system.view(world, "alice", max_actions=5, continue_after_move=False)
    assert {e.id for e in view.inventory} >= {"key", "box"}
    assert "gem" not in {e.id for e in view.inventory + view.items}


def test_weak_tracking_survives_missed_scan_but_not_moved_ancestor(scenario_data):
    scenario_data["world"]["entities"]["bead"]["visibility"] = 0.2
    world = compile_scenario(scenario_data).create_world()
    system = observer(world)
    assert "bead" in {e.id for e in system.scan(world, "alice")}
    remembered = system.memories["alice"].known["bead"].location_signature
    system.rng.random = lambda: 0.99
    kept = {e.id: e for e in system.scan(world, "alice")}
    assert kept["bead"].basis == "continuity"
    world.state.placements["glass"] = Placement(parent_id="table", relation="attached")
    assert "bead" not in {e.id for e in system.scan(world, "alice")}
    assert system.memories["alice"].known["bead"].location_signature == remembered
    assert all(not obs.facts for obs in system.log)  # No invented "it moved" assertion.


def test_closing_opaque_container_removes_contents_from_current_view_not_memory(scenario_data):
    scenario_data["initial_state"]["locks"]["box"] = False
    scenario_data["initial_state"]["openings"] = {"box": True}
    world = compile_scenario(scenario_data).create_world()
    system = observer(world)
    system.scan(world, "alice")
    world.state.openings["box"] = False
    assert "gem" not in {e.id for e in system.scan(world, "alice")}
    assert "gem" in system.known_ids("alice")
    assert system.memories["alice"].known["gem"].view.placement.parent_id == "box"


def test_departure_does_not_reveal_unseen_destination(scenario_data, registry):
    scenario_data["initial_state"]["openings"] = {"gate": True}
    world = compile_scenario(scenario_data).create_world()
    harness = WorldHarness(world, registry)
    system = observer(world)
    result = harness.execute("alice", registry.parse_intent({"kind": "move", "destination_room_id": "b"}),
                             known_ids=frozenset((*world.definition.entities, *world.definition.passages)))
    system.project(result)
    departure = [o for o in system.log if o.observer_id == "bob"]
    arrival = [o for o in system.log if o.observer_id == "eve"]
    assert any(f.kind == "departure" for o in departure for f in o.facts)
    assert not any(f.kind == "arrival" for o in departure for f in o.facts)
    assert not any("b" in f.fields.values() for o in departure for f in o.facts)
    assert all(e.placement is None for o in departure for e in o.entities)
    assert any(f.kind == "arrival" for o in arrival for f in o.facts)


def test_cross_room_sound_does_not_identify_mechanism_source(scenario_data, registry):
    scenario_data["world"]["mechanics"] = [{
        "id": "ring", "trigger": "operated", "subject_id": "socket", "source_id": "socket",
        "sound_description": "墙后传来一阵铃声。", "visual_description": "隐藏灯光亮起。",
    }]
    world = compile_scenario(scenario_data).create_world()
    harness, system = WorldHarness(world, registry), observer(world)
    result = harness.execute("alice", registry.parse_intent({"kind": "operate", "device_id": "socket"}),
                             known_ids=frozenset(world.definition.entities))
    system.project(result)
    facts = [f for o in system.log if o.observer_id == "eve" for f in o.facts]
    assert any(f.kind == "mechanism_heard" for f in facts)
    assert not any(f.kind == "mechanism_seen" for f in facts)
    assert "socket" not in system.known_ids("eve")
    assert all("socket" not in f.fields.values() for f in facts)


def projection_result(world, cues):
    event = WorldEvent(kind="test_signal", actor_id="alice", sequence=1, transaction_id=1, cues=tuple(cues))
    transaction = Transaction(id=1, actor_id="alice", action_kind="test_signal", before_revision=0, after_revision=1, events=(event,))
    return ActionResult(True, transaction=transaction, frames=(EventFrame(event, world, world),))


def test_action_owned_numeric_thresholds_disclose_facts_independently(scenario):
    world = scenario.create_world()
    system = observer(world, roll=0.3)  # score=1, quality=0.7
    cues = [Cue(fact=Fact(kind=kind), anchor_id="alice", threshold=threshold)
            for kind, threshold in (("movement", 0.1), ("exchange", 0.5), ("item_identity", 0.9))]
    system.project(projection_result(world, cues))
    assert [f.kind for o in system.log if o.observer_id == "bob" for f in o.facts] == ["movement", "exchange"]


def test_visible_anchor_does_not_authorize_hidden_named_participant(scenario):
    world = scenario.create_world()
    system = observer(world)
    cue = Cue(fact=Fact(kind="exchange", fields={"recipient_id": "eve"}), anchor_id="alice",
              requires=(EvidenceAnchor(object_id="eve"),), identifies=("eve",))
    system.project(projection_result(world, [cue]))
    assert not [o for o in system.log if o.observer_id == "bob"]
    assert "eve" not in system.known_ids("bob")


def test_addressed_speech_is_received_even_if_bystander_sampling_misses(scenario, registry):
    world = scenario.create_world()
    harness, system = WorldHarness(world, registry), observer(world, roll=0.99)
    result = harness.execute("alice", registry.parse_intent({"kind": "say", "listener_ids": ["bob"],
                             "content": "请接手钥匙", "amplitude": "subtle"}),
                             known_ids=frozenset(world.definition.entities))
    system.project(result)
    received = [f for o in system.log if o.observer_id == "bob" for f in o.facts]
    assert any(f.kind == "speech" and f.fields.get("actor_id") == "alice" for f in received)
    assert not [o for o in system.log if o.observer_id == "eve"]


def projected_facts(system, actor):
    return tuple(f for o in system.log if o.observer_id == actor for f in o.facts)


def test_cross_room_visible_movement_names_real_rooms_and_merges_endpoints(scenario_data, registry):
    scenario_data["world"]["passages"]["gate"]["openable"]["open_visibility"] = 1
    scenario_data["initial_state"]["openings"] = {"gate": True}
    world = compile_scenario(scenario_data).create_world()
    result = WorldHarness(world, registry).execute(
        "alice", registry.parse_intent({"kind": "move", "destination_room_id": "b"}),
        known_ids=frozenset(world.definition.entities))
    system = observer(world)
    system.project(result)
    for actor in ("bob", "eve"):
        facts = projected_facts(system, actor)
        assert facts == (Fact(kind="move", fields={
            "actor_id": "alice", "from_room_id": "a", "destination_room_id": "b"}),)
        text, = render_observation(facts, {"alice": "Alice", "a": "Office", "b": "Workshop"})
        assert text == "Alice从Office走到了Workshop。"
    assert [f.kind for f in projected_facts(system, "alice")] == ["travel_result"]


@pytest.mark.parametrize("raw", [
    {"kind": "take", "item_id": "bead"},
    {"kind": "give", "item_id": "key", "recipient_id": "eve"},
    {"kind": "unlock", "lockable_id": "box", "key_item_id": "key"},
    {"kind": "open", "openable_id": "gate"},
])
def test_ordinary_clear_room_actions_are_complete_without_receipt_duplicates(scenario_data, registry, raw):
    scenario_data["initial_state"]["openings"] = {"glass": True}
    scenario_data["initial_state"]["placements"]["eve"] = {"parent_id": "a"}
    world = compile_scenario(scenario_data).create_world()
    result = WorldHarness(world, registry).execute("alice", registry.parse_intent(raw),
        known_ids=frozenset((*world.definition.entities, *world.definition.passages)))
    assert result.accepted and result.transaction
    for roll in (0.0, 0.3, 0.65, 0.9, 0.999):
        system = observer(world, roll=roll)
        system.project(result)
        for actor in ("alice", "bob"):
            facts = projected_facts(system, actor)
            assert len(facts) == 1 and facts[0].kind == raw["kind"]
            assert facts[0].fields["actor_id"] == "alice"


@pytest.mark.parametrize("amplitude", ["normal", "overt"])
def test_broadcast_speech_combines_authorized_hearing_and_speaker_sight(scenario, registry, amplitude):
    world = scenario.create_world()
    result = WorldHarness(world, registry).execute("alice", registry.parse_intent({
        "kind": "say", "content": "The key is ready.", "amplitude": amplitude}), known_ids=frozenset())
    system = observer(world, roll=0.65)
    system.project(result)
    for actor in ("alice", "bob"):
        assert projected_facts(system, actor) == (Fact(kind="speech", fields={
            "actor_id": "alice", "content": "The key is ready."}),)
    # An opaque wall still prevents attribution, even when speech carries.
    assert projected_facts(system, "eve") == (Fact(kind="speech", fields={"content": "The key is ready."}),)
    assert "alice" not in system.known_ids("eve")


def test_hearing_in_darkness_does_not_invent_speaker_identity(scenario_data, registry):
    scenario_data["world"]["entities"]["a"]["light"] = 0
    world = compile_scenario(scenario_data).create_world()
    result = WorldHarness(world, registry).execute("alice", registry.parse_intent({
        "kind": "say", "content": "Hello."}), known_ids=frozenset())
    system = observer(world, roll=0.99)
    system.project(result)
    assert projected_facts(system, "bob") == (Fact(kind="speech", fields={"content": "Hello."}),)
    assert "alice" not in system.known_ids("bob")


@pytest.mark.parametrize("raw,roll", [
    ({"kind": "hide", "item_id": "key"}, 0.3),
    ({"kind": "give", "item_id": "key", "recipient_id": "eve", "amplitude": "subtle"}, 0.15),
])
def test_stealth_actions_keep_partial_observations_in_the_same_room(scenario_data, registry, raw, roll):
    scenario_data["initial_state"]["placements"]["eve"] = {"parent_id": "a"}
    world = compile_scenario(scenario_data).create_world()
    result = WorldHarness(world, registry).execute("alice", registry.parse_intent(raw),
        known_ids=frozenset(world.definition.entities))
    system = observer(world, roll=roll)
    system.project(result)
    assert projected_facts(system, "bob") == (Fact(kind="handling"),)
    assert "key" not in system.known_ids("bob")
    assert projected_facts(system, "alice")[0].kind == raw["kind"]


def test_clear_room_mode_never_bypasses_a_hidden_required_anchor(scenario, registry):
    world = scenario.create_world()
    cue = registry.get("take").cue(registry.parse_intent({"kind": "take", "item_id": "gem"}),
        "take", "alice", {"actor_id": "alice", "item_id": "gem"}, identifies=("gem",))
    system = observer(world)
    system.project(projection_result(world, (cue,)))
    assert not projected_facts(system, "bob")
    assert "gem" not in system.known_ids("bob")


def test_mechanism_sight_and_sound_form_one_complementary_description():
    facts = (Fact(kind="mechanism_seen", fields={"description": "水位退去。"}),
             Fact(kind="mechanism_heard", fields={"description": "管道传来流水声。"}))
    assert render_observation(facts, {}) == ("水位退去。 管道传来流水声。",)
