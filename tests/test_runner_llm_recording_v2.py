from __future__ import annotations

import json

import pytest

from token_odyssey.agents import (
    AgentDecision,
    AgentUnavailableError,
    DecisionRequest,
    ScriptedAgent,
    ValidationFeedback,
)
from token_odyssey.agents.contracts import TokenUsage
from token_odyssey.agents.llm_agent import LLMAgent
from token_odyssey.inside_act.context import TurnContext
from token_odyssey.inside_act.domain.events import ValidationIssue
from token_odyssey.inside_act.router import ShuffledRoundRouter
from token_odyssey.inside_act.runner import ActRunner
from token_odyssey.interfaces.cli.composition import _identity
from token_odyssey.llm.contracts import LLMProfile, LLMResponse
from token_odyssey.llm.registry import LLMBackendRegistry, LLMProfileRegistry
from token_odyssey.recording import RunRecorder
from token_odyssey.recording.replay import replay_run


def test_runner_retries_unknown_observation_reference_without_world_event(scenario, registry, wait_plan):
    guessed = registry.parse_plan({"frames": [{"commands": [{"kind": "take", "target_entity_id": "brass_key"}]}]})
    agent = ScriptedAgent(
        {
            "shen_lan": [guessed, wait_plan("不再猜测")],
            "qiao_man": [wait_plan()],
            "luo_wen": [wait_plan()],
        },
        registry,
    )
    runner = ActRunner(
        scenario,
        {actor_id: agent for actor_id in scenario.world.character_ids},
        registry,
        ShuffledRoundRouter(3),
        seed=3,
    )
    runner.run(1)
    assert runner.state.placements["brass_key"].parent_id == "ebony_box"
    assert "不再猜测" in runner.runtimes["shen_lan"].private_thoughts
    assert len(runner.harness.world_log) == 3


class FakeBackend:
    def __init__(self, content=None, usage=None):
        self.content = content or '{"private_thought":"等待","frames":[{"commands":[{"kind":"wait"}]}]}'
        self.usage = usage or TokenUsage()
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return LLMResponse(content=self.content, usage=self.usage, model=request.profile.model)


class SequenceBackend(FakeBackend):
    def __init__(self, contents):
        super().__init__()
        self.contents = list(contents)

    def complete(self, request):
        self.requests.append(request)
        return LLMResponse(content=self.contents.pop(0), model=request.profile.model)


def build_llm_agents(scenario, registry, backend, mode="standard"):
    backends = LLMBackendRegistry({"fake": backend})
    profiles = LLMProfileRegistry({mode: LLMProfile(backend_id="fake", model="fake-model")})
    return {
        actor_id: LLMAgent(
            identity=_identity(scenario, actor_id), mode=mode, action_registry=registry,
            backend_registry=backends, profile_registry=profiles,
        )
        for actor_id in scenario.world.character_ids
    }


def test_llm_sessions_are_append_only_exact_request_prefixes(scenario, registry):
    backend = FakeBackend()
    agents = build_llm_agents(scenario, registry, backend)
    runner = ActRunner(scenario, agents, registry, ShuffledRoundRouter(5), seed=5)
    runner.run(3)
    for agent in agents.values():
        assert [len(request) for request in agent.requests] == [2, 4, 6]
        assert agent.requests[0] == agent.requests[1][:2]
        assert agent.requests[1] == agent.requests[2][:4]
        assert len(agent.messages) == 7


def test_invalid_llm_output_and_private_feedback_are_only_appended(scenario, registry):
    backend = SequenceBackend([
        "{}",
        '{"private_thought":"修正","frames":[{"commands":[{"kind":"wait"}]}]}',
    ])
    agent = build_llm_agents(scenario, registry, backend)["shen_lan"]
    context = TurnContext(actor_id="shen_lan", round_number=1, room_id="drawing_room", room_name="会客厅")
    first = agent.decide(DecisionRequest(actor_id="shen_lan", context=context))
    assert first.output_error
    second = agent.decide(
        DecisionRequest(
            actor_id="shen_lan",
            feedback=ValidationFeedback(
                issues=[ValidationIssue(code="output", message=first.output_error)]
            ),
        )
    )
    assert second.plan is not None
    assert [message.role.value for message in agent.messages] == [
        "system", "user", "assistant", "user", "assistant"
    ]
    assert backend.requests[0].messages == backend.requests[1].messages[:2]


def test_llm_prompt_does_not_leak_other_character_private_goal(scenario, registry):
    backend = FakeBackend()
    agents = build_llm_agents(scenario, registry, backend)
    actor = agents["shen_lan"]
    context = TurnContext(actor_id="shen_lan", round_number=1, room_id="drawing_room", room_name="会客厅")
    actor.decide(DecisionRequest(actor_id="shen_lan", context=context))
    prompt = "\n".join(message.content for message in actor.messages)
    assert scenario.world.character("qiao_man").private_goal not in prompt
    assert scenario.world.character("shen_lan").private_goal in prompt


def test_named_modes_route_to_different_backends(scenario, registry):
    fast, deep = FakeBackend(), FakeBackend()
    backends = LLMBackendRegistry({"fast": fast, "deep": deep})
    profiles = LLMProfileRegistry({
        "fast": LLMProfile(backend_id="fast", model="fast-model"),
        "deep": LLMProfile(backend_id="deep", model="deep-model"),
    })
    context = TurnContext(actor_id="shen_lan", round_number=1, room_id="drawing_room", room_name="会客厅")
    for mode in ("fast", "deep"):
        LLMAgent(
            identity=_identity(scenario, "shen_lan"), mode=mode,
            action_registry=registry, backend_registry=backends, profile_registry=profiles,
        ).decide(DecisionRequest(actor_id="shen_lan", context=context))
    assert fast.requests[0].profile.model == "fast-model"
    assert deep.requests[0].profile.model == "deep-model"


def test_human_compatible_participant_receives_context_not_world_state(scenario, registry):
    class HumanPortDouble:
        def decide(self, request):
            assert isinstance(request, DecisionRequest)
            assert not hasattr(request, "world")
            plan = registry.parse_plan({"frames": [{"commands": [{"kind": "wait"}]}]})
            return AgentDecision(actor_id=request.actor_id, raw_content=plan.model_dump_json(), plan=plan)

    participant = HumanPortDouble()
    runner = ActRunner(
        scenario,
        {actor_id: participant for actor_id in scenario.world.character_ids},
        registry,
        ShuffledRoundRouter(1),
        seed=1,
    )
    assert runner.run(1).turns_completed == 3


def test_backend_failure_aborts_act(scenario, registry):
    class Offline:
        def decide(self, request):
            raise AgentUnavailableError("认证失败")

    runner = ActRunner(
        scenario,
        {actor_id: Offline() for actor_id in scenario.world.character_ids},
        registry,
        ShuffledRoundRouter(1),
        seed=1,
    )
    with pytest.raises(AgentUnavailableError, match="认证失败"):
        runner.run(50)
    assert runner.harness.world_log == []


def test_complete_run_records_tokens_and_replays(scenario, registry, wait_plan, tmp_path):
    usage = TokenUsage(prompt_tokens=100, prompt_cache_hit_tokens=80, prompt_cache_miss_tokens=20, completion_tokens=10, total_tokens=110)

    class UsageParticipant:
        def decide(self, request):
            plan = wait_plan(f"{request.actor_id} 等待")
            return AgentDecision(actor_id=request.actor_id, raw_content=plan.model_dump_json(), plan=plan, usage=usage)

    recorder = RunRecorder(
        scenario, seed=11, modes={actor: "test" for actor in scenario.world.character_ids},
        root=tmp_path, run_id="v2-replay",
    )
    runner = ActRunner(
        scenario,
        {actor_id: UsageParticipant() for actor_id in scenario.world.character_ids},
        registry,
        ShuffledRoundRouter(11),
        seed=11,
        recorder=recorder,
    )
    runner.run(2)
    usage_data = json.loads((recorder.run_dir / "token_usage.json").read_text(encoding="utf-8"))
    assert usage_data["total"]["prompt_tokens"] == 600
    assert usage_data["cache_hit_ratio"] == pytest.approx(0.8)

    # Keep a historically rejected plan rejected even though two waits are
    # legal under the current four-action policy.
    decisions_path = recorder.run_dir / "decisions.jsonl"
    rows = [json.loads(line) for line in decisions_path.read_text(encoding="utf-8").splitlines()]
    historical_plan = registry.parse_plan({"frames": [{"commands": [
        {"kind": "wait"}, {"kind": "wait"}
    ]}]})
    rows.insert(0, {
        "round_number": rows[0]["round_number"],
        "actor_id": rows[0]["actor_id"],
        "attempt": 1,
        "accepted": False,
        "reasons": ["历史策略拒绝了该计划"],
        "decision": AgentDecision(
            actor_id=rows[0]["actor_id"],
            raw_content=historical_plan.model_dump_json(),
            plan=historical_plan,
        ).model_dump(mode="json", serialize_as_any=True),
    })
    decisions_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    report = replay_run(recorder.run_dir)
    assert report.success
    assert report.event_count == 6


def test_prompt_flow_markdown_records_incremental_messages_and_rejections(
    scenario, registry, tmp_path
):
    class RejectOnceBackend(FakeBackend):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def complete(self, request):
            self.requests.append(request)
            self.calls += 1
            content = "{}" if self.calls <= 3 else self.content
            return LLMResponse(content=content, model=request.profile.model)

    backend = RejectOnceBackend()
    agents = build_llm_agents(scenario, registry, backend)
    recorder = RunRecorder(
        scenario,
        seed=23,
        modes={actor: "standard" for actor in scenario.world.character_ids},
        root=tmp_path,
        run_id="prompt-flow",
    )
    runner = ActRunner(
        scenario,
        agents,
        registry,
        ShuffledRoundRouter(23),
        seed=23,
        recorder=recorder,
    )
    runner.run(1)
    prompt_flow = (recorder.run_dir / "prompt_flow.md").read_text(encoding="utf-8")
    for actor_id in scenario.world.character_ids:
        assert f"(`{actor_id}`)" in prompt_flow
    assert prompt_flow.count("### System prompt") == len(scenario.world.character_ids)
    assert "#### Appended user prompt" in prompt_flow
    assert "#### Assistant response" in prompt_flow
    assert "· rejected" in prompt_flow
    assert "World Harness 私有反馈" in prompt_flow
    assert "TurnPlan.frames 缺少必填字段" in prompt_flow
    assert "#### Automatic fallback" in prompt_flow


def test_fifty_rounds_preserve_all_runtime_observations_and_thoughts(scenario, registry):
    agent = ScriptedAgent({actor: [] for actor in scenario.world.character_ids}, registry)
    runner = ActRunner(
        scenario,
        {actor_id: agent for actor_id in scenario.world.character_ids},
        registry,
        ShuffledRoundRouter(17),
        seed=17,
    )
    runner.run(50)
    for actor_id in scenario.world.character_ids:
        assert len(runner.runtimes[actor_id].private_thoughts) == 50
        assert len(runner.runtimes[actor_id].observations) >= 50


def test_fifty_round_llm_sessions_are_never_compressed_or_truncated(scenario, registry):
    backend = FakeBackend()
    agents = build_llm_agents(scenario, registry, backend)
    runner = ActRunner(scenario, agents, registry, ShuffledRoundRouter(29), seed=29)
    runner.run(50)
    for agent in agents.values():
        assert len(agent.requests) == 50
        assert len(agent.messages) == 101
        assert agent.requests[-1][:2] == agent.requests[0]
