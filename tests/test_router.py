from collections import Counter

import pytest

from conftest import MemoryRecorder, ROOT
from token_odyssey.kernel.events import Fact, WorldEvent
from token_odyssey.perception.models import Observation
from token_odyssey.runtime.composition import build_scripted_participants
from token_odyssey.runtime.router import FACT_WEIGHT, InteractionWeightedRouter
from token_odyssey.runtime.routing_policy import RoutingPolicy
from token_odyssey.runtime.runner import ActRunner
from token_odyssey.scenario import compile_scenario, load_scenario

ACTORS = ("alice", "bob", "eve", "dan", "fox", "gray")


def stimulus(router, kind="give", *, number=1, observer="bob", author="alice", fields=None, data=None):
    fields = fields or {"actor_id": author, "item_id": "key", "recipient_id": observer}
    event = WorldEvent(kind=kind, actor_id=author, sequence=number, transaction_id=number, data=data or fields)
    observation = Observation(sequence=number, observer_id=observer, world_revision=number, source="event",
                              source_event_sequence=number, facts=(Fact(kind=kind, fields=fields),))
    router.observe((event,), (observation,))
    return event, observation


def test_receipt_is_stronger_than_witness_and_canonical_data_alone_is_inert():
    router = InteractionWeightedRouter(7)
    event, obs = stimulus(router)
    router.observe((event,), (obs,))  # duplicate delivery is idempotent
    assert router.pending == {"bob": 5}
    router.next_actor(ACTORS, (event,))
    assert router.last_decision["actors"]["bob"]["attention"] == 5
    assert router.last_decision["actors"]["eve"]["attention"] == 0
    unseen = InteractionWeightedRouter(7)
    unseen.next_actor(ACTORS, (event,))
    assert all(row["attention"] == 0 for row in unseen.last_decision["actors"].values())


def test_interest_requires_disclosed_object_and_does_not_use_hidden_event_data():
    policy = RoutingPolicy(interests={"bob": {"key": 2}})
    router = InteractionWeightedRouter(7, policy)
    stimulus(router, "handling", fields={"description": "something moved"}, data={"item_id": "key"})
    assert router.pending["bob"] == 0.25
    stimulus(router, "take", number=2, fields={"item_id": "key", "actor_id": "alice"})
    assert router.pending["bob"] == 3


def test_turn_spam_uses_maximum_and_cannot_exceed_cap():
    router = InteractionWeightedRouter(1, RoutingPolicy(interests={"bob": {"key": 2}}))
    for number in range(1, 51):
        stimulus(router, number=number)
    router.next_actor(ACTORS, ())
    row = router.last_decision["actors"]["bob"]
    assert row["impulse"] == 5 and row["attention"] == 5
    assert len(row["reasons"]) == 1
    stimulus(router, number=51)
    router.next_actor(ACTORS, ())
    assert router.last_decision["actors"]["bob"]["attention"] <= 6


@pytest.mark.parametrize("kind,fields,data,expected", [
    ("speech", {"actor_id": "alice", "content": "hello"}, {"listener_ids": ["bob"]}, 4),
    ("speech", {"content": "hello"}, {"listener_ids": ["bob"]}, 0.8),
    ("speech", {"actor_id": "alice", "content": "hello"}, {"listener_ids": []}, 0.8),
    ("show", {"actor_id": "alice", "item_id": "key"}, {"observer_ids": ["bob"]}, 4),
])
def test_direct_speech_and_show_require_authorized_receipt(kind, fields, data, expected):
    router = InteractionWeightedRouter(1)
    event = WorldEvent(kind="say" if kind == "speech" else kind, actor_id="alice", sequence=1, transaction_id=1, data=data)
    observation = Observation(sequence=1, observer_id="bob", world_revision=1, source="event",
                              source_event_sequence=1, facts=(Fact(kind=kind, fields=fields),))
    router.observe((event,), (observation,))
    assert router.pending["bob"] == expected


def test_every_world_interaction_has_a_perceived_priority():
    # Assert coverage of the public fact vocabulary, including split move cues.
    required = {"take", "give", "place", "hide", "show", "speech", "search", "discovery", "open", "close",
                "lock", "unlock", "install", "operate", "arrival", "departure", "mechanism_seen", "mechanism_heard"}
    assert required <= FACT_WEIGHT.keys()
    router = InteractionWeightedRouter(1)
    stimulus(router, "wait", fields={"actor_id": "alice"})
    assert not router.pending
    stimulus(router, "discovery", number=2, observer="alice", fields={"entity_id": "key"})
    assert router.pending["alice"] == 1.5


def test_decay_idle_reactivation_and_no_immediate_repeat():
    router = InteractionWeightedRouter(1)
    router.rng.random = lambda: 0
    assert router.next_actor(ACTORS, ()) == "alice"
    assert router.next_actor(ACTORS, ()) == "bob"
    assert router.idle["alice"] == 1
    stimulus(router, "take", observer="alice", fields={"item_id": "key"})  # own action ignored
    assert not router.pending
    stimulus(router, "discovery", number=2, observer="alice", fields={"entity_id": "key"})
    router.next_actor(ACTORS, ())
    assert router.idle["alice"] == 0
    assert router.attention["alice"] == 0  # served attention consumed
    router.attention["eve"] = 4
    router.next_actor(ACTORS, ())
    assert router.last_decision["actors"]["eve"]["attention"] == pytest.approx(2.6)


def test_hot_pair_cannot_starve_other_characters_and_seed_reproduces():
    def trace(seed):
        router = InteractionWeightedRouter(seed)
        last = {actor: 0 for actor in ACTORS}
        result = []
        for turn in range(1, 601):
            stimulus(router, number=turn, observer="bob" if turn % 2 else "alice",
                     author="alice" if turn % 2 else "bob")
            actor = router.next_actor(ACTORS, ())
            assert not result or actor != result[-1]
            last[actor] = turn
            assert max(turn - value for value in last.values()) <= 3 * len(ACTORS)
            result.append(actor)
        return result
    assert trace(19) == trace(19)
    assert trace(20) != trace(19)


def test_direct_interaction_materially_changes_selection_frequency():
    selected = Counter()
    for seed in range(1200):
        router = InteractionWeightedRouter(seed)
        router.last_actor = "alice"
        stimulus(router)
        selected[router.next_actor(ACTORS, ())] += 1
    # Recipient weight 6; four bystanders weight 1: about 60%, vs 20% without stimulus.
    assert 0.54 < selected["bob"] / 1200 < 0.66
    assert selected["alice"] == 0


def test_solo_empty_and_changed_cast():
    router = InteractionWeightedRouter(1)
    assert [router.next_actor(("alice",), ()) for _ in range(3)] == ["alice"] * 3
    assert router.next_actor(("bob",), ()) == "bob"
    assert set(router.age) == {"bob"}
    with pytest.raises(ValueError):
        router.next_actor((), ())
    with pytest.raises(ValueError):
        router.next_actor(("bob", "bob"), ())


@pytest.mark.parametrize("interests", [{"nobody": {"key": 1}}, {"alice": {"missing": 1}}, {"alice": {"key": 3}}])
def test_scenario_rejects_invalid_routing_references_and_weights(scenario_data, interests):
    scenario_data["routing"] = {"interests": interests}
    with pytest.raises(ValueError):
        compile_scenario(scenario_data)


def test_runner_routes_committed_projection_without_extra_perception(scenario_data, registry):
    scenario_data["scripts"] = {"alice": [{"actions": [{"kind": "give", "item_id": "key", "recipient_id": "bob"}]}]}
    scenario = compile_scenario(scenario_data)
    router = InteractionWeightedRouter(1)
    router.rng.random = lambda: 0
    recorder = MemoryRecorder()
    runner = ActRunner(scenario, build_scripted_participants(scenario, registry), registry, router=router, recorder=recorder)
    runner.step()
    runner.step()
    route = recorder.records["routing"][1]
    assert route["actors"]["bob"]["impulse"] == 5
    assert route["actors"]["eve"]["impulse"] == 0
    assert route["actors"]["bob"]["reasons"][0]["event_sequence"] == 1


def test_floodgate_has_five_npcs_two_rooms_and_completes_under_varied_routing(registry):
    scenario = load_scenario(ROOT / "scenarios/floodgate_dispatch.yaml")
    assert len(scenario.world.character_ids) == 6 and len(scenario.world.room_ids) == 2
    assert set(scenario.routing.interests) == set(scenario.world.character_ids)
    for seed in (1, 7, 19, 41, 99):
        runner = ActRunner(scenario, build_scripted_participants(scenario, registry), registry, seed=seed)
        assert runner.run().goals_met, seed


def test_unidentified_mechanism_sound_does_not_match_private_source_interest():
    router = InteractionWeightedRouter(1, RoutingPolicy(interests={"eve": {"socket": 2}}))
    event = WorldEvent(kind="mechanism", source="world", mechanic_id="hidden_rule", sequence=1,
                       transaction_id=1, data={"source_id": "socket"}, subject_ids=("secret_flag",))
    heard = Observation(sequence=1, observer_id="eve", world_revision=1, source="event", source_event_sequence=1,
                        facts=(Fact(kind="mechanism_heard", fields={"description": "响了一声"}),))
    router.observe((event,), (heard,))
    assert router.pending == {"eve": 1.2}
    assert router.reasons["eve"][0]["interest"] == 0
