from token_odyssey.kernel.events import ActionResult, Cue, EventFrame, EvidenceAnchor, Fact, Transaction, WorldEvent
from token_odyssey.kernel.harness import WorldHarness
from token_odyssey.kernel.state import Placement
from token_odyssey.perception.system import ObservationSystem
from token_odyssey.scenario import compile_scenario


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
