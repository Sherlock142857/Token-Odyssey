import json
import time
from pathlib import Path
from threading import Event, Thread
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from token_odyssey.config.models import RunConfig, load_run_config
from token_odyssey.llm.contracts import ChatMessage, ChatRole, LLMResponse, TokenUsage
from token_odyssey.orchestration.agents import CampaignLLMService
from token_odyssey.orchestration.models import (
    ActBrief, ActOutcome, CampaignBible, CampaignCharacter, CampaignGenesis, CampaignState,
    DirectorTransition,
)
from token_odyssey.orchestration.session import CampaignSession
from token_odyssey.orchestration.store import CampaignStore
from token_odyssey.orchestration.prompts import (
    CHARACTER_MEMORY_SYSTEM, DIRECTOR_SYSTEM, WORLD_SUMMARY_SYSTEM, scene_builder_system,
)
from token_odyssey.interfaces.campaign_web.server import create_server
from token_odyssey.kernel.actions.registry import builtin_registry
from token_odyssey.runtime.composition import BACKEND_FACTORIES
from token_odyssey.scenario import compile_scenario
from token_odyssey.translators.llm import LLMIdentity, LLMTranslator


def scenario(act, *, finale=False):
    data = {
        "schema_version": 3, "id": f"act_{act}", "title": f"第{act}幕", "max_rounds": 3,
        "public_background": "Hero 面对眼前的选择。", "routing": {"strategy": "interaction"},
        "world": {"entities": {
            "room": {"kind": "room", "name": "旧大厅"},
            "Hero": {"kind": "character", "name": "Hero", "description": "远征幸存者。"},
        }, "passages": {}, "flag_names": [], "mechanics": []},
        "initial_state": {"placements": {"Hero": {"parent_id": "room"}}},
        "roles": {},
        "end_when": [], "expected": [],
    }
    if not finale:
        data["world"]["entities"]["lever"] = {
            "kind": "item", "name": "归航拉杆", "portable": False, "operable": True,
            "perception": {
                "scan": {"description": "一根固定在石座上的铜制拉杆。"},
                "inspect": {"description": "拉杆底部的归航刻度与潮门传动轴相连。"},
            },
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
                        "inner_life": "我担心自己当年的决定伤害了同伴。", "historical_tie": "亲手封闭过北方潮门。"}],
                    "protagonist_ties": ["Hero 的旧罗盘是潮门钥匙。"], "main_threads": ["查明潮门苏醒原因"],
                    "story_outline": ["旧潮门异动迫使 Hero 重返现场", "线索揭示远征旧决定的后果", "Hero 作出选择并承担潮门的最终后果"]},
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
            {"actor_id": "Hero", "durable_memories": ["我亲眼看见旧门闩松开。"], "inner_state": "我如释重负。"},
            {"actor_id": "Hero", "durable_memories": ["我在退潮后的大厅完成了告别。"], "inner_state": "我愿意前行。"},
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
    assert session.state.current_scenario["roles"]["Hero"]["personality"] == "谨慎而坚定"

    session.command("enter-act", {"campaign_id": first["campaign_id"]})
    playing = wait_for(session, "playing_act")
    while playing["act"]["status"] != "waiting_for_input" or playing["act"]["busy"]:
        time.sleep(0.01)
        playing = session.snapshot()
    session.command("developer-instruction", {
        "campaign_id": first["campaign_id"], "developer_instruction": "   ",
    })
    assert session.debug_snapshot()["developer_instruction"] == ""
    developer_instruction = "下一幕必须去黑曜石灯塔测试潜入路线。"
    session.command("developer-instruction", {
        "campaign_id": first["campaign_id"], "developer_instruction": developer_instruction,
    })
    assert session.debug_snapshot()["developer_instruction"] == developer_instruction
    pending = playing["act"]["pending"]
    session.command("submit", {"campaign_id": first["campaign_id"], "actor_id": "Hero",
        "request_id": pending["request_id"], "actions": [{"kind": "operate", "device_id": "lever"}], "auto": True})
    second = wait_for(session, "interlude")
    assert second["act_number"] == 2 and second["current_is_finale"]
    assert session.debug_snapshot()["developer_instruction"] == ""
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
    while playing["act"]["status"] != "waiting_for_input" or playing["act"]["busy"]:
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
    while playing["act"]["status"] != "waiting_for_input" or playing["act"]["busy"]:
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
    assert any(developer_instruction in message.content
               for request in director_requests for message in request)
    assert any("现在是开发者测试" in message.content
               for request in director_requests for message in request)
    director_prompt_text = "\n".join(
        message.content for request in director_requests for message in request
        if message.role == "user"
    )
    for result_type in ("CampaignGenesis", "DirectorTransition", "DirectorConclusion"):
        assert f"当前调用只允许返回 {result_type}" in director_prompt_text
    assert [len(messages) for messages in director_requests] == sorted(len(messages) for messages in director_requests)
    for before, after in zip(director_requests, director_requests[1:]):
        assert after[:len(before)] == before
    scene_requests = [request for key, request in requests if key == "scene"]
    assert [len(request.messages) for request in scene_requests] == [2, 3, 2]
    assert all(message.role != "assistant" for message in scene_requests[1].messages)
    for request in (scene_requests[0], scene_requests[2]):
        payload = json.loads(request.messages[1].content)
        assert set(payload) == {
            "campaign_context", "act_brief", "cast_physical_profiles", "physical_continuity",
        }
        encoded = request.messages[1].content
        assert "我担心自己当年的决定" not in encoded
        assert "我亲眼看见旧门闩松开" not in encoded
        assert "十年前远征队封闭了北方潮门" in encoded

    world_requests = [request for key, request in requests if key == "world"]
    assert all(developer_instruction not in message.content
               for request in world_requests for message in request.messages)
    first_world_payload = json.loads(world_requests[0].messages[-1].content)
    assert set(first_world_payload) == {
        "act_context", "termination", "state_delta", "committed_timeline",
    }
    assert "action_results" not in first_world_payload


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
    assert "人物性格、记忆、内心活动" in prompt
    assert "scan 基础外观" in prompt
    assert "物理边界只能由 Passage 表示" in prompt
    assert "绝不能再建一个同名 Item" in prompt


def test_new_campaign_requires_story_outline_but_old_bible_remains_loadable():
    bible = {
        "title": "测试", "public_world": "公开世界", "major_history": ["旧事"],
        "central_conflict": "冲突", "protagonist_id": "Hero",
        "characters": [{"id": "Hero", "name": "Hero", "description": "主角",
            "personality": "谨慎", "public_role": "旅人", "inner_life": "我保持警惕。",
            "historical_tie": "我经历过旧事。"}],
        "protagonist_ties": ["与旧事有关"], "main_threads": ["查清真相"],
    }
    assert CampaignBible.model_validate(bible).story_outline == ()
    genesis = {"bible": bible, "first_act": {"act_number": 1, "title": "开端",
        "dramatic_purpose": "建立冲突", "opening": "Hero 到达。", "player_goal": "调查",
        "cast_ids": ["Hero"]}}
    with pytest.raises(ValueError, match="story outline"):
        CampaignGenesis.model_validate(genesis)
    genesis["bible"]["story_outline"] = ["冲突出现", "真相升级", "选择带来结局"]
    assert len(CampaignGenesis.model_validate(genesis).bible.story_outline) == 3


def test_deepseek_scene_builder_has_stable_dedicated_profile():
    config = load_run_config("configs/llm.deepseek.yaml")
    name = config.campaign.scene_builder_profile
    profile = config.profiles[name]
    assert name == "builder"
    assert profile.temperature == 0.2
    assert profile.max_output_tokens == 8192


def test_campaign_prompts_fix_act_scope_lighting_and_first_person_thoughts():
    prompt = scene_builder_system(builtin_registry())
    assert "不等于一个 Room 节点" in DIRECTOR_SYSTEM
    assert "走出去绝不能单独触发下一 Act" in DIRECTOR_SYSTEM
    assert "完全不同的新主环境" in DIRECTOR_SYSTEM
    assert "即使情节尚未推动到预期位置" in DIRECTOR_SYSTEM
    assert "不得阻止场景切换和主线继续向前推进" in DIRECTOR_SYSTEM
    assert "第一人称" in DIRECTOR_SYSTEM and "第一人称" in CHARACTER_MEMORY_SYSTEM
    assert "积极推动故事发展" in DIRECTOR_SYSTEM and "public_interlude" in DIRECTOR_SYSTEM
    assert "CampaignCharacter.description" in DIRECTOR_SYSTEM
    assert "完整替换列表" in CHARACTER_MEMORY_SYSTEM
    assert "排除了所有未被内核接受" in WORLD_SUMMARY_SYSTEM
    assert "绝不得低于0.8" in prompt
    assert "普通换房" in prompt
    assert "initial_state.openings 只能包含" in prompt
    assert "Character description 或 perception" in prompt
    npc_prompt = LLMTranslator(builtin_registry(), LLMIdentity(
        actor_id="Hero", name="Hero", private_goal="我想查明真相。",
    )).system_prompt()
    assert "private_thought 中用自己的姓名" in npc_prompt
    assert "尽量不要在行动或台词中虚构当前场景里不存在或尚未获知" in npc_prompt
    assert '"private_thought":"我的私有想法' in npc_prompt


def test_empty_developer_instruction_adds_nothing_to_director_prompt(tmp_path, monkeypatch):
    config, _ = campaign_config(monkeypatch)
    session = CampaignSession(config, runs_dir=tmp_path)
    session.state = CampaignState(campaign_id="developer-instruction-test")
    assert session._developer_instruction_prompt() == ""
    session.state.developer_instruction = "在下一幕测试一次追逐。"
    prompt = session._developer_instruction_prompt()
    assert "现在是开发者测试" in prompt
    assert "在下一幕测试一次追逐。" in prompt
    assert "本次仍只能输出 DirectorTransition" in prompt
    assert "remaining_threads=[]" in prompt and "decision=prepare_finale" in prompt

    session.state.current_is_finale = True
    prompt = session._developer_instruction_prompt()
    assert "本次仍只能输出 DirectorConclusion" in prompt
    assert "不得输出 next_act" in prompt


def test_campaign_scene_rejects_room_light_below_playable_floor(tmp_path, monkeypatch):
    config, _ = campaign_config(monkeypatch)
    session = CampaignSession(config, runs_dir=tmp_path)
    bible = CampaignBible(
        title="测试", public_world="公开世界", major_history=("公开历史",),
        central_conflict="公开冲突", protagonist_id="Hero", protagonist_ties=("公开联系",),
        main_threads=("公开主线",), characters=(CampaignCharacter(
            id="Hero", name="Hero", description="主角", personality="谨慎",
            public_role="测绘员", inner_life="我担心历史重演。", historical_tie="参与过旧事。",
        ),),
    )
    session.state = CampaignState(
        campaign_id="lighting-test", protagonist_id="Hero", bible=bible,
    )
    raw = scenario(1)
    raw["world"]["entities"]["room"]["light"] = 0.79
    brief = ActBrief(
        act_number=1, title="第一幕", dramatic_purpose="建立冲突", opening="主角到达。",
        player_goal="完成当地任务", cast_ids=("Hero",), required_entity_ids=("lever",),
    )
    with pytest.raises(ValueError, match="light must be at least 0.8"):
        session._validate_campaign_scenario(raw, brief)


def test_campaign_scene_requires_scan_and_key_item_inspect_descriptions(tmp_path, monkeypatch):
    config, _ = campaign_config(monkeypatch)
    session = CampaignSession(config, runs_dir=tmp_path)
    session.state = CampaignState(
        campaign_id="description-test", protagonist_id="Hero",
        bible=CampaignBible(
            title="测试", public_world="公开世界", major_history=("公开历史",),
            central_conflict="公开冲突", protagonist_id="Hero", protagonist_ties=("公开联系",),
            main_threads=("公开主线",), characters=(CampaignCharacter(
                id="Hero", name="Hero", description="主角", personality="谨慎",
                public_role="测绘员", inner_life="我保持警惕。", historical_tie="参与过旧事。",
            ),),
        ),
    )
    brief = ActBrief(act_number=1, title="第一幕", dramatic_purpose="建立冲突", opening="抵达。",
                     player_goal="操作拉杆", cast_ids=("Hero",), required_entity_ids=("lever",))
    raw = scenario(1)
    raw["world"]["entities"]["lever"]["perception"] = {}
    with pytest.raises(ValueError, match="scan appearance"):
        session._validate_campaign_scenario(raw, brief)
    raw = scenario(1)
    raw["world"]["entities"]["lever"]["perception"].pop("inspect")
    with pytest.raises(ValueError, match="distinct inspect"):
        session._validate_campaign_scenario(raw, brief)
    raw = scenario(1)
    raw["world"]["entities"]["plain_wall"] = {
        "kind": "item", "name": "固定墙板", "portable": False,
        "perception": {"scan": {"description": "一块普通的固定墙板。"}},
    }
    raw["initial_state"]["placements"]["plain_wall"] = {"parent_id": "room"}
    assert session._validate_campaign_scenario(raw, brief).roles["Hero"].personality == "谨慎"


def test_campaign_scene_rejects_item_copy_of_physical_passage(tmp_path, monkeypatch):
    config, _ = campaign_config(monkeypatch)
    session = CampaignSession(config, runs_dir=tmp_path)
    session.state = CampaignState(
        campaign_id="duplicate-door-test", protagonist_id="Hero",
        bible=CampaignBible(
            title="测试", public_world="公开世界", major_history=("公开历史",),
            central_conflict="公开冲突", protagonist_id="Hero", protagonist_ties=("公开联系",),
            main_threads=("公开主线",), characters=(CampaignCharacter(
                id="Hero", name="Hero", description="主角", personality="谨慎",
                public_role="测绘员", inner_life="我保持警惕。", historical_tie="参与过旧事。",
            ),),
        ),
    )
    brief = ActBrief(act_number=1, title="第一幕", dramatic_purpose="建立冲突", opening="抵达。",
                     player_goal="操作拉杆", cast_ids=("Hero",), required_entity_ids=("lever",))
    raw = scenario(1)
    raw["world"]["entities"]["room_two"] = {"kind": "room", "name": "内庭"}
    raw["world"]["entities"]["copied_door"] = {
        "kind": "item", "name": "内庭门", "portable": False, "container": {}, "openable": {},
        "perception": {
            "scan": {"description": "一扇关闭的内庭门。"},
            "inspect": {"description": "门板内侧带着湿润的苔痕。"},
        },
    }
    raw["world"]["passages"]["courtyard_door"] = {
        "name": "内庭门", "rooms": ["room", "room_two"], "openable": {},
    }
    raw["initial_state"]["placements"]["copied_door"] = {
        "parent_id": "room", "relation": "attached",
    }
    with pytest.raises(ValueError, match="represented only by its Passage"):
        session._validate_campaign_scenario(raw, brief)


def test_campaign_scene_normalizes_safe_defaults_and_static_character_prose(tmp_path, monkeypatch):
    config, _ = campaign_config(monkeypatch)
    session = CampaignSession(config, runs_dir=tmp_path)
    session.state = CampaignState(
        campaign_id="normalization-test", protagonist_id="Hero",
        bible=CampaignBible(
            title="测试", public_world="公开世界", major_history=("公开历史",),
            central_conflict="公开冲突", protagonist_id="Hero", protagonist_ties=("公开联系",),
            main_threads=("公开主线",), characters=(CampaignCharacter(
                id="Hero", name="Hero", description="远征幸存者。", personality="谨慎",
                public_role="测绘员", inner_life="我保持警惕。", historical_tie="参与过旧事。",
            ),),
        ),
    )
    brief = ActBrief(act_number=1, title="第一幕", dramatic_purpose="建立冲突", opening="抵达。",
                     player_goal="操作拉杆", cast_ids=("Hero",), required_entity_ids=("lever",))
    raw = scenario(1)
    raw["world"]["entities"]["room_two"] = {"kind": "room", "name": "码头"}
    raw["world"]["passages"]["gangplank"] = {
        "name": "跳板", "rooms": ["room", "room_two"],
    }
    raw["initial_state"]["openings"] = {"gangplank": True}
    raw["world"]["entities"]["Hero"]["description"] = "Hero 正站在门边擦杯子。"
    raw["world"]["entities"]["Hero"]["perception"] = {
        "scan": {"description": "Hero 仍在擦杯子。"},
    }
    compiled = session._validate_campaign_scenario(raw, brief)
    hero = compiled.world.entities["Hero"]
    assert hero.description == "远征幸存者。"
    assert hero.perception == {}
    assert "gangplank" not in compiled.initial_state.openings

    raw["initial_state"]["openings"] = {"gangplank": False}
    with pytest.raises(ValueError, match=r"openings extra=\['gangplank'\]"):
        session._validate_campaign_scenario(raw, brief)


def test_previous_physical_continuity_strips_only_character_prose():
    world = {
        "entities": {
            "Hero": {"kind": "character", "name": "Hero", "description": "正在擦杯子",
                     "perception": {"scan": {"description": "站在吧台后"}}},
            "map": {"kind": "item", "name": "地图", "description": "泛黄的纸张",
                    "perception": {"scan": {"description": "一张旧地图"}}},
        },
        "passages": {},
    }
    stripped = CampaignSession._physical_continuity_world(world)
    assert "description" not in stripped["entities"]["Hero"]
    assert "perception" not in stripped["entities"]["Hero"]
    assert stripped["entities"]["map"]["description"] == "泛黄的纸张"
    assert world["entities"]["Hero"]["description"] == "正在擦杯子"


def test_summary_and_memory_inputs_are_compact_and_observer_scoped():
    compiled = compile_scenario(scenario(1))
    transaction = {
        "id": 1,
        "events": [{
            "kind": "say", "source": "action", "actor_id": "Hero", "mechanic_id": None,
            "data": {"content": "只保留已提交台词", "listener_ids": []},
            "signals": ["speech"], "subject_ids": ["Hero"], "changes": [],
            "cues": [{"fact": {"kind": "speech", "fields": {"content": "冗余 cue"}}}],
            "sequence": 1, "transaction_id": 1, "caused_by": None,
        }],
    }
    timeline = CampaignSession._committed_timeline([transaction], compiled)
    encoded = json.dumps(timeline, ensure_ascii=False)
    assert "只保留已提交台词" in encoded
    assert "冗余 cue" not in encoded and "signals" not in encoded and "subject_ids" not in encoded
    assert CampaignSession._state_delta(
        {"flags": {"released": False}}, {"flags": {"released": True}},
    ) == [{"table": "flags", "key": "released", "before": False, "after": True}]

    observations = [
        {"sequence": 1, "observer_id": "Hero", "source": "event",
         "source_event_sequence": 1,
         "facts": [{"kind": "speech", "fields": {"actor_id": "Hero", "content": "我听见了线索"}}],
         "entities": [], "labels": {"Hero": "Hero"}},
        {"sequence": 2, "observer_id": "Hero", "source": "room", "source_event_sequence": None,
         "facts": [{"kind": "location", "fields": {"room_id": "room"}}],
         "entities": [], "labels": {"room": "旧大厅"}},
        {"sequence": 3, "observer_id": "Hero", "source": "room", "source_event_sequence": None,
         "facts": [{"kind": "location", "fields": {"room_id": "room"}}],
         "entities": [], "labels": {"room": "旧大厅"}},
        {"sequence": 4, "observer_id": "Hero", "source": "scan", "source_event_sequence": None,
         "facts": [], "entities": [{"id": "lever", "name": "归航拉杆", "kind": "item",
             "description": "刻度指向归航。"}], "labels": {}},
        {"sequence": 5, "observer_id": "Hero", "source": "continuity", "source_event_sequence": None,
         "facts": [], "entities": [{"id": "lever", "name": "归航拉杆", "kind": "item",
             "description": "不应保留的连续性重复。"}], "labels": {}},
        {"sequence": 6, "observer_id": "Other", "source": "event", "source_event_sequence": 2,
         "facts": [{"kind": "speech", "fields": {"content": "OTHER_ONLY"}}],
         "entities": [], "labels": {}},
    ]
    observations.extend({
        "sequence": 10 + index, "observer_id": "Hero", "source": "scan",
        "source_event_sequence": None, "facts": [],
        "entities": [{"id": "lever", "name": "归航拉杆", "kind": "item",
                      "description": None, "placement": {"parent_id": "room"}}],
        "labels": {},
    } for index in range(30))
    evidence = CampaignSession._compact_character_observations("Hero", observations, compiled)
    encoded = json.dumps(evidence, ensure_ascii=False)
    assert "我听见了线索" in encoded and "刻度指向归航" in encoded
    assert "OTHER_ONLY" not in encoded and "连续性重复" not in encoded
    assert evidence["visited_rooms"] == [{"id": "room", "name": "旧大厅"}]
    assert len(encoded) < len(json.dumps(observations, ensure_ascii=False)) / 3


def test_scene_builder_truncation_retries_without_partial_assistant_message(tmp_path, monkeypatch):
    captured = []
    marker = "PARTIAL_OUTPUT_MUST_NOT_BE_REPLAYED"

    class Backend:
        def complete(self, request):
            captured.append(request)
            if len(captured) == 1:
                return LLMResponse(content='{"unfinished":"' + marker,
                    finish_reason="length", usage=TokenUsage(completion_tokens=256))
            return LLMResponse(content=json.dumps(scenario(1), ensure_ascii=False))

    monkeypatch.setitem(BACKEND_FACTORIES, "truncated_scene", lambda config: Backend())
    raw_config = {
        "backends": {"test": {"driver": "truncated_scene", "base_url": "unused", "api_key_env": "UNUSED"}},
        "profiles": {"builder": {"backend_id": "test", "model": "fake", "max_output_tokens": 256}},
        "campaign": {"director_profile": "builder", "scene_builder_profile": "builder",
            "world_summary_profile": "builder", "character_memory_profile": "builder",
            "npc_profile": "builder", "max_generation_retries": 2},
    }
    config = RunConfig.model_validate(raw_config)
    session = CampaignSession(config, runs_dir=tmp_path)
    session.store = CampaignStore.create(tmp_path / "campaigns")
    session.llm = CampaignLLMService(config, session.store)
    session.state = CampaignState(
        campaign_id=session.store.path.name, protagonist_id="Hero",
        bible=CampaignBible(title="测试", public_world="世界", major_history=("旧事",),
            central_conflict="冲突", protagonist_id="Hero", protagonist_ties=("联系",),
            main_threads=("主线",), characters=(CampaignCharacter(
                id="Hero", name="Hero", description="主角", personality="谨慎", public_role="旅人",
                inner_life="我保持警惕。", historical_tie="经历旧事。"),)),
    )
    brief = ActBrief(act_number=1, title="第一幕", dramatic_purpose="建立", opening="抵达。",
                     player_goal="操作", cast_ids=("Hero",), required_entity_ids=("lever",))
    session._prepare_scene(brief, brief.opening, "act_ready")
    assert session.state.phase == "interlude"
    assert len(captured) == 2 and len(captured[1].messages) == 3
    assert marker not in "\n".join(message.content for message in captured[1].messages)
    errors = session._read_rows(session.store.path / "scene_validation_errors.jsonl")
    assert errors[0]["truncated"] is True and "truncated" in errors[0]["error"]


def test_debug_exchange_cursor_updates_pending_call_in_place(tmp_path, monkeypatch):
    entered, release = Event(), Event()

    class Backend:
        def complete(self, request):
            entered.set()
            assert release.wait(5)
            return LLMResponse(content='{"ok":true}', model="fake")

    monkeypatch.setitem(BACKEND_FACTORIES, "debug_pending", lambda config: Backend())
    raw_config = {
        "backends": {"test": {"driver": "debug_pending", "base_url": "unused", "api_key_env": "UNUSED"}},
        "profiles": {"test": {"backend_id": "test", "model": "fake"}},
        "campaign": {"director_profile": "test", "scene_builder_profile": "test",
            "world_summary_profile": "test", "character_memory_profile": "test", "npc_profile": "test"},
    }
    config = RunConfig.model_validate(raw_config)
    session = CampaignSession(config, runs_dir=tmp_path)
    session.store = CampaignStore.create(tmp_path / "campaigns")
    session.state = CampaignState(campaign_id=session.store.path.name, phase="creating_world")
    session.llm = CampaignLLMService(config, session.store)
    messages = [ChatMessage(role=ChatRole.USER, content="开始")]
    worker = Thread(target=lambda: session.llm.complete(
        "test", "director-genesis-e0-attempt-1", "director", messages))
    worker.start()
    assert entered.wait(5)
    first = session.debug_snapshot()
    pending, = first["exchanges"]
    assert pending["status"] == "pending" and pending["stage"] == "导演"
    release.set()
    worker.join(timeout=5)
    assert not worker.is_alive()
    second = session.debug_snapshot(first["cursor"])
    replied, = second["exchanges"]
    assert replied["id"] == pending["id"] and replied["status"] == "replied"
    assert session.debug_snapshot(second["cursor"])["exchanges"] == []


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
    payloads = {json.loads(request.messages[-1].content)["actor_id"]:
                json.loads(request.messages[-1].content) for request in captured}
    prompts = {actor_id: json.dumps(payload, ensure_ascii=False)
               for actor_id, payload in payloads.items()}
    assert "ALPHA_ONLY" in prompts["Hero"] and "BRAVO_ONLY" not in prompts["Hero"]
    assert "HERO_INTERLUDE" in prompts["Hero"] and "NIA_INTERLUDE" not in prompts["Hero"]
    assert "BRAVO_ONLY" in prompts["Nia"] and "ALPHA_ONLY" not in prompts["Nia"]
    assert "observation_evidence" in payloads["Hero"] and "observations" not in payloads["Hero"]


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
        assert player["act_background"] == "Hero 面对眼前的选择。"
        assert player["act_goal"] == "放下归航拉杆"
        debug = json.load(request("/api/debug"))
        assert debug["bible"] and debug["current_scenario"] and debug["director_messages"]
        assert debug["exchanges"] and all(row["status"] == "replied" for row in debug["exchanges"])
        delta = json.load(request(f"/api/debug?cursor={debug['cursor']}"))
        assert delta["exchanges"] == [] and delta["cursor"] == debug["cursor"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_campaign_frontend_has_quick_wait_checkboxes_and_persistent_context():
    root = (Path(__file__).resolve().parents[1]
            / "src/token_odyssey/interfaces/campaign_web/static")
    html = (root / "index.html").read_text(encoding="utf-8")
    script = (root / "app.js").read_text(encoding="utf-8")
    style = (root / "style.css").read_text(encoding="utf-8")
    assert "[hidden] { display:none!important; }" in style
    assert 'id="quick-wait"' in html and 'id="reference"' in html
    assert 'actions:[{kind:"wait"}]' in script
    assert 'type="checkbox"' in script and 'control.checked' in script
    assert 'select name="${field}" multiple' not in script
    assert "state.act_background" in script and "state.act_goal" in script
    assert "世界观与重大历史" in script
    assert 'id="developer-instruction"' in html
    assert 'api("/api/developer-instruction"' in script
    assert "exitNames" in script and "standalone" in script


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
