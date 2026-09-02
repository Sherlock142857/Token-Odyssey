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
from token_odyssey.inside_act.context import LocationView, TurnContext
from token_odyssey.inside_act.domain.events import ValidationIssue
from token_odyssey.inside_act.router import ShuffledRoundRouter
from token_odyssey.inside_act.runner import ActRunner
from token_odyssey.interfaces.cli.composition import _identity
from token_odyssey.llm.contracts import LLMProfile, LLMResponse
from token_odyssey.llm.registry import LLMBackendRegistry, LLMProfileRegistry
from token_odyssey.recording import RunRecorder
from token_odyssey.recording.replay import replay_run


class FakeBackend:
    def __init__(self, content=None, usage=None):
        self.content = content or '{"private_thought":"等待","actions":[{"kind":"wait"}]}'
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
            identity=_identity(scenario, actor_id), mode=mode,
            action_registry=registry, backend_registry=backends,
            profile_registry=profiles,
        )
        for actor_id in scenario.world.character_ids
    }


def empty_context(actor_id="shen_lan"):
    return TurnContext(
        actor_id=actor_id,
        location=LocationView(id="drawing_room", name="会客厅"),
    )


def test_runner_retries_unknown_reference_without_world_event(scenario, registry, wait_plan):
    guessed = registry.parse_plan({"actions": [{"kind": "take", "target_id": "brass_key"}]})
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
    assert runner.harness.world_log == []


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


def test_invalid_llm_output_appends_json_feedback(scenario, registry):
    backend = SequenceBackend([
        "{}",
        '{"private_thought":"修正","actions":[{"kind":"wait"}]}',
    ])
    agent = build_llm_agents(scenario, registry, backend)["shen_lan"]
    first = agent.decide(DecisionRequest(actor_id="shen_lan", context=empty_context()))
    assert first.output_error
    second = agent.decide(DecisionRequest(
        actor_id="shen_lan",
        feedback=ValidationFeedback(
            issues=[ValidationIssue(code="output", message=first.output_error)]
        ),
    ))
    assert second.plan is not None
    feedback = json.loads(agent.messages[-2].content)
    assert feedback["action_rejected"] is True
    assert "actions" in feedback["errors"][0]["message"]


def test_system_prompt_uses_v3_examples_and_hides_internal_terms(scenario, registry):
    agent = build_llm_agents(scenario, registry, FakeBackend())["shen_lan"]
    agent.decide(DecisionRequest(actor_id="shen_lan", context=empty_context()))
    prompt = agent.messages[0].content
    assert '"actions"' in prompt
    assert '"target_id"' in prompt
    assert '"target_ids"' in prompt
    assert '"amplitude":"subtle"' in prompt
    assert '"amplitude":"normal"' not in prompt
    assert "target_entity_id" not in prompt
    assert "frames" not in prompt
    assert "commands" not in prompt
    assert "World Harness" not in prompt
    assert "World Log" not in prompt
    assert "【" not in prompt and "】" not in prompt


def test_llm_prompt_does_not_leak_other_character_private_goal(scenario, registry):
    agents = build_llm_agents(scenario, registry, FakeBackend())
    actor = agents["shen_lan"]
    actor.decide(DecisionRequest(actor_id="shen_lan", context=empty_context()))
    prompt = "\n".join(message.content for message in actor.messages)
    assert scenario.world.character("qiao_man").private_goal not in prompt
    assert scenario.world.character("shen_lan").private_goal in prompt


def test_human_participant_receives_structured_context(scenario, registry):
    class HumanPortDouble:
        def decide(self, request):
            assert isinstance(request.context, TurnContext)
            plan = registry.parse_plan({"actions": [{"kind": "wait"}]})
            return AgentDecision(
                actor_id=request.actor_id,
                raw_content=plan.model_dump_json(serialize_as_any=True),
                plan=plan,
            )

    participant = HumanPortDouble()
    runner = ActRunner(
        scenario,
        {actor_id: participant for actor_id in scenario.world.character_ids},
        registry,
        ShuffledRoundRouter(1),
        seed=1,
    )
    assert runner.run(1).turns_completed == 3


def test_runner_truncates_stale_interaction_after_move(scenario, registry):
    stale_plan = registry.parse_plan({"actions": [
        {"kind": "move", "target_id": "study"},
        {"kind": "take", "target_id": "ebony_box"},
    ]})
    agent = ScriptedAgent({"shen_lan": [stale_plan]}, registry)
    runner = ActRunner(
        scenario,
        {actor_id: agent for actor_id in scenario.world.character_ids},
        registry,
        ShuffledRoundRouter(1),
        seed=1,
    )
    runner._run_turn("shen_lan", 1)
    assert runner.state.root_room_of("shen_lan") == "study"
    assert [event.action_kind for event in runner.harness.world_log] == ["move"]
    notice = runner.runtimes["shen_lan"].execution_notices[0]
    assert notice.code == "move_truncated"
    assert notice.action_index == 0
    assert notice.unexecuted_from_action_index == 1
    assert notice.unexecuted_through_action_index == 1
    assert "frame" not in notice.message


def test_runner_truncation_uses_last_state_changing_move(scenario, registry):
    stale_plan = registry.parse_plan({"actions": [
        {"kind": "move", "target_id": "study"},
        {"kind": "move", "target_id": "foyer"},
        {"kind": "take", "target_id": "ebony_box"},
    ]})
    agent = ScriptedAgent({"shen_lan": [stale_plan]}, registry)
    runner = ActRunner(
        scenario,
        {actor_id: agent for actor_id in scenario.world.character_ids},
        registry,
        ShuffledRoundRouter(1),
        seed=1,
    )
    runner._run_turn("shen_lan", 1)
    assert runner.state.root_room_of("shen_lan") == "foyer"
    assert [event.action_kind for event in runner.harness.world_log] == ["move", "move"]


def test_execution_notice_is_next_context_json_feedback(scenario, registry):
    redundant_move = registry.parse_plan({"actions": [{
        "kind": "move", "target_id": "drawing_room"
    }]})
    agent = ScriptedAgent({"shen_lan": [redundant_move]}, registry)
    runner = ActRunner(
        scenario,
        {actor_id: agent for actor_id in scenario.world.character_ids},
        registry,
        ShuffledRoundRouter(1),
        seed=1,
    )
    runner._run_turn("shen_lan", 1)
    environment = runner.observation.scan_environment(runner.state, "shen_lan", 2)
    context = runner.context_projector.build(
        runner.state, runner.runtimes["shen_lan"], environment, 2
    )
    rendered = json.loads(LLMAgent._render_context(context))
    assert rendered["last_action_feedback"][0]["code"] == "redundant_move"
    assert "frame_index" not in rendered["last_action_feedback"][0]
    assert runner.runtimes["shen_lan"].execution_notices == []


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


def test_v3_run_records_tokens_and_replays(scenario, registry, wait_plan, tmp_path):
    usage = TokenUsage(
        prompt_tokens=100, prompt_cache_hit_tokens=80,
        prompt_cache_miss_tokens=20, completion_tokens=10, total_tokens=110,
    )

    class UsageParticipant:
        def decide(self, request):
            plan = wait_plan(f"{request.actor_id} 等待")
            return AgentDecision(
                actor_id=request.actor_id,
                raw_content=plan.model_dump_json(serialize_as_any=True),
                plan=plan,
                usage=usage,
            )

    recorder = RunRecorder(
        scenario, seed=11,
        modes={actor: "test" for actor in scenario.world.character_ids},
        root=tmp_path, run_id="v3-replay",
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
    manifest = json.loads((recorder.run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 3
    usage_data = json.loads((recorder.run_dir / "token_usage.json").read_text(encoding="utf-8"))
    assert usage_data["total"]["prompt_tokens"] == 600
    assert replay_run(recorder.run_dir).success


def test_replay_explicitly_rejects_v2(tmp_path):
    run_dir = tmp_path / "old-run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps({"schema_version": 2}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="schema v3"):
        replay_run(run_dir)


def test_prompt_flow_records_json_rejection_and_fallback(scenario, registry, tmp_path):
    class RejectingBackend(FakeBackend):
        def complete(self, request):
            self.requests.append(request)
            return LLMResponse(content="{}", model=request.profile.model)

    participants = build_llm_agents(scenario, registry, RejectingBackend())
    recorder = RunRecorder(
        scenario, seed=23,
        modes={actor: "standard" for actor in scenario.world.character_ids},
        root=tmp_path, run_id="prompt-flow",
    )
    ActRunner(
        scenario, participants, registry, ShuffledRoundRouter(23), seed=23,
        recorder=recorder,
    ).run(1)
    prompt_flow = (recorder.run_dir / "prompt_flow.md").read_text(encoding="utf-8")
    assert '"action_rejected": true' in prompt_flow
    assert "TurnPlan.actions 缺少必填字段" in prompt_flow
    assert "Automatic fallback" in prompt_flow
    assert "World Harness" not in prompt_flow


def test_fifty_round_llm_sessions_are_never_compressed(scenario, registry):
    agents = build_llm_agents(scenario, registry, FakeBackend())
    runner = ActRunner(scenario, agents, registry, ShuffledRoundRouter(29), seed=29)
    runner.run(50)
    for agent in agents.values():
        assert len(agent.requests) == 50
        assert len(agent.messages) == 101
        assert agent.requests[-1][:2] == agent.requests[0]
