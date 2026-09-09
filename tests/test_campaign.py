import json
import time
from threading import Thread
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from token_odyssey.config.models import RunConfig
from token_odyssey.llm.contracts import LLMResponse
from token_odyssey.orchestration.agents import CampaignLLMService
from token_odyssey.orchestration.models import (
    ActOutcome, CampaignBible, CampaignCharacter, CampaignState, DirectorTransition,
)
from token_odyssey.orchestration.session import CampaignSession
from token_odyssey.orchestration.store import CampaignStore
from token_odyssey.orchestration.prompts import scene_builder_system
from token_odyssey.interfaces.campaign_web.server import create_server
from token_odyssey.kernel.actions.registry import builtin_registry
from token_odyssey.runtime.composition import BACKEND_FACTORIES
from token_odyssey.scenario import compile_scenario


def scenario(act, *, finale=False):
    data = {
        "schema_version": 3, "id": f"act_{act}", "title": f"第{act}幕", "max_rounds": 3,
        "public_background": "Hero 面对眼前的选择。", "routing": {"strategy": "interaction"},
        "world": {"entities": {
            "room": {"kind": "room", "name": "旧大厅"},
            "Hero": {"kind": "character", "name": "Hero", "description": "远征幸存者。"},
        }, "passages": {}, "flag_names": [], "mechanics": []},
        "initial_state": {"placements": {"Hero": {"parent_id": "room"}}},
        "roles": {"Hero": {"personality": "谨慎而坚定", "private_goal": "理解真相"}},
        "end_when": [], "expected": [],
    }
    if not finale:
        data["world"]["entities"]["lever"] = {
            "kind": "item", "name": "归航拉杆", "portable": False, "operable": True,
        }
        data["initial_state"]["placements"]["lever"] = {"parent_id": "room"}
        data["world"]["flag_names"] = ["released"]
        data["world"]["mechanics"] = [{
            "id": "release", "trigger": "operated", "subject_id": "lever", "source_id": "lever",
            "effects": [{"kind": "flag", "subject_id": "released", "value": True}],
            "visual_description": "拉杆落下，旧门闩随之松开。",
        }]
        data["end_when"] = [{"kind": "flag", "subject_id": "released", "value": True}]
        data["expected"] = list(data["end_when"])
    return data


def campaign_config(monkeypatch):
    responses = {
        "director": [
            {
                "bible": {"title": "潮痕远征", "public_world": "群岛被旧潮汐机械连接。",
                    "major_history": ["十年前远征队封闭了北方潮门。"], "central_conflict": "潮门再次苏醒。",
                    "protagonist_id": "Hero", "characters": [{"id": "Hero", "name": "Hero",
                        "description": "远征幸存者。", "personality": "谨慎而坚定", "public_role": "航路测绘员",
                        "inner_life": "担心自己当年的决定伤害了同伴。", "historical_tie": "亲手封闭过北方潮门。"}],
                    "protagonist_ties": ["Hero 的旧罗盘是潮门钥匙。"], "main_threads": ["查明潮门苏醒原因"]},
                "first_act": {"act_number": 1, "title": "旧门回声", "dramatic_purpose": "让主角面对历史",
                    "opening": "Hero 回到旧大厅，先听见远征者留下的录音。", "player_goal": "放下归航拉杆",
                    "cast_ids": ["Hero"], "required_entity_ids": ["lever"], "is_finale": False},
            },
            {"decision": "prepare_finale", "act_assessment": "Hero 打开了归路。",
                "resolved_threads": ["查明潮门苏醒原因"], "remaining_threads": [],
                "public_interlude": "潮水退去后，Hero 与留下的人在大厅重聚。",
                "character_interludes": [{"observer_id": "Hero", "content": "Hero 亲眼看见潮水退去。"}],
                "continuity_notes": ["Hero 仍在旧大厅"],
                "next_act": {"act_number": 2, "title": "退潮之后", "dramatic_purpose": "告别与确认",
                    "opening": "旧大厅安静下来。", "player_goal": "与重要的人说最后几句话",
                    "cast_ids": ["Hero"], "required_entity_ids": [], "is_finale": True}},
            {"decision": "conclude", "final_narration": "清晨，Hero 终于离开旧大厅。",
                "ending_summary": "潮门的故事在退潮声中结束。", "resolved_threads": ["查明潮门苏醒原因"],
                "remaining_threads": []},
        ],
        "scene": [{"schema_version": 3}, scenario(1), scenario(2, finale=True)],
        "world": [
            {"summary": "OBJECTIVE_SECRET：拉杆已被操作。", "confirmed_changes": ["released=true"],
             "unresolved_outcomes": []},
            {"summary": "Hero 在终幕停留片刻。", "confirmed_changes": [], "unresolved_outcomes": []},
        ],
        "memory": [
            {"actor_id": "Hero", "durable_memories": ["我亲眼看见旧门闩松开。"], "inner_state": "如释重负。"},
            {"actor_id": "Hero", "durable_memories": ["我在退潮后的大厅完成了告别。"], "inner_state": "愿意前行。"},
        ],
    }
    requests = []

    class Backend:
        def __init__(self, key):
            self.key = key

        def complete(self, request):
            requests.append((self.key, request))
            return LLMResponse(content=json.dumps(responses[self.key].pop(0), ensure_ascii=False),
                               model=request.profile.model)

    monkeypatch.setitem(BACKEND_FACTORIES, "campaign_test", lambda config: Backend(config.base_url))
    raw = {"backends": {}, "profiles": {}, "campaign": {
        "director_profile": "director", "scene_builder_profile": "scene",
        "world_summary_profile": "world", "character_memory_profile": "memory", "npc_profile": "memory",
    }}
    for key in responses:
        raw["backends"][key] = {"driver": "campaign_test", "base_url": key, "api_key_env": "UNUSED"}
        raw["profiles"][key] = {"backend_id": key, "model": key}
    return RunConfig.model_validate(raw), requests


def wait_for(session, phase, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if session.snapshot().get("phase") == phase:
            return session.snapshot()
        time.sleep(0.01)
    raise AssertionError(session.snapshot())


def test_campaign_runs_two_acts_with_manual_finale_and_isolated_memory(tmp_path, monkeypatch):
    config, requests = campaign_config(monkeypatch)
    session = CampaignSession(config, runs_dir=tmp_path)
    session.start({"creative_brief": "一场与主角旧远征有关的群岛故事。", "seed": 9})
    first = wait_for(session, "interlude")
    assert first["act_number"] == 1 and first["protagonist"]["id"] == "Hero"

    session.command("enter-act", {"campaign_id": first["campaign_id"]})
    playing = wait_for(session, "playing_act")
    while playing["act"]["status"] != "waiting_for_input":
        time.sleep(0.01)
        playing = session.snapshot()
    pending = playing["act"]["pending"]
    session.command("submit", {"campaign_id": first["campaign_id"], "actor_id": "Hero",
        "request_id": pending["request_id"], "actions": [{"kind": "operate", "device_id": "lever"}], "auto": True})
    second = wait_for(session, "interlude")
    assert second["act_number"] == 2 and second["current_is_finale"]
    campaign_path = tmp_path / "campaigns" / first["campaign_id"]
    assert (campaign_path / "scene_validation_errors.jsonl").is_file()

    # A ready act is a durable boundary. A second process can pick it up
    # without repeating director, scene or memory model calls.
    resumed = CampaignSession(config, runs_dir=tmp_path)
    resumed.resume(campaign_path)
    assert resumed.snapshot()["phase"] == "interlude"
    session = resumed

    session.command("enter-act", {"campaign_id": first["campaign_id"]})
    playing = wait_for(session, "playing_act")
    while playing["act"]["status"] != "waiting_for_input":
        time.sleep(0.01)
        playing = session.snapshot()
    # Simulate a service restart in the middle of the finale. The exact turn
    # is intentionally not restored; the act returns to its ready checkpoint.
    restarted = CampaignSession(config, runs_dir=tmp_path)
    restarted.resume(campaign_path)
    restart_state = restarted.snapshot()
    assert restart_state["phase"] == "interlude" and restart_state["resume_notice"]
    session = restarted
    session.command("enter-act", {"campaign_id": first["campaign_id"]})
    playing = wait_for(session, "playing_act")
    while playing["act"]["status"] != "waiting_for_input":
        time.sleep(0.01)
        playing = session.snapshot()
    session.command("end-act", {"campaign_id": first["campaign_id"]})
    ending = wait_for(session, "completed")
    assert ending["conclusion"]["decision"] == "conclude"
    assert ending["checkpoint_kind"] == "act_finished"

    memory_prompts = [message.content for key, request in requests if key == "memory"
                      for message in request.messages if message.role == "user"]
    assert memory_prompts and all("OBJECTIVE_SECRET" not in prompt for prompt in memory_prompts)
    assert (campaign_path / "checkpoint.json").is_file()
    director_requests = [request.messages for key, request in requests if key == "director"]
    assert [len(messages) for messages in director_requests] == sorted(len(messages) for messages in director_requests)
    for before, after in zip(director_requests, director_requests[1:]):
        assert after[:len(before)] == before


def test_finale_transition_rejects_remaining_threads():
    try:
        DirectorTransition.model_validate({"decision": "prepare_finale", "act_assessment": "失败",
            "remaining_threads": ["仍未解决"], "public_interlude": "夜幕降临。",
            "next_act": {"act_number": 2, "title": "终幕", "dramatic_purpose": "收束", "opening": "众人重聚。",
                "player_goal": "告别", "cast_ids": ["Hero"], "is_finale": True}})
    except ValueError as exc:
        assert "unresolved" in str(exc)
    else:
        raise AssertionError("finale with remaining threads was accepted")


def test_old_run_config_remains_valid_and_campaign_profiles_are_checked():
    assert RunConfig().campaign is None
    try:
        RunConfig.model_validate({"campaign": {"director_profile": "missing", "scene_builder_profile": "missing",
            "world_summary_profile": "missing", "character_memory_profile": "missing", "npc_profile": "missing"}})
    except ValueError as exc:
        assert "unknown profile" in str(exc)
    else:
        raise AssertionError("unknown campaign profile was accepted")


def test_scene_agent_receives_packaged_generation_and_current_router_docs():
    prompt = scene_builder_system(builtin_registry())
    assert "docs/scenario-generation.md" in prompt
    assert "docs/router.md" in prompt
    assert "routing.strategy=interaction" in prompt
    assert "Scenario schema" in prompt and "动作 schemas" in prompt


def test_each_character_memory_receives_only_its_observations_and_interlude(tmp_path, monkeypatch):
    responses = [
        {"actor_id": "Hero", "durable_memories": ["ALPHA"], "inner_state": "平静"},
        {"actor_id": "Nia", "durable_memories": ["BRAVO"], "inner_state": "警觉"},
    ]
    captured = []

    class Backend:
        def complete(self, request):
            captured.append(request)
            return LLMResponse(content=json.dumps(responses.pop(0), ensure_ascii=False))

    monkeypatch.setitem(BACKEND_FACTORIES, "memory_test", lambda config: Backend())
    config = RunConfig.model_validate({
        "backends": {"memory": {"driver": "memory_test", "base_url": "memory", "api_key_env": "UNUSED"}},
        "profiles": {"memory": {"backend_id": "memory", "model": "memory"}},
        "campaign": {"director_profile": "memory", "scene_builder_profile": "memory",
            "world_summary_profile": "memory", "character_memory_profile": "memory", "npc_profile": "memory"},
    })
    raw = scenario(2, finale=True)
    raw["world"]["entities"]["Nia"] = {"kind": "character", "name": "Nia", "description": "领航员。"}
    raw["initial_state"]["placements"]["Nia"] = {"parent_id": "room"}
    raw["roles"]["Nia"] = {"personality": "警觉"}
    compiled = compile_scenario(raw).model_dump(mode="json")
    act_dir = tmp_path / "act"
    act_dir.mkdir()
    observations = [
        {"observer_id": "Hero", "facts": [{"kind": "speech", "fields": {"content": "ALPHA_ONLY"}}]},
        {"observer_id": "Nia", "facts": [{"kind": "speech", "fields": {"content": "BRAVO_ONLY"}}]},
    ]
    (act_dir / "observations.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in observations) + "\n", encoding="utf-8")
    store = CampaignStore.create(tmp_path / "campaigns")
    session = CampaignSession(config, runs_dir=tmp_path)
    session.store = store
    session.llm = CampaignLLMService(config, store)
    session.state = CampaignState(
        campaign_id=store.path.name, phase="remembering", act_number=2, protagonist_id="Hero",
        bible=CampaignBible(title="测试", public_world="公开世界", major_history=("公开历史",),
            central_conflict="公开冲突", protagonist_id="Hero", protagonist_ties=("公开联系",),
            main_threads=("公开主线",), characters=(
                CampaignCharacter(id="Hero", name="Hero", description="主角", personality="谨慎",
                    public_role="测绘员", inner_life="自己的心事", historical_tie="自己的历史"),
                CampaignCharacter(id="Nia", name="Nia", description="同伴", personality="警觉",
                    public_role="领航员", inner_life="另一个人的心事", historical_tie="另一个人的历史"),
            )),
        current_scenario=compiled,
        act_outcome=ActOutcome(act_number=2, status="stopped", reason="player_ended", run_dir=str(act_dir)),
    )
    interludes = (
        type("Interlude", (), {"observer_id": "Hero", "content": "HERO_INTERLUDE"})(),
        type("Interlude", (), {"observer_id": "Nia", "content": "NIA_INTERLUDE"})(),
    )
    session._remember_characters({"Hero", "Nia"}, interludes)
    prompts = {json.loads(request.messages[-1].content)["actor_id"]: request.messages[-1].content
               for request in captured}
    assert "ALPHA_ONLY" in prompts["Hero"] and "BRAVO_ONLY" not in prompts["Hero"]
    assert "HERO_INTERLUDE" in prompts["Hero"] and "NIA_INTERLUDE" not in prompts["Hero"]
    assert "BRAVO_ONLY" in prompts["Nia"] and "ALPHA_ONLY" not in prompts["Nia"]


def test_campaign_http_separates_player_and_debug_data(tmp_path, monkeypatch):
    config, _ = campaign_config(monkeypatch)
    session = CampaignSession(config, runs_dir=tmp_path)
    server = create_server(session, port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    opener = build_opener(ProxyHandler({}))

    def request(path, payload=None, headers=None):
        body = None if payload is None else json.dumps(payload).encode()
        return opener.open(Request(base + path, data=body, headers=headers or {}), timeout=5)

    try:
        assert "完整多 ACT 游玩" in request("/").read().decode()
        catalog = json.load(request("/api/catalog"))
        encoded = json.dumps(catalog)
        assert "backends" not in encoded and "api_key" not in encoded
        with pytest.raises(HTTPError) as denied:
            request("/api/start", {"creative_brief": "测试"}, {"Content-Type": "application/json"})
        assert denied.value.code == 403
        headers = {"Content-Type": "application/json", "X-Playtest-Token": catalog["token"]}
        request("/api/start", {"creative_brief": "测试一个与旧历史有关的故事。", "seed": 3}, headers).close()
        wait_for(session, "interlude")
        player = json.load(request("/api/state"))
        assert "bible" not in player and "current_scenario" not in player and "director_messages" not in player
        debug = json.load(request("/api/debug"))
        assert debug["bible"] and debug["current_scenario"] and debug["director_messages"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_act_technical_failure_pauses_without_starting_story_summary(tmp_path, monkeypatch):
    config, _ = campaign_config(monkeypatch)
    session = CampaignSession(config, runs_dir=tmp_path)
    session.store = CampaignStore.create(tmp_path / "campaigns")
    session.state = CampaignState(campaign_id=session.store.path.name, phase="playing_act")

    class Worker:
        def join(self):
            pass

    session.act = type("FailedAct", (), {"status": "failed", "error": "transport unavailable"})()
    launched = []
    session._launch = lambda target, name: launched.append(name)
    session._finish_act_worker(Worker())
    assert session.state.phase == "technical_failed"
    assert session.state.act_outcome is None
    assert not launched
