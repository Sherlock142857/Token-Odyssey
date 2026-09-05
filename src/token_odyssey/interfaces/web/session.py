"""A serial worker owns the runner; HTTP polling reads cached presentation data."""

import json
from copy import deepcopy
from pathlib import Path
from secrets import token_urlsafe
from threading import RLock, Thread

from pydantic import Field

from token_odyssey.agents.human import HumanAgent
from token_odyssey.common import FrozenModel
from token_odyssey.config.models import ParticipantConfig, RunConfig
from token_odyssey.kernel.actions.registry import builtin_registry
from token_odyssey.kernel.events import Fact
from token_odyssey.kernel.fluents import Fluents
from token_odyssey.recording import RunRecorder
from token_odyssey.recording.recorder import jsonable
from token_odyssey.recording.replay import replay_run
from token_odyssey.runtime.composition import build_participants, identity_for
from token_odyssey.runtime.runner import ActRunner, RunResult
from token_odyssey.translators.language import render_fact

from .presentation import ACTION_NAMES, action_catalog, event_text, issue_text


TERMINAL = {"completed", "limit_reached", "stopped", "failed"}


class StartOptions(FrozenModel):
    cast: dict[str, ParticipantConfig]
    rounds: int = Field(ge=1, le=1000)
    seed: int = Field(ge=0, le=2**32 - 1)
    auto: bool = True


class WebError(ValueError):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


class WebRecorder:
    def __init__(self, disk, publish):
        self.disk, self.publish = disk, publish

    def record(self, stream, value):
        self.disk.record(stream, value)
        self.publish(stream, jsonable(value))

    def finalize(self, **kwargs):
        self.disk.finalize(**kwargs)


class WebSession:
    """One shared local playtest, with hot-seat human controllers and no rescan on GET."""

    def __init__(self, scenario, config=None, *, runs_dir="runs", llm_timeout=60):
        self.scenario, self.config = scenario, config or RunConfig()
        self.runs_dir, self.llm_timeout = Path(runs_dir), llm_timeout
        self.registry = builtin_registry()
        self.lock = RLock()
        self.token = token_urlsafe(32)
        self.runner = None
        self.worker = None
        self.status = "idle"
        self.session_id = None
        self.version = 0
        self.pause_requested = self.stop_requested = False
        self.cast = {}
        self.requests, self.observations, self.results = {}, {}, {}
        self.known, self.world_log, self.usage = {}, [], {}
        self.routing = []
        self.pending = self.active_actor = self.error = self.report = None
        self.counts = {"turns": 0, "transactions": 0, "events": 0}
        self.labels = {obj.id: obj.name for obj in (*scenario.world.entities.values(), *scenario.world.passages.values())}

    def catalog(self):
        profiles = [{"id": key, "model": profile.model} for key, profile in self.config.profiles.items()]
        first_profile = next(iter(self.config.profiles), None)
        default_human = "seeker" if "seeker" in self.scenario.world.character_ids else self.scenario.world.character_ids[0]
        cast = {}
        for actor in self.scenario.world.character_ids:
            binding = self.config.cast.get(actor) or self.scenario.cast.get(actor)
            profile = binding.profile if binding and binding.profile else first_profile
            cast[actor] = {"adapter": "human" if actor == default_human else "llm" if profile else "scripted",
                           "profile": profile if actor != default_human and profile else None}
        return {"token": self.token, "scenario": {"id": self.scenario.id, "title": self.scenario.title,
                "background": self.scenario.public_background, "rounds": self.scenario.max_rounds,
                "seed": self.scenario.seed}, "characters": [{"id": actor, "name": self.labels[actor]}
                for actor in self.scenario.world.character_ids], "profiles": profiles,
                "default_cast": cast, "actions": action_catalog(self.registry)}

    def start(self, payload):
        options = StartOptions.model_validate(payload)
        if set(options.cast) != set(self.scenario.world.character_ids):
            raise WebError("请为场景中的每个角色选择控制方式。", 400)
        config = RunConfig(backends=self.config.backends, profiles=self.config.profiles, cast=options.cast)
        with self.lock:
            if self.status not in TERMINAL | {"idle"} or self._busy():
                raise WebError("当前 act 尚未结束，请先结束本次测试。")
            disk = RunRecorder(self.scenario, root=self.runs_dir, seed=options.seed)
            recorder = WebRecorder(disk, self._publish)
            try:
                participants = build_participants(self.scenario, config, self.registry, recorder=recorder)
                # Bound local web waiting time without altering the provider or CLI.
                for participant in participants.values():
                    backend = getattr(participant, "backend", None)
                    if backend is not None and hasattr(backend, "client"):
                        backend.client = backend.client.with_options(timeout=self.llm_timeout, max_retries=0)
            except Exception as exc:
                disk.finalize(state=self.scenario.initial_state, result=None, status="failed", error=type(exc).__name__)
                raise WebError(f"无法创建参与者（{type(exc).__name__}）。请检查启动时的模型配置和服务端凭据。", 400) from exc
            self.cast = options.cast
            self.options, self.disk = options, disk
            self.session_id = disk.run_dir.name
            self.requests, self.observations, self.results = {}, {}, {}
            self.known, self.world_log, self.usage = {}, [], {}
            self.routing = []
            self.pending = self.active_actor = self.error = self.report = None
            self.counts = {"turns": 0, "transactions": 0, "events": 0}
            self.pause_requested = self.stop_requested = False
            self.runner = ActRunner(self.scenario, participants, self.registry, seed=options.seed, recorder=recorder)
            self._launch(options.auto)
            return {"session_id": self.session_id}

    def _busy(self):
        return self.worker is not None and self.worker.is_alive()

    def command(self, operation, payload):
        with self.lock:
            if self.runner is None or payload.get("session_id") != self.session_id:
                raise WebError("运行已切换，请刷新后重试。")
            if self.status in TERMINAL:
                raise WebError("本次 act 已结束，请开始新测试。")
            if operation == "pause":
                self.pause_requested = True
                self.version += 1
                return {}
            if operation == "stop":
                self.stop_requested = True
                self.version += 1
                if not self._busy():
                    self._launch(False)
                return {}
            if self._busy():
                raise WebError("正在处理一个回合，请等待处理完成。")
            if operation == "submit":
                if self.status != "waiting_for_input" or not self.pending:
                    raise WebError("当前没有等待提交的人类回合。")
                actor_id = payload.get("actor_id")
                if actor_id != self.pending["actor_id"]:
                    raise WebError("当前应由另一位角色行动。")
                actions = payload.get("actions")
                if not isinstance(actions, list) or not 1 <= len(actions) <= self.scenario.turn_policy.max_actions:
                    raise WebError("动作队列为空或超过本回合上限。", 400)
                try:
                    self.runner.participants[actor_id].submit(payload.get("request_id"), actions)
                except ValueError as exc:
                    raise WebError(str(exc), 400) from exc
                self.pending = None
            elif operation != "advance":
                raise WebError("未知操作。", 404)
            elif self.status == "waiting_for_input":
                raise WebError("请先提交当前角色的动作。")
            self.pause_requested = False
            self._launch(payload.get("auto", True) is True)
            return {}

    def _launch(self, auto):
        self.status = "running"
        self.version += 1
        self.worker = Thread(target=self._drive, args=(auto,), daemon=True, name="act-playtest")
        self.worker.start()

    def _publish(self, stream, row):
        with self.lock:
            if stream == "routing":
                self.active_actor = row["actor_id"]
                self.routing.append(row)
                self.routing = self.routing[-20:]
            elif stream == "requests":
                actor = row["actor_id"]
                if self.cast[actor].adapter == "human":
                    self.requests[actor] = row
                    known = self.known.setdefault(actor, {})
                    for obs in row["view"]["observations"]:
                        self._remember(known, obs)
                    for entity in (*row["view"]["inventory"], *row["view"]["items"], *row["view"]["characters"]):
                        self._remember(known, {"entities": [entity]})
            elif stream == "observations":
                actor = row["observer_id"]
                if self.cast[actor].adapter == "human":
                    self.observations.setdefault(actor, []).append(row)
                    self._remember(self.known.setdefault(actor, {}), row)
            elif stream == "action_results":
                actor = self.active_actor
                row = {**row, "actor_id": actor, "after_event_sequence": len(self.runner.events),
                       "messages": [issue_text(i) for i in row["issues"] + row["notices"]]}
                self.results.setdefault(actor, []).append(row)
                if not row["accepted"] or row["notices"]:
                    self.world_log.append({"type": "feedback", "actor_id": actor, "request_id": row["request_id"],
                        "text": f"{self.labels[actor]} · {ACTION_NAMES.get(row['kind'], row['kind'])}：" +
                        " ".join(issue_text(i) for i in row["issues"] + row["notices"]), "accepted": row["accepted"]})
            elif stream == "fallbacks":
                self.world_log.append({"type": "feedback", "actor_id": row["actor_id"], "accepted": False,
                    "text": f"{self.labels[row['actor_id']]}：修正次数已用尽，本回合自动等待。"})
            elif stream == "events":
                self.world_log.append({"type": "event", "text": event_text(row, self.labels),
                                      "event": row})
            elif stream == "llm_exchanges" and row.get("response"):
                usage = self.usage.setdefault(row["actor_id"], {})
                for key, value in row["response"]["usage"].items():
                    usage[key] = usage.get(key, 0) + value
            self.version += 1

    @staticmethod
    def _remember(known, observation):
        for entity in observation.get("entities", []):
            previous = known.get(entity["id"], {})
            known[entity["id"]] = {**entity, "description": entity.get("description") or previous.get("description")}

    def _drive(self, auto):
        try:
            while True:
                with self.lock:
                    stopped = self.stop_requested
                if stopped:
                    status = "stopped"
                    break
                if self.runner.goals_met:
                    status = "completed"
                    break
                if self.runner.turns_completed >= self.options.rounds * len(self.cast):
                    status = "limit_reached"
                    break
                status = self.runner.step()
                with self.lock:
                    self._update_counts()
                    self.version += 1
                    if self.stop_requested:
                        status = "stopped"
                        break
                    if status in {"completed", "waiting_for_input"}:
                        break
                    if self.runner.turns_completed >= self.options.rounds * len(self.cast):
                        status = "limit_reached"
                        break
                    if self.pause_requested or not auto:
                        status = "paused"
                        break
            self._checkpoint(status)
            with self.lock:
                if status == "waiting_for_input":
                    participant = self.runner.participants[self.runner.pending.request.actor_id]
                    assert isinstance(participant, HumanAgent)
                    self.pending = participant.present()
                else:
                    self.pending = None
                self.status = status
                self.version += 1
        except Exception as exc:
            with self.lock:
                self._update_counts()
                self.pending = None
                self.error = f"运行中断（{type(exc).__name__}）。已提交的动作保留在运行目录；请检查模型连接或服务端配置后开始新测试。"
            try:
                self.disk.finalize(state=self.runner.harness.world.state, result=None,
                                   status="failed", error=type(exc).__name__)
            finally:
                with self.lock:
                    self.status = "failed"
                    self.version += 1

    def _update_counts(self):
        self.counts = {"turns": self.runner.turns_completed,
                       "transactions": len(self.runner.harness.world_log), "events": len(self.runner.events)}

    def _checkpoint(self, status):
        result = RunResult(status=status, turns_completed=self.runner.turns_completed,
            rounds_completed=self.runner.turns_completed // len(self.cast),
            transactions=len(self.runner.harness.world_log), events=len(self.runner.events),
            goals_met=self.runner.goals_met)
        self.disk.finalize(state=self.runner.harness.world.state, result=result, status=status)
        if status in TERMINAL:
            fluent = Fluents(self.runner.harness.world)
            expected = [{"condition": atom.model_dump(mode="json"), "met": fluent.satisfies(atom)}
                        for atom in self.scenario.expected]
            replay = replay_run(self.disk.run_dir)
            report = {"mode": "web", "success": status == "completed" and all(x["met"] for x in expected) and replay.success,
                      "result": result.model_dump(mode="json"), "expected": expected,
                      "replay_matches": replay.success, "run_dir": str(self.disk.run_dir)}
            (self.disk.run_dir / "acceptance.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            with self.lock:
                self.report = report

    def snapshot(self, actor_id=None):
        with self.lock:
            human_ids = [actor for actor, binding in self.cast.items() if binding.adapter == "human"]
            if actor_id is not None and actor_id not in human_ids:
                raise WebError("仅可查看本地人类角色的玩家视角。", 403)
            selected = actor_id or (self.pending["actor_id"] if self.pending else next(iter(human_ids), None))
            request = self.requests.get(selected)
            labels = {}
            lines = []
            for obs in self.observations.get(selected, []):
                labels.update(obs["labels"])
                labels.update({entity["id"]: entity["name"] for entity in obs["entities"]})
                if obs["source"] == "event":
                    texts = list(dict.fromkeys(render_fact(Fact.model_validate(fact), labels) for fact in obs["facts"]))
                    lines.append({"id": obs["sequence"], "event_sequence": obs["source_event_sequence"],
                                  "revision": obs["world_revision"], "texts": texts})
            feedback = []
            if request:
                feedback = [issue_text(x) for x in request["view"]["feedback"] + request["issues"]]
            response = {"session_id": self.session_id, "version": self.version, "status": self.status,
                "busy": self._busy(), "counts": self.counts, "rounds": self.options.rounds if self.runner else None,
                "cast": {actor: binding.model_dump() for actor, binding in self.cast.items()},
                "active_actor": self.active_actor, "selected_actor": selected, "human_ids": human_ids,
                "pending": self.pending if self.pending and self.pending["actor_id"] == selected else None,
                "request": request, "known_entities": list(self.known.get(selected, {}).values()),
                "identity": identity_for(self.scenario, selected).model_dump(mode="json") if selected else None,
                "observations": lines, "feedback": feedback, "action_results": self.results.get(selected, []),
                "error": self.error, "pause_requested": self.pause_requested, "stop_requested": self.stop_requested,
                "run_dir": str(self.disk.run_dir) if self.runner else None,
                "result": self.report["result"] if self.report else None}
            return deepcopy(response)

    def observer_snapshot(self):
        with self.lock:
            return deepcopy({"session_id": self.session_id, "version": self.version,
                "entries": self.world_log, "report": self.report, "usage": self.usage, "labels": self.labels,
                "routing": self.routing})
