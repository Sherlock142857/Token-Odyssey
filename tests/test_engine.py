from __future__ import annotations

import pytest

from airpg.agents import AgentUnavailableError, ScriptedAgent
from airpg.agents import _LLMTurn
from airpg.context import ContextBuilder
from airpg.debug import MemoryDebugSink
from airpg.engine import GameEngine
from airpg.models import (
    AgentDecision,
    AgentRuntime,
    AgentSession,
    MoveActionIntent,
    TakeActionIntent,
    TurnIntent,
    TokenUsage,
    WaitActionIntent,
)
from airpg.recording import RunRecorder
from airpg.replay import replay_run


def wait(actor_id: str, thought: str = "等待") -> TurnIntent:
    return TurnIntent(
        actor_id=actor_id,
        private_thought=thought,
        action=WaitActionIntent(),
    )


def test_router_retries_invalid_intent_without_logging_it(scenario):
    invalid = TurnIntent(
        actor_id="shen_lan",
        private_thought="这条不会被保存",
        action=MoveActionIntent(destination_room_id="drawing_room"),
    )
    valid = TurnIntent(
        actor_id="shen_lan",
        private_thought="去书房确认文件",
        action=MoveActionIntent(destination_room_id="study"),
    )
    scripted = ScriptedAgent(
        {
            "shen_lan": [invalid, valid],
            "qiao_man": [wait("qiao_man")],
            "luo_wen": [wait("luo_wen")],
        }
    )
    debug = MemoryDebugSink()
    engine = GameEngine(
        scenario,
        {actor_id: scripted for actor_id in scenario.world.actors},
        seed=3,
        debug=debug,
    )

    result = engine.run(1)

    assert result.turns_completed == 3
    assert scenario.world.actors["shen_lan"].room_id == "study"
    assert engine.runtimes["shen_lan"].private_thoughts == [
        "这条不会被保存",
        "去书房确认文件",
    ]
    assert len(engine.sessions["shen_lan"].messages) == 5
    assert engine.sessions["shen_lan"].messages[2].role.value == "assistant"
    assert engine.sessions["shen_lan"].messages[3].role.value == "user"
    assert "World Harness 私有反馈" in engine.sessions["shen_lan"].messages[3].content
    assert any(record["category"] == "validation_rejected" for record in debug.records)
    assert len(engine.harness.world_log) == 3


def test_unknown_item_id_is_rejected_by_observation_domain(scenario):
    guessed = TurnIntent(
        actor_id="shen_lan",
        action=TakeActionIntent(target_item_id="brass_key"),
    )
    scripted = ScriptedAgent(
        {
            "shen_lan": [guessed, wait("shen_lan", "不再猜测")],
            "qiao_man": [wait("qiao_man")],
            "luo_wen": [wait("luo_wen")],
        }
    )
    engine = GameEngine(
        scenario,
        {actor_id: scripted for actor_id in scenario.world.actors},
        seed=3,
    )

    engine.run(1)

    key = scenario.world.items["brass_key"]
    assert key.location.target_id == "ebony_box"
    assert engine.runtimes["shen_lan"].private_thoughts == ["不再猜测"]


def test_context_does_not_leak_other_actor_private_state(scenario):
    runtime = AgentRuntime(actor_id="shen_lan")
    session = AgentSession(actor_id="shen_lan")
    qiao_secret = scenario.world.actors["qiao_man"].private_goal
    builder = ContextBuilder(scenario)
    builder.begin_turn(session, runtime, 1)
    context = builder.append_assistant(
        session,
        runtime,
        '{"private_thought":"只有沈岚自己知道的念头","action":{"kind":"wait"},"dialogue":null}',
        round_number=1,
    )

    full_context = "\n".join(message.content for message in context.conversation)
    assert "只有沈岚自己知道的念头" in full_context
    assert qiao_secret not in full_context


def test_observation_listener_is_player_ui_boundary(scenario):
    scripted = ScriptedAgent(
        {actor_id: [wait(actor_id)] for actor_id in scenario.world.actors}
    )
    engine = GameEngine(
        scenario,
        {actor_id: scripted for actor_id in scenario.world.actors},
        seed=1,
    )
    received = []
    engine.add_observation_listener(
        lambda observation: received.append(observation)
        if observation.observer_id == "shen_lan"
        else None
    )

    engine.run(1)

    assert received
    assert all(observation.observer_id == "shen_lan" for observation in received)


def test_provider_failure_aborts_instead_of_waiting_for_fifty_rounds(scenario):
    class OfflineAgent:
        def decide(self, context):
            raise AgentUnavailableError("认证失败")

    engine = GameEngine(
        scenario,
        {actor_id: OfflineAgent() for actor_id in scenario.world.actors},
    )

    with pytest.raises(RuntimeError, match="认证失败"):
        engine.run(50)

    assert engine.harness.world_log == []


def test_complete_run_can_be_replayed_without_llm(scenario, tmp_path):
    scripted = ScriptedAgent(
        {
            actor_id: [wait(actor_id, f"{actor_id} 的连续想法") for _ in range(2)]
            for actor_id in scenario.world.actors
        }
    )
    recorder = RunRecorder(
        scenario,
        seed=11,
        provider="scripted",
        root=tmp_path,
        run_id="replay-source",
    )
    engine = GameEngine(
        scenario,
        {actor_id: scripted for actor_id in scenario.world.actors},
        seed=11,
        recorder=recorder,
    )
    engine.run(2)

    report = replay_run(recorder.run_dir)

    assert report.success
    assert report.event_count == 6
    assert (recorder.run_dir / "transcript.md").exists()
    assert (recorder.run_dir / "agents" / "shen_lan.jsonl").exists()


def test_agent_sessions_are_append_only_exact_prefixes(scenario):
    class CapturingAgent:
        def __init__(self, actor_id):
            self.actor_id = actor_id
            self.requests = []

        def decide(self, context):
            self.requests.append(context.messages())
            intent = wait(context.actor_id, f"第 {len(self.requests)} 次想法")
            raw = intent.model_dump_json(exclude={"actor_id"})
            return AgentDecision(actor_id=context.actor_id, raw_content=raw, intent=intent)

    agents = {actor_id: CapturingAgent(actor_id) for actor_id in scenario.world.actors}
    engine = GameEngine(scenario, agents, seed=5)
    engine.run(3)

    for actor_id, agent in agents.items():
        assert [len(request) for request in agent.requests] == [2, 4, 6]
        assert agent.requests[0] == agent.requests[1][:2]
        assert agent.requests[1] == agent.requests[2][:4]
        assert len(engine.sessions[actor_id].messages) == 7
        assert len(engine.runtimes[actor_id].private_thoughts) == 3


def test_token_cache_usage_is_aggregated_per_actor(scenario, tmp_path):
    class UsageAgent:
        def decide(self, context):
            intent = wait(context.actor_id)
            return AgentDecision(
                actor_id=context.actor_id,
                raw_content=intent.model_dump_json(exclude={"actor_id"}),
                intent=intent,
                usage=TokenUsage(
                    prompt_tokens=100,
                    prompt_cache_hit_tokens=80,
                    prompt_cache_miss_tokens=20,
                    completion_tokens=10,
                    total_tokens=110,
                ),
            )

    recorder = RunRecorder(
        scenario,
        seed=4,
        provider="usage-test",
        root=tmp_path,
        run_id="usage",
    )
    engine = GameEngine(
        scenario,
        {actor_id: UsageAgent() for actor_id in scenario.world.actors},
        seed=4,
        recorder=recorder,
    )
    engine.run(2)

    import json

    usage = json.loads((recorder.run_dir / "token_usage.json").read_text(encoding="utf-8"))
    assert usage["total"]["prompt_tokens"] == 600
    assert usage["total"]["prompt_cache_hit_tokens"] == 480
    assert usage["cache_hit_ratio"] == pytest.approx(0.8)


def test_llm_turn_parser_builds_discriminated_action_schema():
    parsed = _LLMTurn.model_validate(
        {
            "private_thought": "检查盒子",
            "action": {
                "kind": "search",
                "mode": "normal",
                "target_item_id": "ebony_box",
            },
            "dialogue": None,
        }
    )

    assert parsed.action.kind.value == "search"
    assert parsed.action.target_item_id == "ebony_box"


def test_fifty_rounds_keep_every_message_observation_and_thought(scenario):
    scripted = ScriptedAgent({actor_id: [] for actor_id in scenario.world.actors})
    engine = GameEngine(
        scenario,
        {actor_id: scripted for actor_id in scenario.world.actors},
        seed=17,
    )

    engine.run(50)

    for actor_id in scenario.world.actors:
        # One system message plus one user and one assistant message for every turn.
        assert len(engine.sessions[actor_id].messages) == 101
        assert 40 < engine.sessions[actor_id].observation_cursor <= len(
            engine.runtimes[actor_id].observations
        )
        assert len(engine.runtimes[actor_id].observations) > 40
        assert len(engine.runtimes[actor_id].private_thoughts) == 50
