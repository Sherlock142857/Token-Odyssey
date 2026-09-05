import pytest

from token_odyssey.kernel.fluents import Fluents
from token_odyssey.kernel.state import Placement
from token_odyssey.scenario import compile_scenario


def test_glass_is_visible_but_contents_are_unreachable(scenario):
    world = scenario.create_world()
    f = Fluents(world)
    assert f.transmission("alice", "bead") == 1
    assert not f.accessible("alice", "bead")
    assert f.transmission("alice", "gem") == 0
    assert not f.accessible("alice", "gem")


def test_closed_owned_container_still_blocks_access(scenario):
    world = scenario.create_world()
    world.state.placements["box"] = Placement(parent_id="alice", relation="attached")
    f = Fluents(world)
    assert f.controller("gem") == "alice"
    assert not f.accessible("alice", "gem")


def test_shared_container_wall_does_not_separate_its_inhabitants(scenario_data):
    scenario_data["initial_state"]["placements"]["alice"] = {"parent_id": "box"}
    scenario_data["initial_state"]["placements"]["bob"] = {"parent_id": "box"}
    world = compile_scenario(scenario_data).create_world()
    f = Fluents(world)
    assert f.same_room("alice", "bob")
    assert f.accessible("alice", "gem")
    assert f.transmission("alice", "bob") == 1
    assert not f.accessible("alice", "gate")


def test_passage_access_from_both_endpoints_is_not_double_placement(scenario):
    world = scenario.create_world()
    assert "gate" not in world.state.placements
    f = Fluents(world)
    assert f.accessible("alice", "gate") and f.accessible("eve", "gate")
    assert not f.can_traverse("alice", "gate", "b")
    world.state.openings["gate"] = True
    assert f.can_traverse("alice", "gate", "b")
    assert f.can_traverse("eve", "gate", "a")
    assert f.transmission("alice", "eve") == 0  # Walkability != visibility.


@pytest.mark.parametrize("corruption", [
    "missing_placement", "unknown_parent", "cycle", "non_container", "open_locked",
    "bad_key", "bad_passage", "unknown_effect", "duplicate_effect", "bad_connection", "wrong_flag_type",
])
def test_compiler_rejects_invalid_worlds(scenario_data, corruption):
    d = scenario_data
    if corruption == "missing_placement": del d["initial_state"]["placements"]["gem"]
    elif corruption == "unknown_parent": d["initial_state"]["placements"]["gem"]["parent_id"] = "missing"
    elif corruption == "cycle":
        d["initial_state"]["placements"]["box"] = {"parent_id": "gem", "relation": "attached"}
    elif corruption == "non_container": d["initial_state"]["placements"]["gem"]["parent_id"] = "table"
    elif corruption == "open_locked": d["initial_state"]["openings"] = {"box": True}
    elif corruption == "bad_key": d["world"]["entities"]["box"]["lockable"]["key_item_ids"] = ["alice"]
    elif corruption == "bad_passage": d["world"]["passages"]["gate"]["rooms"] = ["a", "a"]
    elif corruption in {"unknown_effect", "duplicate_effect"}:
        effect = {"kind": "flag", "subject_id": "missing" if corruption == "unknown_effect" else "powered", "value": True}
        d["world"]["mechanics"] = [{"id": "bad", "trigger": "operated", "source_id": "socket",
                                    "effects": [effect, effect] if corruption == "duplicate_effect" else [effect]}]
    elif corruption == "bad_connection": d["initial_state"]["connections"] = {"gem": "socket"}
    elif corruption == "wrong_flag_type": d["initial_state"]["flags"] = {"powered": "not-a-boolean"}
    with pytest.raises(ValueError):
        compile_scenario(d)


def test_capability_defaults_are_compiled_not_hidden_in_actions(scenario):
    assert scenario.initial_state.openings == {"box": False, "glass": False, "gate": False}
    assert scenario.initial_state.locks == {"box": True, "gate": False}
    assert scenario.initial_state.flags == {"powered": False, "released": False}


def test_legacy_schema_is_explicitly_rejected(scenario_data):
    scenario_data["schema_version"] = 2
    with pytest.raises(ValueError, match="schema_version must be 3"):
        compile_scenario(scenario_data)


def test_window_can_transmit_sight_without_allowing_travel(scenario_data):
    passage = scenario_data["world"]["passages"]["gate"]
    passage.update(forward_travel=False, reverse_travel=False)
    passage["openable"]["closed_visibility"] = 1
    f = Fluents(compile_scenario(scenario_data).create_world())
    assert f.transmission("alice", "eve") == 1
    assert not f.can_traverse("alice", "gate", "b")
    assert not f.can_traverse("eve", "gate", "a")


def test_duplicate_yaml_keys_are_not_silently_overwritten(tmp_path):
    from token_odyssey.scenario import load_scenario
    path = tmp_path / "bad.yaml"
    path.write_text("schema_version: 3\nid: first\nid: second\n")
    with pytest.raises(ValueError, match="duplicate YAML key"):
        load_scenario(path)


@pytest.mark.parametrize("bad_world", [[], {"entities": []}, {"entities": {"a": 42}}])
def test_compiler_reports_malformed_mapping_structure(scenario_data, bad_world):
    scenario_data["world"] = bad_world
    with pytest.raises(ValueError, match="mapping"):
        compile_scenario(scenario_data)
