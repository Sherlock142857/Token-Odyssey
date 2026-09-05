import json
from pathlib import Path
from threading import Event, Thread
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from token_odyssey.config.models import RunConfig
from token_odyssey.interfaces.web.server import create_server
from token_odyssey.interfaces.web.session import WebError, WebSession
from token_odyssey.runtime.composition import BACKEND_FACTORIES
from token_odyssey.scenario import load_scenario
from token_odyssey.verification import ScriptedResponseBackend, run_acceptance


SCENARIO = Path(__file__).resolve().parents[1] / "scenarios/sealed_chalice.yaml"


def options(session, *, all_human=False, rounds=12, auto=True):
    return {"cast": {actor: {"adapter": "human" if actor == "seeker" or all_human else "scripted"}
                     for actor in session.scenario.world.character_ids},
            "rounds": rounds, "seed": 19, "auto": auto}


def settled(session):
    session.worker.join(timeout=10)
    assert not session.worker.is_alive(), "web worker did not finish"
    return session.snapshot()


def submit(session, state, actions, **kwargs):
    return session.command("submit", {"session_id": state["session_id"], "request_id": state["pending"]["request_id"],
        "actor_id": state["pending"]["actor_id"], "actions": actions, **kwargs})


@pytest.fixture
def session(tmp_path):
    return WebSession(load_scenario(SCENARIO), runs_dir=tmp_path / "web")


@pytest.mark.parametrize("use_llm", [False, True])
def test_complete_human_act_matches_existing_run_and_replay(session, tmp_path, monkeypatch, use_llm):
    settings = options(session)
    if use_llm:
        config = {"backends": {}, "profiles": {}}
        for actor in ("keeper", "witness"):
            config["backends"][actor] = {"driver": "web_test", "base_url": actor, "api_key_env": "UNUSED"}
            config["profiles"][actor] = {"backend_id": actor, "model": "offline-api-response"}
            settings["cast"][actor] = {"adapter": "llm", "profile": actor}
        session.config = RunConfig.model_validate(config)
        monkeypatch.setitem(BACKEND_FACTORIES, "web_test", lambda config: ScriptedResponseBackend(session.scenario.scripts.get(config.base_url, ())))
    session.start(settings)
    for batch in session.scenario.scripts["seeker"]:
        state = settled(session)
        assert state["status"] == "waiting_for_input", state
        assert state["pending"]["actor_id"] == "seeker"
        before = (session.runner.observation.rng.getstate(), len(session.runner.observation.log), session.runner.turns_completed)
        for _ in range(5):
            session.snapshot()
            session.observer_snapshot()
        assert before == (session.runner.observation.rng.getstate(), len(session.runner.observation.log), session.runner.turns_completed)
        submit(session, state, batch["actions"])
    state = settled(session)
    assert state["status"] == "completed"
    report = session.observer_snapshot()["report"]
    assert report["success"] and report["replay_matches"]
    assert all(condition["met"] for condition in report["expected"])
    assert any(not result["accepted"] for result in state["action_results"])
    assert (session.disk.run_dir / "acceptance.json").is_file()
    if use_llm:
        assert (session.disk.run_dir / "llm_exchanges.jsonl").is_file()
    baseline = run_acceptance(SCENARIO, root=tmp_path / "baseline")
    for filename in ("final_state.json", "observations.jsonl", "views.jsonl", "transactions.jsonl"):
        assert (session.disk.run_dir / filename).read_bytes() == (Path(baseline.run_dir) / filename).read_bytes()


def test_player_dto_does_not_expose_hidden_world_or_other_private_goals(session):
    session.start(options(session))
    state = settled(session)
    encoded = json.dumps(state, ensure_ascii=False)
    assert "chalice_seated" not in encoded
    assert "activate_beacon" not in encoded
    assert "陶瓷继电组件" not in encoded
    assert session.scenario.roles["keeper"].private_goal not in encoded
    assert "expected" not in state and "world_log" not in state
    with pytest.raises(WebError, match="人类角色"):
        session.snapshot("keeper")
    # Bootstrap exposes public cast/profile labels, never backend config or role secrets.
    catalog = session.catalog()
    assert "backends" not in catalog
    assert "roles" not in catalog["scenario"]


def test_invalid_stale_and_duplicate_submissions_do_not_advance(session):
    session.start(options(session))
    state = settled(session)
    turns = state["counts"]["turns"]
    for actions in ([], [{"kind": "wait"}] * 6, [{"kind": "invalid"}]):
        with pytest.raises(WebError):
            submit(session, state, actions)
        assert session.runner.turns_completed == turns
    with pytest.raises(WebError, match="stale"):
        session.command("submit", {"session_id": state["session_id"], "actor_id": "seeker", "request_id": "old", "actions": [{"kind": "wait"}]})
    with pytest.raises(WebError, match="另一位"):
        session.command("submit", {"session_id": state["session_id"], "actor_id": "keeper", "actions": [{"kind": "wait"}]})
    submit(session, state, [{"kind": "wait"}], auto=False)
    assert settled(session)["status"] == "paused"
    with pytest.raises(WebError):
        submit(session, state, [{"kind": "wait"}])


def test_rejected_action_allows_correction_with_new_request_id(session):
    session.start(options(session))
    first = settled(session)
    submit(session, first, [{"kind": "take", "item_id": "unknown-secret"}])
    second = settled(session)
    assert second["status"] == "waiting_for_input"
    assert second["counts"] == first["counts"]
    assert second["pending"]["request_id"] != first["pending"]["request_id"]
    assert second["pending"]["issues"][0]["code"] == "UNKNOWN_TO_ACTOR"
    submit(session, second, [{"kind": "wait"}], auto=False)
    assert settled(session)["counts"]["turns"] == first["counts"]["turns"] + 1


def test_hotseat_pause_limit_and_new_run_rejects_old_session(session):
    session.start(options(session, all_human=True, rounds=1))
    for _ in range(3):
        state = settled(session)
        assert state["selected_actor"] == state["pending"]["actor_id"]
        submit(session, state, [{"kind": "wait"}])
    old = settled(session)
    assert old["status"] == "limit_reached"
    assert session.observer_snapshot()["report"]["replay_matches"]
    assert not session.observer_snapshot()["report"]["success"]
    session.start(options(session))
    settled(session)
    with pytest.raises(WebError, match="运行已切换"):
        session.command("stop", {"session_id": old["session_id"]})
    session.command("stop", {"session_id": session.session_id})
    assert settled(session)["status"] == "stopped"


def test_slow_llm_does_not_block_polling_or_allow_concurrent_actions(session, monkeypatch):
    entered, release = Event(), Event()

    class SlowBackend:
        def complete(self, request):
            entered.set()
            assert release.wait(5)
            raise RuntimeError("secret must not appear in browser")

    monkeypatch.setitem(BACKEND_FACTORIES, "slow", lambda config: SlowBackend())
    session.config = RunConfig.model_validate({"backends": {"slow": {"driver": "slow", "base_url": "unused", "api_key_env": "UNUSED"}},
        "profiles": {"slow": {"backend_id": "slow", "model": "slow"}}})
    settings = options(session)
    settings["cast"] = {actor: {"adapter": "llm", "profile": "slow"} for actor in settings["cast"]}
    session.start(settings)
    try:
        assert entered.wait(5)
        assert session.snapshot()["status"] == "running"
        with pytest.raises(WebError, match="正在处理"):
            session.command("advance", {"session_id": session.session_id})
        session.command("pause", {"session_id": session.session_id})
        assert session.snapshot()["pause_requested"]
    finally:
        release.set()
    state = settled(session)
    assert state["status"] == "failed"
    assert "secret" not in state["error"]
    manifest = json.loads((session.disk.run_dir / "manifest.json").read_text())
    assert manifest["status"] == "failed"


def test_http_boundary_and_refresh(session):
    server = create_server(session, port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    opener = build_opener(ProxyHandler({}))

    def request(path, payload=None, headers=None):
        body = None if payload is None else json.dumps(payload).encode()
        return opener.open(Request(base + path, data=body, headers=headers or {}), timeout=5)

    try:
        with request("/") as response:
            assert "安排动作" in response.read().decode()
            assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
        catalog = json.load(request("/api/catalog"))
        with pytest.raises(HTTPError) as denied:
            request("/api/start", options(session), {"Content-Type": "application/json"})
        assert denied.value.code == 403
        headers = {"Content-Type": "application/json", "X-Playtest-Token": catalog["token"]}
        with pytest.raises(HTTPError) as denied:
            request("/api/start", options(session), {**headers, "Origin": "http://evil.example"})
        assert denied.value.code == 403
        with pytest.raises(HTTPError) as denied:
            request("/api/state", headers={"Host": "evil.example"})
        assert denied.value.code == 403
        request("/api/start", options(session), headers).close()
        first = settled(session)
        for _ in range(3):
            second = json.load(request("/api/state"))
            assert second["pending"] == first["pending"]
            assert second["counts"] == first["counts"]
        for path in ("/api.txt", "/../api.txt", "/runs/manifest.json"):
            with pytest.raises(HTTPError) as missing:
                request(path)
            assert missing.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_brief_is_available_before_first_turn_and_hotseat_pending_stays_private(session):
    session.start(options(session, all_human=True))
    state = settled(session)
    other = next(actor for actor in session.scenario.world.character_ids if actor != state["selected_actor"])
    before_turn = session.snapshot(other)
    assert before_turn["request"] is None and before_turn["pending"] is None
    identity = before_turn["identity"]
    assert identity["actor_id"] == other
    assert identity["act_title"] == session.scenario.title
    assert identity["description"] == session.scenario.world.entities[other].description
    assert identity["private_goal"] == session.scenario.roles[other].private_goal
    assert "routing" not in state
