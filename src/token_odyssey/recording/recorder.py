"""Explicit run sinks; adapter diagnostics never require inspecting participants."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel

from token_odyssey.constants import RUN_LOG_SCHEMA_VERSION
from token_odyssey.kernel.state import WorldState
from token_odyssey.llm.contracts import LLMExchange, TokenUsage
from token_odyssey.scenario import Scenario


def jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", serialize_as_any=True)
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [jsonable(item) for item in value]
    return value


class Recorder(Protocol):
    """Downstream sink for run diagnostics; recorder success cannot create truth."""

    def record(self, stream: str, value: Any) -> None: ...
    def finalize(self, *, state: WorldState, result: Any, status: str, error: str | None = None) -> None: ...


class NullRecorder:
    def record(self, stream: str, value: Any) -> None:
        pass

    def finalize(self, *, state: WorldState, result: Any, status: str, error: str | None = None) -> None:
        pass


class RunRecorder:
    """Persist replayable run artifacts after runtime decisions and commits.

    Files may contain prompts, model replies, private thoughts, observations, and
    token usage, but are never read back as authority by an active WorldHarness.
    Credentials are configuration inputs and must never be passed to this sink.
    """

    def __init__(self, scenario: Scenario, *, root: str | Path = "runs", seed: int | None = None) -> None:
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
        self.run_dir = Path(root) / run_id
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self.manifest = {
            "schema_version": RUN_LOG_SCHEMA_VERSION,
            "scenario_id": scenario.id,
            "seed": scenario.seed if seed is None else seed,
            "status": "running",
        }
        self._write("manifest.json", self.manifest)
        self._write("scenario.json", scenario)
        self._write("initial_state.json", scenario.initial_state)
        self._usage: dict[str, dict[str, int]] = {}
        self._system_written: set[str] = set()
        (self.run_dir / "prompt_flow.md").write_text(f"# {scenario.title} · 模型请求\n", encoding="utf-8")

    def record(self, stream: str, value: Any) -> None:
        if not stream.replace("_", "").isalnum():
            raise ValueError("invalid record stream name")
        with (self.run_dir / f"{stream}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(jsonable(value), ensure_ascii=False) + "\n")
        if isinstance(value, LLMExchange):
            usage = self._usage.setdefault(value.actor_id, {key: 0 for key in TokenUsage.model_fields})
            if value.response:
                for key, count in value.response.usage.model_dump().items():
                    usage[key] += count
            with (self.run_dir / "prompt_flow.md").open("a", encoding="utf-8") as handle:
                handle.write(f"\n## {value.actor_id} · {value.request_id}\n\n")
                if value.actor_id not in self._system_written:
                    handle.write(_fence(value.request.messages[0].content))
                    self._system_written.add(value.actor_id)
                handle.write("\n输入：\n" + _fence(value.request.messages[-1].content))
                handle.write(
                    "\n输出：\n" + _fence(value.response.content if value.response else value.error or "无回复")
                )

    def finalize(self, *, state: WorldState, result: Any, status: str, error: str | None = None) -> None:
        self._write("final_state.json", state)
        self._write("token_usage.json", self._usage)
        self.manifest.update(status=status, result=jsonable(result), error=error)
        self._write("manifest.json", self.manifest)

    def _write(self, name: str, value: Any) -> None:
        path = self.run_dir / name
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(jsonable(value), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)


def _fence(text: str) -> str:
    fence = "~~~~"
    while fence in text:
        fence += "~"
    return f"{fence}text\n{text}\n{fence}\n"
