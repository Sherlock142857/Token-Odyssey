"""Append-only run artifact schema v2."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from token_odyssey.agents.contracts import AgentDecision, ChatMessage, TokenUsage
from token_odyssey.inside_act.domain.events import WorldEvent
from token_odyssey.inside_act.domain.knowledge import Observation
from token_odyssey.inside_act.domain.scenario import Scenario
from token_odyssey.inside_act.domain.spatial import WorldState


class NullRunRecorder:
    run_dir: Path | None = None

    def record_observation(self, *args: Any, **kwargs: Any) -> None: pass
    def record_trace(self, *args: Any, **kwargs: Any) -> None: pass
    def record_router(self, *args: Any, **kwargs: Any) -> None: pass
    def record_decision(self, *args: Any, **kwargs: Any) -> None: pass
    def record_world_event(self, *args: Any, **kwargs: Any) -> None: pass
    def finalize(self, *args: Any, **kwargs: Any) -> None: pass


class RunRecorder:
    def __init__(
        self,
        scenario: Scenario,
        *,
        seed: int,
        modes: dict[str, str],
        root: str | Path = "runs",
        run_id: str | None = None,
    ) -> None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.run_id = run_id or f"{timestamp}-{uuid4().hex[:8]}"
        self.run_dir = Path(root) / self.run_id
        (self.run_dir / "agents").mkdir(parents=True, exist_ok=False)
        self.scenario = scenario
        self.seed = seed
        self.modes = modes
        self._usage = {actor_id: TokenUsage() for actor_id in scenario.world.character_ids}
        scenario_data = scenario.model_dump(mode="json")
        canonical = json.dumps(scenario_data, sort_keys=True, ensure_ascii=False).encode()
        self.manifest = {
            "schema_version": 2,
            "run_id": self.run_id,
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "scenario_id": scenario.id,
            "scenario_sha256": hashlib.sha256(canonical).hexdigest(),
            "seed": seed,
            "modes": modes,
        }
        self._write("manifest.json", self.manifest)
        self._write("scenario.json", scenario_data)
        self._write("initial_state.json", scenario.world.model_dump(mode="json"))
        (self.run_dir / "transcript.md").write_text(
            f"# {scenario.title}\n\nRun `{self.run_id}` · schema v2 · seed `{seed}`\n\n",
            encoding="utf-8",
        )

    def record_observation(self, observation: Observation) -> None:
        self._append(
            "observations.jsonl",
            {"outcome": observation.level.value, "observation": observation},
        )

    def record_trace(self, category: str, payload: dict) -> None:
        self._append("trace.jsonl", {"category": category, "payload": payload})

    def record_router(self, round_number: int, order: list[str]) -> None:
        self._append("trace.jsonl", {"category": "router", "round_number": round_number, "order": order})
        with (self.run_dir / "transcript.md").open("a", encoding="utf-8") as handle:
            handle.write(f"## 第 {round_number} 轮\n\n")

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
        self._append(
            "decisions.jsonl",
            {
                "round_number": round_number,
                "actor_id": actor_id,
                "attempt": attempt,
                "accepted": accepted,
                "reasons": reasons,
                "decision": decision,
            },
        )
        _add_usage(self._usage[actor_id], decision.usage)

    def record_world_event(self, event: WorldEvent, transcript: str) -> None:
        self._append("world_events.jsonl", event)
        with (self.run_dir / "transcript.md").open("a", encoding="utf-8") as handle:
            if event.action_kind == "say":
                handle.write(f"{transcript}\n\n")
            else:
                handle.write(f"*{transcript}*\n\n")

    def finalize(
        self,
        *,
        state: WorldState,
        participants: dict[str, object],
        result: Any | None,
        status: str,
        error: str | None = None,
    ) -> None:
        total = TokenUsage()
        for usage in self._usage.values():
            _add_usage(total, usage)
        self._write(
            "token_usage.json",
            {
                "actors": {actor: usage for actor, usage in self._usage.items()},
                "total": total,
                "cache_hit_ratio": (
                    total.prompt_cache_hit_tokens / total.prompt_tokens
                    if total.prompt_tokens
                    else 0.0
                ),
            },
        )
        self._write("final_state.json", state)
        sessions = {
            actor_id: getattr(participant, "messages", [])
            for actor_id, participant in participants.items()
        }
        self._write("sessions.json", sessions)
        self.manifest.update(
            {
                "status": status,
                "ended_at": datetime.now(timezone.utc).isoformat(),
                "error": error,
                "result": _jsonable(result),
            }
        )
        self._write("manifest.json", self.manifest)

    def _append(self, relative: str, value: Any) -> None:
        with (self.run_dir / relative).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_jsonable(value), ensure_ascii=False) + "\n")

    def _write(self, relative: str, value: Any) -> None:
        path = self.run_dir / relative
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(_jsonable(value), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)


def _add_usage(target: TokenUsage, addition: TokenUsage) -> None:
    for field_name in TokenUsage.model_fields:
        setattr(target, field_name, getattr(target, field_name) + getattr(addition, field_name))


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", serialize_as_any=True)
    if hasattr(value, "__dict__"):
        return {key: _jsonable(item) for key, item in vars(value).items()}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
