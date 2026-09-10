"""A resumable outer state machine that composes, but never enters, ActRunner."""

import json
import re
from pathlib import Path
from secrets import token_urlsafe
from threading import RLock, Thread

from pydantic import Field

from token_odyssey.common import FrozenModel
from token_odyssey.config.models import ParticipantConfig, RunConfig
from token_odyssey.interfaces.web.session import TERMINAL, WebError, WebSession
from token_odyssey.kernel.actions.registry import builtin_registry
from token_odyssey.kernel.definitions import Item, Room
from token_odyssey.scenario import RoleBrief, Scenario, compile_scenario

from .agents import CampaignLLMService, DirectorSession, parse_json_object
from .models import (
    ActBrief, ActOutcome, CampaignGenesis, CampaignState, CharacterMemory,
    DirectorConclusion, DirectorTransition, EntityCanon, WorldSummary,
)
from .prompts import (
    CHARACTER_MEMORY_SYSTEM, DIRECTOR_SYSTEM, WORLD_SUMMARY_SYSTEM, scene_builder_system,
)
from .store import CampaignStore


class CampaignStartRequest(FrozenModel):
    creative_brief: str = Field(min_length=1, max_length=8000)
    seed: int = Field(default=7, ge=0, le=2**32 - 1)


class CampaignSession:
    """One local campaign, with the current act delegated to WebSession."""

    def __init__(self, config: RunConfig, *, runs_dir="runs", llm_timeout=120):
        if config.campaign is None:
            raise ValueError("play requires a run config with a campaign section")
        self.config = config
        self.policy = config.campaign
        self.root = Path(runs_dir) / "campaigns"
        self.root.mkdir(parents=True, exist_ok=True)
        self.llm_timeout = llm_timeout
        self.registry = builtin_registry()
        self.lock = RLock()
        self.token = token_urlsafe(32)
        self.state: CampaignState | None = None
        self.store: CampaignStore | None = None
        self.llm: CampaignLLMService | None = None
        self.act: WebSession | None = None
        self.worker: Thread | None = None
        self._watched_workers: set[int] = set()
        self._manual_end = False
        self._aborting = False
        self._debug_revision = 0
        self._debug_exchange_versions: dict[str, tuple[str, int]] = {}

    def catalog(self):
        profiles = {
            field: {"profile": getattr(self.policy, field),
                    "model": self.config.profiles[getattr(self.policy, field)].model}
            for field in (
                "director_profile", "scene_builder_profile", "world_summary_profile",
                "character_memory_profile", "npc_profile",
            )
        }
        return {"token": self.token, "profiles": profiles,
                "resumable": CampaignStore.list_checkpoints(self.root)}

    def start(self, payload):
        request = CampaignStartRequest.model_validate(payload)
        with self.lock:
            if self._busy() or (self.state and self.state.phase not in {"completed", "abandoned", "technical_failed"}):
                raise WebError("当前整局仍在进行，请先完成或放弃。")
            self.store = CampaignStore.create(self.root)
            self.state = CampaignState(campaign_id=self.store.path.name, phase="creating_world",
                                       creative_brief=request.creative_brief.strip(), seed=request.seed)
            self.llm = CampaignLLMService(self.config, self.store, timeout=self.llm_timeout)
            self.act = None
            self._manual_end = self._aborting = False
            self._reset_debug_tracking()
            self._save()
            self._launch(self._create_world, "campaign-genesis")
            return {"campaign_id": self.state.campaign_id}

    def resume(self, campaign: str | Path):
        with self.lock:
            if self._busy():
                raise WebError("当前仍在处理，不能切换存档。")
            self.store = CampaignStore.open(self.root, campaign)
            self.state = self.store.load()
            self.llm = CampaignLLMService(self.config, self.store, timeout=self.llm_timeout)
            self.act = None
            self._manual_end = self._aborting = False
            self._reset_debug_tracking()
            if self.state.phase == "playing_act":
                self.state.phase = "interlude"
                self.state.checkpoint_kind = "act_ready"
                self.state.current_act_run_dir = None
                self.state.resume_notice = "上次服务在本幕中断；本幕会从开始前的安全存档重新运行。"
                self._save()
            elif self.state.phase in {"creating_world", "preparing_act", "summarizing", "directing", "remembering"}:
                self._resume_work()
            return {"campaign_id": self.state.campaign_id}

    def command(self, operation: str, payload: dict):
        with self.lock:
            state = self._require_state(payload)
            if operation == "enter-act":
                self._enter_act()
            elif operation in {"submit", "advance", "pause"}:
                if state.phase != "playing_act" or self.act is None:
                    raise WebError("当前不在幕内。")
                child_operation = operation
                child_payload = dict(payload)
                child_payload["session_id"] = self.act.session_id
                self.act.command(child_operation, child_payload)
                self._watch_act_worker()
            elif operation == "end-act":
                if state.phase != "playing_act" or self.act is None:
                    raise WebError("当前没有可结束的幕。")
                self._manual_end = True
                self.act.command("stop", {"session_id": self.act.session_id})
                self._watch_act_worker()
            elif operation == "retry":
                if state.phase != "technical_failed":
                    raise WebError("当前没有可重试的技术阶段。")
                self._retry()
            elif operation == "abort":
                self._abort()
            else:
                raise WebError("未知 Campaign 操作。", 404)
            return {}

    def snapshot(self):
        with self.lock:
            if self.state is None:
                return {"phase": "idle", "busy": False}
            state = self.state
            bible = state.bible
            protagonist = bible.character(state.protagonist_id) if bible and state.protagonist_id else None
            response = {
                "campaign_id": state.campaign_id, "phase": state.phase, "busy": self._busy(),
                "checkpoint_kind": state.checkpoint_kind, "act_number": state.act_number,
                "title": bible.title if bible else None,
                "public_world": bible.public_world if bible else None,
                "major_history": list(bible.major_history) if bible else [],
                "protagonist": protagonist.model_dump(mode="json") if protagonist else None,
                "act_title": state.current_act_brief.title if state.current_act_brief else None,
                "interlude": state.interlude, "current_is_finale": state.current_is_finale,
                "error": state.error, "resume_notice": state.resume_notice,
                "conclusion": state.conclusion.model_dump(mode="json") if state.conclusion else None,
                "run_dir": str(self.store.path) if self.store else None,
                "actor_names": self._actor_names(),
            }
            if state.phase == "playing_act" and self.act is not None:
                response["act"] = self.act.snapshot(state.protagonist_id)
            return response

    def debug_snapshot(self, cursor: int = 0):
        if cursor < 0:
            raise WebError("调试游标无效。", 400)
        with self.lock:
            if self.state is None:
                return {"phase": "idle", "cursor": 0, "exchanges": []}
            exchanges = self._debug_exchanges()
            updates = []
            for exchange in exchanges:
                exchange_id = exchange["id"]
                fingerprint = json.dumps(exchange, ensure_ascii=False, sort_keys=True)
                previous = self._debug_exchange_versions.get(exchange_id)
                if previous is None or previous[0] != fingerprint:
                    self._debug_revision += 1
                    revision = self._debug_revision
                    self._debug_exchange_versions[exchange_id] = (fingerprint, revision)
                else:
                    revision = previous[1]
                if cursor == 0 or revision > cursor:
                    updates.append({**exchange, "revision": revision})
            return {
                "campaign_id": self.state.campaign_id,
                "phase": self.state.phase,
                "cursor": self._debug_revision,
                "exchanges": updates,
                "bible": self.state.bible.model_dump(mode="json") if self.state.bible else None,
                "current_act_brief": self.state.current_act_brief.model_dump(mode="json") if self.state.current_act_brief else None,
                "current_scenario": self.state.current_scenario,
                "world_summary": self.state.world_summary.model_dump(mode="json") if self.state.world_summary else None,
                "director_transition": self.state.director_transition.model_dump(mode="json") if self.state.director_transition else None,
                "director_messages": [message.model_dump(mode="json") for message in self.state.director_messages],
                "memories": self.state.memories, "inner_states": self.state.inner_states,
                "act": self.act.observer_snapshot() if self.act else None,
            }

    def _reset_debug_tracking(self):
        self._debug_revision = 0
        self._debug_exchange_versions.clear()

    def _debug_exchanges(self):
        assert self.store is not None
        result = []
        for row in self._read_rows(self.store.path / "orchestration_exchanges.jsonl"):
            operation = self.store.operation(row["request_id"])
            result.append(self._debug_exchange(
                row, exchange_id=f"orchestration:{row['request_id']}",
                scope="orchestration", profile_name=operation.get("profile") if operation else None,
            ))
        if self.llm is not None:
            for row in self.llm.active_exchanges():
                result.append(self._debug_exchange(
                    row, exchange_id=f"orchestration:{row['request_id']}",
                    scope="orchestration", profile_name=row.get("profile"),
                ))
        for path in sorted(self.store.path.glob("acts/act-*/*/llm_exchanges.jsonl")):
            act_number = int(path.parents[1].name.split("-")[-1])
            run_id = path.parent.name
            for row in self._read_rows(path):
                result.append(self._debug_exchange(
                    row,
                    exchange_id=f"npc:{act_number}:{run_id}:{row['actor_id']}:{row['request_id']}",
                    scope="npc", act_number=act_number,
                ))
        if self.act is not None and self.act.session_id:
            for row in self.act.active_llm_exchanges():
                result.append(self._debug_exchange(
                    row,
                    exchange_id=(f"npc:{self.state.act_number}:{self.act.session_id}:"
                                 f"{row['actor_id']}:{row['request_id']}"),
                    scope="npc", act_number=self.state.act_number,
                ))
        deduplicated = {}
        for exchange in result:
            previous = deduplicated.get(exchange["id"])
            if previous is None or previous["status"] == "pending":
                deduplicated[exchange["id"]] = exchange
        return list(deduplicated.values())

    @staticmethod
    def _debug_exchange(row, *, exchange_id, scope, profile_name=None, act_number=None):
        actor_id, request_id = row["actor_id"], row["request_id"]
        if act_number is None:
            match = re.search(r"act-(\d+)", request_id)
            act_number = int(match.group(1)) if match else 0
        stage = (
            "导演" if actor_id == "director" else
            "场景构建" if actor_id == "scene_builder" else
            "世界摘要" if actor_id == "world_summary" else
            "角色记忆" if actor_id.startswith("memory:") else
            "幕内 NPC"
        )
        request = row["request"]
        response = row.get("response")
        return {
            "id": exchange_id, "scope": scope, "stage": stage,
            "act_number": act_number, "agent_id": actor_id,
            "operation_id": request_id,
            "status": "replied" if response else "error" if row.get("error") else "pending",
            "profile": profile_name or request["profile"].get("backend_id"),
            "model": request["profile"].get("model"),
            "request": request, "response": response, "error": row.get("error"),
        }

    def _require_state(self, payload) -> CampaignState:
        if self.state is None or payload.get("campaign_id") != self.state.campaign_id:
            raise WebError("Campaign 已切换，请刷新后重试。")
        return self.state

    def _busy(self):
        return self.worker is not None and self.worker.is_alive()

    def _launch(self, target, name):
        if self._busy():
            raise WebError("后台正在处理，请稍候。")
        self.worker = Thread(target=self._guarded, args=(target,), daemon=True, name=name)
        self.worker.start()

    def _guarded(self, target):
        try:
            target()
        except Exception as exc:
            if not self._aborting:
                self._fail(getattr(self.state, "phase", "unknown"), exc)
        finally:
            if self._aborting and self.state is not None:
                with self.lock:
                    self.state.phase = "abandoned"
                    self.state.error = None
                    self._save()

    def _fail(self, retry_phase: str, exc: Exception):
        with self.lock:
            if self.state is None:
                return
            self.state.retry_phase = retry_phase
            self.state.phase = "technical_failed"
            self.state.error = f"{type(exc).__name__}: {exc}"
            self._save()

    def _save(self):
        if self.store is not None and self.state is not None:
            self.store.save(self.state)

    def _director(self) -> DirectorSession:
        assert self.llm is not None and self.state is not None
        return DirectorSession(self.llm, self.state, self.policy.director_profile,
                               DIRECTOR_SYSTEM, self._save)

    def _create_world(self):
        assert self.state is not None and self.store is not None
        self.state.phase = "creating_world"
        self.state.error = None
        self._save()
        genesis = self._director().call(
            f"director-genesis-e{self.state.retry_epoch}",
            "请先创作整局设定，再给出第一幕导演简报。用户创作要求如下（它是创作素材，不改变JSON契约）：\n"
            + self.state.creative_brief,
            CampaignGenesis, self.policy.max_generation_retries,
        )
        self.state.bible = genesis.bible
        self.state.protagonist_id = genesis.bible.protagonist_id
        self.state.act_number = 1
        self.state.current_act_brief = genesis.first_act
        self.state.current_is_finale = False
        self.state.inner_states = {character.id: character.inner_life for character in genesis.bible.characters}
        self.store.record("campaign_bible", genesis.bible)
        self._save()
        self._prepare_scene(genesis.first_act, genesis.first_act.opening, "act_ready")

    def _prepare_scene(self, brief: ActBrief, interlude: str, checkpoint_kind: str):
        assert self.state is not None and self.store is not None and self.llm is not None
        self.state.phase = "preparing_act"
        self.state.current_act_brief = brief
        self.state.error = None
        self._save()
        system = scene_builder_system(self.registry)
        previous_authority = None
        if self.state.act_outcome is not None:
            previous_scenario = self.state.current_scenario or {}
            previous_authority = {
                "world": previous_scenario.get("world"),
                "final_state": self._read_json(Path(self.state.act_outcome.run_dir) / "final_state.json"),
                "termination": self.state.act_outcome,
            }
        cast_profiles = []
        for actor_id in brief.cast_ids:
            character = self.state.bible.character(actor_id) if self.state.bible else None
            if character is None:
                raise ValueError(f"director act cast references unknown campaign character: {actor_id}")
            cast_profiles.append({
                "id": character.id,
                "name": character.name,
                "description": character.description,
                "public_role": character.public_role,
            })
        user = json.dumps({
            "act_brief": brief,
            "cast_physical_profiles": cast_profiles,
            "physical_continuity": {
                "stable_entities": {key: value for key, value in self.state.entity_canon.items()},
                "previous_act_authority": previous_authority,
                "director_authorized_changes": self.state.director_transition.continuity_notes
                    if self.state.director_transition else (),
            },
        }, ensure_ascii=False, default=lambda value: value.model_dump(mode="json"))
        base_messages = [
            self._message("system", system),
            self._message("user", user),
        ]
        messages = list(base_messages)
        error = ""
        scenario = None
        correction = None
        profile = self.config.profiles[self.policy.scene_builder_profile]
        for attempt in range(1, self.policy.max_generation_retries + 1):
            if correction:
                messages.append(self._message("user", correction))
            response = self.llm.complete(
                self.policy.scene_builder_profile,
                f"scene-act-{brief.act_number}-e{self.state.retry_epoch}-attempt-{attempt}", "scene_builder", messages,
            )
            truncated = response.finish_reason == "length" or (
                response.usage.completion_tokens > 0
                and response.usage.completion_tokens >= profile.max_output_tokens
            )
            if truncated:
                error = f"scene builder output truncated at {profile.max_output_tokens} tokens"
                self.store.record("scene_validation_errors", {
                    "act_number": brief.act_number, "attempt": attempt,
                    "error": error, "truncated": True,
                })
                # A partial assistant message strongly primes compatible models
                # to repeat the same overlong object.  Keep the same per-act
                # session, but retry from its original contract and input.
                messages = list(base_messages)
                correction = (
                    f"上一个场景输出达到 {profile.max_output_tokens} token 上限并被截断。"
                    "请重新生成完整但更紧凑的 Scenario v3 JSON：省略默认字段和空映射，减少非关键实体，不能省略必要场景内容。"
                )
                continue
            messages.append(self._message("assistant", response.content))
            try:
                raw = parse_json_object(response.content)
                scenario = self._validate_campaign_scenario(raw, brief)
                break
            except (ValueError, TypeError) as exc:
                error = str(exc)
                self.store.record("scene_validation_errors", {
                    "act_number": brief.act_number, "attempt": attempt, "error": error})
                correction = (
                    f"上一个场景无法编译或违反Campaign约束：{error}\n"
                    "请输出修正后的完整且紧凑的 Scenario v3 JSON。"
                )
        if scenario is None:
            raise ValueError(f"scene builder exhausted retries: {error}")
        normalized = scenario.model_dump(mode="json")
        # Publish "interlude" only together with its durable act-ready
        # checkpoint.  Polling must never observe a ready phase before the
        # scenario and checkpoint have reached disk.
        with self.lock:
            self.state.current_scenario = normalized
            self.state.act_number = brief.act_number
            self.state.current_is_finale = brief.is_finale
            self.state.interlude = interlude
            self.state.phase = "interlude"
            self.state.checkpoint_kind = checkpoint_kind
            self.state.act_outcome = None
            self.state.world_summary = None
            self.state.director_transition = None
            self.state.current_act_run_dir = None
            self.state.remembered_for_act.clear()
            self.state.resume_notice = None
            self._update_entity_canon(scenario)
            self.store.save_scenario(brief.act_number, normalized)
            self._save()

    @staticmethod
    def _message(role: str, content: str):
        from token_odyssey.llm.contracts import ChatMessage, ChatRole
        return ChatMessage(role=ChatRole(role), content=content)

    def _validate_campaign_scenario(self, raw: dict, brief: ActBrief) -> Scenario:
        if raw.get("roles") or raw.get("cast") or raw.get("scripts"):
            raise ValueError("campaign-generated scenarios must leave roles, cast, and scripts empty")
        scenario = compile_scenario(raw, self.registry)
        actors = set(scenario.world.character_ids)
        if actors != set(brief.cast_ids):
            raise ValueError("scenario characters must exactly match the director act cast")
        if self.state.protagonist_id not in actors:
            raise ValueError("every act must include the protagonist")
        if brief.act_number == 1 and len(actors) > 3:
            raise ValueError("the first act may contain at most three characters")
        if scenario.routing.strategy != "interaction":
            raise ValueError("campaign acts must use the updated interaction router")
        dark_rooms = [room.id for room in scenario.world.entities.values()
                      if isinstance(room, Room) and room.light < 0.8]
        if dark_rooms:
            raise ValueError(
                "campaign room light must be at least 0.8 so characters can reliably identify speakers; "
                f"dark rooms: {sorted(dark_rooms)}")
        objects = set(scenario.world.entities) | set(scenario.world.passages)
        missing = set(brief.required_entity_ids) - objects
        if missing:
            raise ValueError(f"required campaign entities are missing: {sorted(missing)}")
        referenced = set(brief.required_entity_ids)
        for predicate in (*scenario.end_when, *scenario.expected):
            referenced.add(predicate.subject_id)
            if predicate.object_id:
                referenced.add(predicate.object_id)
        for rule in scenario.world.mechanics:
            referenced.add(rule.source_id)
            if rule.subject_id:
                referenced.add(rule.subject_id)
            for atom in (*rule.when, *rule.effects):
                referenced.add(atom.subject_id)
                object_id = getattr(atom, "object_id", None)
                if object_id:
                    referenced.add(object_id)
        missing_scan, missing_inspect = [], []
        for item_id, obj in scenario.world.entities.items():
            if not isinstance(obj, Item):
                continue
            scan = obj.perception_for("scan").description.strip()
            if not scan:
                missing_scan.append(item_id)
            interactive = obj.portable or any((
                obj.container, obj.openable, obj.lockable, obj.slot, obj.operable,
            )) or item_id in referenced
            inspect = obj.perception_for("inspect").description.strip()
            if interactive and (not inspect or inspect == scan):
                missing_inspect.append(item_id)
        if missing_scan:
            raise ValueError(f"campaign Items require a scan appearance: {sorted(missing_scan)}")
        if missing_inspect:
            raise ValueError(
                "interactive or story-relevant campaign Items require a distinct inspect description: "
                f"{sorted(missing_inspect)}"
            )
        for object_id, obj in {**scenario.world.entities, **scenario.world.passages}.items():
            previous = self.state.entity_canon.get(object_id)
            kind = getattr(obj, "kind", "passage")
            if previous and (previous.kind != kind or previous.name != obj.name):
                raise ValueError(f"stable entity {object_id} changed kind or name")
        roles = {}
        for actor_id in scenario.world.character_ids:
            canon = self.state.bible.character(actor_id) if self.state.bible else None
            inner = self.state.inner_states.get(actor_id, "")
            roles[actor_id] = RoleBrief(
                personality=canon.personality if canon else "",
                private_goal=inner or (canon.inner_life if canon else ""),
                memories=self.state.memories.get(actor_id, ()),
                known_entity_ids=(),
            )
        return scenario.model_copy(update={"roles": roles})

    def _update_entity_canon(self, scenario: Scenario):
        for object_id, obj in {**scenario.world.entities, **scenario.world.passages}.items():
            self.state.entity_canon[object_id] = EntityCanon(
                kind=getattr(obj, "kind", "passage"), name=obj.name)

    def _enter_act(self):
        state = self.state
        if state.phase != "interlude" or state.current_scenario is None:
            raise WebError("当前没有准备好的下一幕。")
        scenario = Scenario.model_validate(state.current_scenario)
        cast = {
            actor: ParticipantConfig(adapter="human") if actor == state.protagonist_id
            else ParticipantConfig(adapter="llm", profile=self.policy.npc_profile)
            for actor in scenario.world.character_ids
        }
        child_config = RunConfig(backends=self.config.backends, profiles=self.config.profiles, cast=cast)
        self.act = WebSession(
            scenario, child_config, runs_dir=self.store.act_root(state.act_number),
            llm_timeout=self.llm_timeout, identity_contexts=self._identity_contexts(scenario),
        )
        self._manual_end = False
        self.act.start({"cast": {key: value.model_dump(mode="json") for key, value in cast.items()},
                        "rounds": scenario.max_rounds, "seed": scenario.seed, "auto": True})
        state.phase = "playing_act"
        state.checkpoint_kind = "act_ready"
        state.current_act_run_dir = str(self.act.disk.run_dir)
        state.resume_notice = None
        self._save()
        self._watch_act_worker()

    def _watch_act_worker(self):
        if self.act is None or self.act.worker is None:
            return
        worker = self.act.worker
        marker = id(worker)
        if marker in self._watched_workers:
            return
        self._watched_workers.add(marker)
        Thread(target=self._finish_act_worker, args=(worker,), daemon=True,
               name=f"campaign-watch-{self.state.act_number}").start()

    def _finish_act_worker(self, worker):
        worker.join()
        with self.lock:
            if self.act is None or self.state is None or self._aborting:
                return
            status = self.act.status
            if status not in TERMINAL:
                return
            if status == "failed":
                self._fail("playing_act", RuntimeError(self.act.error or "act failed"))
                return
            reason = "player_ended" if self._manual_end else (
                "engine_completed" if status == "completed" else "budget_exhausted")
            self.state.act_outcome = ActOutcome(
                act_number=self.state.act_number, status=status, reason=reason,
                run_dir=str(self.act.disk.run_dir), turns=self.act.counts["turns"],
                events=self.act.counts["events"],
            )
            self.state.current_act_run_dir = str(self.act.disk.run_dir)
            self.state.phase = "summarizing"
            self.state.checkpoint_kind = "act_finished"
            self.state.error = None
            self._save()
            self._launch(self._process_finished_act, f"campaign-transition-{self.state.act_number}")

    def _process_finished_act(self):
        state = self.state
        assert state is not None and state.act_outcome is not None and self.store is not None and self.llm is not None
        run_dir = Path(state.act_outcome.run_dir)
        if state.world_summary is None:
            state.phase = "summarizing"
            self._save()
            payload = {
                "act_number": state.act_number,
                "termination": state.act_outcome,
                "initial_state": self._read_json(run_dir / "initial_state.json"),
                "final_state": self._read_json(run_dir / "final_state.json"),
                "world_log": self._read_rows(run_dir / "transactions.jsonl"),
                "action_results": self._read_rows(run_dir / "action_results.jsonl"),
            }
            state.world_summary = self.llm.typed_call(
                profile_name=self.policy.world_summary_profile,
                operation_prefix=f"world-summary-act-{state.act_number}-e{state.retry_epoch}", actor_id="world_summary",
                system_prompt=WORLD_SUMMARY_SYSTEM + "\n\n" + self._public_campaign_context(),
                user_prompt=json.dumps(payload, ensure_ascii=False,
                    default=lambda value: value.model_dump(mode="json")),
                result_type=WorldSummary, retries=self.policy.max_generation_retries,
            )
            self.store.record("world_summaries", {"act_number": state.act_number,
                                                   "summary": state.world_summary})
            self._save()
        state.phase = "directing"
        self._save()
        if state.current_is_finale:
            if state.conclusion is None:
                state.conclusion = self._director().call(
                    f"director-conclusion-act-{state.act_number}-e{state.retry_epoch}",
                    "终幕已经结束。根据以下客观摘要给出最终旁白和结局；必须闭合所有主线：\n"
                    + state.world_summary.model_dump_json(),
                    DirectorConclusion, self.policy.max_generation_retries,
                )
                self.store.record("director_conclusions", state.conclusion)
                self._save()
            state.phase = "remembering"
            self._save()
            self._remember_characters(self._current_actor_ids(), ())
            state.phase = "completed"
            state.checkpoint_kind = "act_finished"
            state.interlude = state.conclusion.final_narration
            state.error = None
            self._save()
            return
        if state.director_transition is None:
            transition = self._director().call(
                f"director-transition-act-{state.act_number}-e{state.retry_epoch}",
                "本幕已经结束。根据客观摘要和结束原因安排幕间与下一幕；玩家失败也必须得到现实回应。\n"
                + json.dumps({"outcome": state.act_outcome, "summary": state.world_summary},
                             ensure_ascii=False, default=lambda value: value.model_dump(mode="json")),
                DirectorTransition, self.policy.max_generation_retries,
            )
            if transition.next_act.act_number != state.act_number + 1:
                raise ValueError("director next act number must be sequential")
            if state.protagonist_id not in transition.next_act.cast_ids:
                raise ValueError("director next act must include the protagonist")
            bible_ids = {character.id for character in state.bible.characters}
            unknown_cast = set(transition.next_act.cast_ids) - bible_ids
            if unknown_cast:
                raise ValueError(f"director next act references unknown campaign characters: {sorted(unknown_cast)}")
            known_characters = set(self._current_actor_ids()) | set(transition.next_act.cast_ids)
            known_characters |= {character.id for character in state.bible.characters}
            unknown_observers = {item.observer_id for item in transition.character_interludes} - known_characters
            if unknown_observers:
                raise ValueError(f"interlude observations reference unknown characters: {sorted(unknown_observers)}")
            state.director_transition = transition
            self.store.record("director_transitions", transition)
            for item in transition.character_interludes:
                self.store.record("interlude_observations", {
                    "act_number": state.act_number, "observer_id": item.observer_id,
                    "content": item.content,
                })
            self._save()
        state.phase = "remembering"
        self._save()
        transition = state.director_transition
        actor_ids = set(self._current_actor_ids()) | {item.observer_id for item in transition.character_interludes}
        self._remember_characters(actor_ids, transition.character_interludes)
        self._prepare_scene(transition.next_act, transition.public_interlude, "next_act_ready")

    def _remember_characters(self, actor_ids, interludes):
        state = self.state
        assert state is not None and state.act_outcome is not None and self.llm is not None and self.store is not None
        run_dir = Path(state.act_outcome.run_dir)
        observations = self._read_rows(run_dir / "observations.jsonl")
        scenario = Scenario.model_validate(state.current_scenario)
        for actor_id in sorted(actor_ids):
            if actor_id in state.remembered_for_act:
                continue
            actor_observations = [row for row in observations if row.get("observer_id") == actor_id]
            authorized_interlude = [item.content for item in interludes if item.observer_id == actor_id]
            brief = scenario.roles.get(actor_id, RoleBrief())
            canon = state.bible.character(actor_id) if state.bible else None
            payload = {
                "actor_id": actor_id,
                "personality": canon.personality if canon else brief.personality,
                "existing_authorized_memories": state.memories.get(actor_id, brief.memories),
                "previous_inner_state": state.inner_states.get(actor_id, ""),
                "observations": actor_observations,
                "authorized_interlude_observations": authorized_interlude,
            }
            system = CHARACTER_MEMORY_SYSTEM + "\n\n" + self._public_campaign_context()
            memory = self.llm.typed_call(
                profile_name=self.policy.character_memory_profile,
                operation_prefix=f"character-memory-act-{state.act_number}-{actor_id}-e{state.retry_epoch}",
                actor_id=f"memory:{actor_id}", system_prompt=system,
                user_prompt=json.dumps(payload, ensure_ascii=False),
                result_type=CharacterMemory, retries=self.policy.max_generation_retries,
            )
            if memory.actor_id != actor_id:
                raise ValueError("character memory response used the wrong actor_id")
            state.memories[actor_id] = memory.durable_memories
            state.inner_states[actor_id] = memory.inner_state
            state.remembered_for_act.add(actor_id)
            self.store.record("character_memories", {
                "act_number": state.act_number, "memory": memory})
            self._save()

    def _identity_contexts(self, scenario: Scenario):
        return {actor: self._public_campaign_context(actor) for actor in scenario.world.character_ids}

    def _public_campaign_context(self, actor_id: str | None = None):
        bible = self.state.bible if self.state else None
        if bible is None:
            return ""
        lines = [f"世界：{bible.public_world}", "重大历史：" + "；".join(bible.major_history),
                 f"当前核心冲突：{bible.central_conflict}", "公开人物："]
        lines.extend(f"- {character.name} [{character.id}]：{character.public_role}；{character.description}"
                     for character in bible.characters)
        own = bible.character(actor_id) if actor_id else None
        if own:
            lines.extend([f"你与历史的联系：{own.historical_tie}", f"你的长期内心底色：{own.inner_life}"])
        return "\n".join(lines)

    def _actor_names(self):
        if self.state is None or self.state.current_scenario is None:
            return {}
        scenario = Scenario.model_validate(self.state.current_scenario)
        return {actor: scenario.world.entities[actor].name for actor in scenario.world.character_ids}

    def _current_actor_ids(self):
        if self.state.current_scenario is None:
            return ()
        return Scenario.model_validate(self.state.current_scenario).world.character_ids

    def _retry(self):
        phase = self.state.retry_phase
        self.state.retry_epoch += 1
        self.state.error = None
        if phase == "playing_act":
            self.state.phase = "interlude"
            self.state.checkpoint_kind = "act_ready"
            self.state.current_act_run_dir = None
            self.state.resume_notice = "技术故障后的本幕将从开始前重新运行。"
            self._save()
        elif phase == "creating_world":
            self._launch(self._create_world, "campaign-retry-genesis")
        elif phase == "preparing_act":
            brief = self.state.current_act_brief
            interlude = self.state.director_transition.public_interlude if self.state.director_transition else brief.opening
            kind = "next_act_ready" if brief.act_number > 1 else "act_ready"
            self._launch(lambda: self._prepare_scene(brief, interlude, kind), "campaign-retry-scene")
        elif phase in {"summarizing", "directing", "remembering"}:
            self._launch(self._process_finished_act, "campaign-retry-transition")
        else:
            raise WebError("该技术阶段无法自动重试。")

    def _resume_work(self):
        phase = self.state.phase
        if phase == "creating_world":
            self._launch(self._create_world, "campaign-resume-genesis")
        elif phase == "preparing_act":
            brief = self.state.current_act_brief
            interlude = self.state.director_transition.public_interlude if self.state.director_transition else brief.opening
            kind = "next_act_ready" if brief.act_number > 1 else "act_ready"
            self._launch(lambda: self._prepare_scene(brief, interlude, kind), "campaign-resume-scene")
        else:
            self._launch(self._process_finished_act, "campaign-resume-transition")

    def _abort(self):
        self._aborting = True
        if self.act and self.state.phase == "playing_act" and self.act.status not in TERMINAL:
            try:
                self.act.command("stop", {"session_id": self.act.session_id})
            except WebError:
                pass
        self.state.phase = "abandoned"
        self.state.error = None
        self._save()

    @staticmethod
    def _read_json(path: Path):
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _read_rows(path: Path):
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
