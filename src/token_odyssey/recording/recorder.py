"""Append-only run artifact schema v2."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from token_odyssey.agents.contracts import AgentDecision, ChatMessage, ChatRole, TokenUsage
from token_odyssey.inside_act.domain.events import EventSource, ExecutionNotice, WorldEvent
from token_odyssey.inside_act.domain.knowledge import Observation
from token_odyssey.inside_act.domain.scenario import Scenario
from token_odyssey.inside_act.domain.spatial import WorldState


class NullRunRecorder:
    run_dir: Path | None = None

    def record_observation(self, *args: Any, **kwargs: Any) -> None: pass
    def record_trace(self, *args: Any, **kwargs: Any) -> None: pass
    def record_router(self, *args: Any, **kwargs: Any) -> None: pass
    def record_decision(self, *args: Any, **kwargs: Any) -> None: pass
    def record_fallback(self, *args: Any, **kwargs: Any) -> None: pass
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
        self._decision_records: dict[str, list[dict[str, Any]]] = {
            actor_id: [] for actor_id in scenario.world.character_ids
        }
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
        outcome: str,
        reasons: list[str],
        notices: list[ExecutionNotice],
    ) -> None:
        self._append(
            "decisions.jsonl",
            {
                "round_number": round_number,
                "actor_id": actor_id,
                "attempt": attempt,
                "accepted": accepted,
                "outcome": outcome,
                "reasons": reasons,
                "notices": notices,
                "decision": decision,
            },
        )
        self._decision_records[actor_id].append(
            {
                "round_number": round_number,
                "attempt": attempt,
                "accepted": accepted,
                "outcome": outcome,
                "reasons": list(reasons),
                "notices": list(notices),
                "output_error": decision.output_error,
                "raw_content": decision.raw_content,
                "fallback": False,
            }
        )
        _add_usage(self._usage[actor_id], decision.usage)

    def record_fallback(self, *, round_number: int, actor_id: str) -> None:
        records = self._decision_records[actor_id]
        for record in reversed(records):
            if record["round_number"] == round_number:
                record["fallback"] = True
                record["outcome"] = "fallback"
                return

    def record_world_event(self, event: WorldEvent, transcript: str) -> None:
        self._append("world_events.jsonl", event)
        with (self.run_dir / "transcript.md").open("a", encoding="utf-8") as handle:
            if event.source == EventSource.ACTION and event.action_kind == "say":
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
        self._write_prompt_flow(participants, error=error)
        self.manifest.update(
            {
                "status": status,
                "ended_at": datetime.now(timezone.utc).isoformat(),
                "error": error,
                "result": _jsonable(result),
            }
        )
        self._write("manifest.json", self.manifest)

    def _write_prompt_flow(
        self, participants: dict[str, object], *, error: str | None
    ) -> None:
        parts = [
            f"# {self.scenario.title} · Prompt Flow",
            "",
            f"Run `{self.run_id}`。这里只记录每次新增的消息，不重复累计前缀。",
        ]
        for actor_id in self.scenario.world.character_ids:
            actor = self.scenario.world.character(actor_id)
            messages = list(getattr(participants[actor_id], "messages", []))
            parts.extend(["", f"## {actor.name} (`{actor_id}`)", ""])
            if not messages:
                parts.append("该 Participant 没有 LLM prompt session。")
                continue

            system_messages = [
                message for message in messages if message.role == ChatRole.SYSTEM
            ]
            if system_messages:
                parts.extend(
                    ["### System prompt", "", _fenced(system_messages[0].content)]
                )

            exchanges: list[tuple[ChatMessage | None, ChatMessage | None]] = []
            pending_user: ChatMessage | None = None
            for message in messages:
                if message.role == ChatRole.SYSTEM:
                    continue
                if message.role == ChatRole.USER:
                    if pending_user is not None:
                        exchanges.append((pending_user, None))
                    pending_user = message
                elif message.role == ChatRole.ASSISTANT:
                    exchanges.append((pending_user, message))
                    pending_user = None
            if pending_user is not None:
                exchanges.append((pending_user, None))

            records = self._decision_records.get(actor_id, [])
            for index, (user_message, assistant_message) in enumerate(exchanges, start=1):
                record = records[index - 1] if index <= len(records) else None
                if record is None:
                    title = f"### Request {index} · 未完成"
                else:
                    title = (
                        f"### Request {index} · round={record['round_number']} "
                        f"attempt={record['attempt']} · {record['outcome']}"
                    )
                parts.extend(["", title, "", "#### Appended user prompt", ""])
                parts.append(_fenced(user_message.content if user_message else "（缺少 user 消息）"))
                parts.extend(["", "#### Assistant response", ""])
                if assistant_message is not None:
                    parts.append(_fenced(assistant_message.content))
                elif record and record.get("raw_content"):
                    parts.append(_fenced(str(record["raw_content"])))
                else:
                    parts.append("（后端没有返回；运行在该请求处结束。）")
                if record and record["reasons"]:
                    parts.extend(["", "#### Rejection reasons", ""])
                    parts.extend(f"- {reason}" for reason in record["reasons"])
                if record and record["notices"]:
                    parts.extend(["", "#### Execution notices", ""])
                    parts.extend(
                        f"- [{notice.code}] {notice.message}"
                        for notice in record["notices"]
                    )
                if record and record.get("fallback"):
                    parts.extend(
                        [
                            "",
                            "#### Automatic fallback",
                            "",
                            "该角色本次行动权的重试已耗尽；Harness 提交了一个不产生 World Event 的 `wait`。",
                        ]
                    )

        if error:
            parts.extend(["", "## Run error", "", _fenced(error)])
        (self.run_dir / "prompt_flow.md").write_text(
            "\n".join(parts).rstrip() + "\n", encoding="utf-8"
        )

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


def _fenced(content: str) -> str:
    fence = "~~~~"
    while fence in content:
        fence += "~"
    return f"{fence}text\n{content}\n{fence}"
