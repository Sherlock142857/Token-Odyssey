from __future__ import annotations

from airpg.models import (
    AgentRuntime,
    DialogueIntent,
    ObservationLevel,
    PerformanceMode,
    TurnIntent,
)
from airpg.harness import WorldHarness
from airpg.harness.observation import ObservationSystem


class FixedRandom:
    def __init__(self, value: float) -> None:
        self.value = value

    def random(self) -> float:
        return self.value


def make_system(scenario, roll: float = 0.5):
    runtimes = {
        actor_id: AgentRuntime(actor_id=actor_id) for actor_id in scenario.world.actors
    }
    system = ObservationSystem(scenario.world, runtimes, seed=1)
    system.rng = FixedRandom(roll)
    return system, runtimes


def test_opaque_container_hides_key_until_search(scenario):
    system, runtimes = make_system(scenario, roll=0.1)
    system.scan_environment("shen_lan", 0)

    assert "ebony_box" in runtimes["shen_lan"].knowledge.items
    assert "brass_key" not in runtimes["shen_lan"].knowledge.items

    system.reveal_container("shen_lan", "ebony_box", 1)

    assert "brass_key" in runtimes["shen_lan"].knowledge.items


def test_direct_dialogue_target_always_gets_secret_message(scenario):
    system, runtimes = make_system(scenario, roll=0.99)
    harness = WorldHarness(scenario.world)
    event = harness.execute(
        TurnIntent(
            actor_id="shen_lan",
            dialogue=DialogueIntent(
                target_actor_ids=["qiao_man"],
                content="钥匙在盒子里。",
                mode=PerformanceMode.SECRETIVE,
            ),
        ),
        1,
    )[0]

    result = system.project_event(event)

    assert result["qiao_man"] is not None
    assert result["qiao_man"].level == ObservationLevel.FULL
    assert "钥匙在盒子里" in result["qiao_man"].text
    assert result["luo_wen"] is None


def test_partial_event_projection_redacts_details(scenario):
    scenario.world.actors["luo_wen"].room_id = "foyer"
    system, _ = make_system(scenario, roll=0.18)
    harness = WorldHarness(scenario.world)
    event = harness.execute(
        TurnIntent(
            actor_id="shen_lan",
            dialogue=DialogueIntent(
                target_actor_ids=["qiao_man"],
                content="不要让罗闻知道钥匙的事。",
                mode=PerformanceMode.SECRETIVE,
            ),
        ),
        1,
    )[0]

    result = system.project_event(event)

    assert result["luo_wen"] is not None
    assert result["luo_wen"].level == ObservationLevel.PARTIAL
    assert "钥匙" not in result["luo_wen"].text
