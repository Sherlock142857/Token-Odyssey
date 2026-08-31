"""Append-only run recording and deterministic replay artifacts."""

from __future__ import annotations

import hashlib
import json
import platform
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from airpg.models import (
    AgentDecision,
    AgentSession,
    ChatMessage,
    Observation,
    Scenario,
    TokenUsage,
    WorldEvent,
    WorldState,
)


class NullRunRecorder:
    run_dir: Path | None = None

    def record_agent_message(self, *args: Any, **kwargs: Any) -> None: pass
    def record_decision(self, *args: Any, **kwargs: Any) -> None: pass
    def record_observation(self, *args: Any, **kwargs: Any) -> None: pass
    def record_projection(self, *args: Any, **kwargs: Any) -> None: pass
    def record_world_event(self, *args: Any, **kwargs: Any) -> None: pass
    def record_router(self, *args: Any, **kwargs: Any) -> None: pass
    def record_state_change(self, *args: Any, **kwargs: Any) -> None: pass
    def record_trace(self, *args: Any, **kwargs: Any) -> None: pass
    def finalize(self, *args: Any, **kwargs: Any) -> None: pass


class RunRecorder:
    """Writes facts, private agent streams and diagnostics without conflating them."""

    def __init__(
        self,
        scenario: Scenario,
        *,
        seed: int,
        provider: str,
        root: str | Path = "runs",
        run_id: str | None = None,
    ) -> None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.run_id = run_id or f"{timestamp}-{uuid4().hex[:8]}"
        self.run_dir = Path(root) / self.run_id
        (self.run_dir / "agents").mkdir(parents=True, exist_ok=False)
        self.scenario = scenario
        self.seed = seed
        self.provider = provider
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._token_by_actor: dict[str, TokenUsage] = {
            actor_id: TokenUsage() for actor_id in scenario.world.actors
        }
        scenario_data = scenario.model_dump(mode="json")
        canonical = json.dumps(scenario_data, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self._manifest: dict[str, Any] = {
            "schema_version": 1,
            "run_id": self.run_id,
            "status": "running",
            "started_at": self.started_at,
            "scenario_id": scenario.id,
            "scenario_title": scenario.title,
            "scenario_sha256": hashlib.sha256(canonical).hexdigest(),
            "seed": seed,
            "provider": provider,
            "python": platform.python_version(),
            "airpg_version": _package_version(),
            "models": {
                actor_id: actor.model.model_dump(mode="json")
                for actor_id, actor in scenario.world.actors.items()
            },
        }
        self._write_json("manifest.json", self._manifest)
        self._write_json("scenario.json", scenario_data)
        self._write_json("initial_state.json", scenario.world.model_dump(mode="json"))
        (self.run_dir / "transcript.md").write_text(
            f"# {scenario.title}\n\nRun `{self.run_id}` · seed `{seed}` · provider `{provider}`\n\n",
            encoding="utf-8",
        )

    def record_agent_message(
        self,
        actor_id: str,
        message: ChatMessage,
        *,
        round_number: int,
        purpose: str,
        message_index: int,
    ) -> None:
        self._append_jsonl(
            Path("agents") / f"{actor_id}.jsonl",
            {
                "round_number": round_number,
                "message_index": message_index,
                "purpose": purpose,
                "message": message.model_dump(mode="json"),
            },
        )

    def record_decision(
        self,
        *,
        round_number: int,
        actor_id: str,
        attempt: int,
        decision: AgentDecision,
        accepted: bool,
        reasons: list[str],
    ) -> None:
        self._append_jsonl(
            "decisions.jsonl",
            {
                "round_number": round_number,
                "actor_id": actor_id,
                "attempt": attempt,
                "accepted": accepted,
                "reasons": reasons,
                "decision": decision.model_dump(mode="json"),
            },
        )
        self._accumulate_usage(actor_id, decision.usage)

    def record_observation(self, observation: Observation) -> None:
        self._append_jsonl(
            "observations.jsonl",
            {"outcome": observation.level.value, "observation": observation.model_dump(mode="json")},
        )

    def record_projection(
        self,
        *,
        observer_id: str,
        event_sequence: int,
        outcome: str,
        full_threshold: float,
        partial_threshold: float,
        roll: float | None,
    ) -> None:
        if outcome != "none":
            return
        self._append_jsonl(
            "observations.jsonl",
            {
                "outcome": "none",
                "observer_id": observer_id,
                "source_event_sequence": event_sequence,
                "full_threshold": full_threshold,
                "partial_threshold": partial_threshold,
                "roll": roll,
            },
        )

    def record_world_event(self, event: WorldEvent, transcript_text: str) -> None:
        self._append_jsonl("world_events.jsonl", event.model_dump(mode="json"))
        actor = self.scenario.world.actors[event.actor_id]
        if event.kind.value == "dialogue":
            line = f"**{actor.name}**：{event.data['content']}\n\n"
        else:
            line = f"*{transcript_text}*\n\n"
        with (self.run_dir / "transcript.md").open("a", encoding="utf-8") as handle:
            handle.write(line)

    def record_router(self, round_number: int, order: list[str]) -> None:
        self._append_jsonl(
            "trace.jsonl",
            {"category": "router", "round_number": round_number, "order": order},
        )
        with (self.run_dir / "transcript.md").open("a", encoding="utf-8") as handle:
            handle.write(f"## 第 {round_number} 轮\n\n")

    def record_state_change(self, event: WorldEvent) -> None:
        if event.kind.value != "action" or event.action_kind is None:
            return
        self._append_jsonl(
            "state_changes.jsonl",
            {
                "event_sequence": event.sequence,
                "round_number": event.round_number,
                "actor_id": event.actor_id,
                "action_kind": event.action_kind.value,
                "data": event.data,
            },
        )

    def record_trace(self, category: str, payload: Any) -> None:
        self._append_jsonl("trace.jsonl", {"category": category, "payload": _jsonable(payload)})

    def finalize(
        self,
        *,
        state: WorldState,
        sessions: dict[str, AgentSession],
        result: Any | None,
        status: str,
        error: str | None = None,
    ) -> None:
        totals = TokenUsage()
        actors: dict[str, Any] = {}
        for actor_id, usage in self._token_by_actor.items():
            actors[actor_id] = usage.model_dump(mode="json")
            _add_usage(totals, usage)
        token_data = {
            "actors": actors,
            "total": totals.model_dump(mode="json"),
            "cache_hit_ratio": (
                totals.prompt_cache_hit_tokens / totals.prompt_tokens
                if totals.prompt_tokens
                else 0.0
            ),
        }
        self._write_json("token_usage.json", token_data)
        self._write_json("final_state.json", state.model_dump(mode="json"))
        self._write_json(
            "sessions.json",
            {actor_id: session.model_dump(mode="json") for actor_id, session in sessions.items()},
        )
        self._manifest.update(
            {
                "status": status,
                "ended_at": datetime.now(timezone.utc).isoformat(),
                "error": error,
                "result": _jsonable(result),
            }
        )
        self._write_json("manifest.json", self._manifest)

    def _accumulate_usage(self, actor_id: str, usage: TokenUsage) -> None:
        _add_usage(self._token_by_actor[actor_id], usage)

    def _append_jsonl(self, relative: str | Path, payload: Any) -> None:
        path = self.run_dir / relative
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_jsonable(payload), ensure_ascii=False) + "\n")

    def _write_json(self, relative: str | Path, payload: Any) -> None:
        path = self.run_dir / relative
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(_jsonable(payload), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)


def _add_usage(target: TokenUsage, addition: TokenUsage) -> None:
    for field in TokenUsage.model_fields:
        setattr(target, field, getattr(target, field) + getattr(addition, field))


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if hasattr(value, "__dict__"):
        return {key: _jsonable(item) for key, item in vars(value).items()}
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _package_version() -> str:
    try:
        return version("airpg")
    except PackageNotFoundError:
        return "development"
