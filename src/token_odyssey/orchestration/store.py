"""Atomic campaign checkpoints and idempotent orchestration call records."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from token_odyssey.constants import CAMPAIGN_CHECKPOINT_SCHEMA_VERSION
from token_odyssey.recording.recorder import jsonable

from .models import CampaignState


def new_campaign_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]


class CampaignStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.operations = self.path / "operations"

    @classmethod
    def create(cls, root: str | Path) -> "CampaignStore":
        store = cls(Path(root) / new_campaign_id())
        store.path.mkdir(parents=True, exist_ok=False)
        store.operations.mkdir()
        (store.path / "acts").mkdir()
        (store.path / "scenarios").mkdir()
        return store

    @classmethod
    def open(cls, root: str | Path, campaign: str | Path) -> "CampaignStore":
        supplied = Path(campaign)
        path = supplied if supplied.is_dir() else Path(root) / supplied
        if not (path / "checkpoint.json").is_file():
            raise ValueError("campaign checkpoint does not exist")
        return cls(path)

    def save(self, state: CampaignState) -> None:
        self._write("checkpoint.json", state)
        self._write(
            "manifest.json",
            {
                "schema_version": CAMPAIGN_CHECKPOINT_SCHEMA_VERSION,
                "campaign_id": state.campaign_id,
                "phase": state.phase,
                "checkpoint_kind": state.checkpoint_kind,
                "act_number": state.act_number,
                "title": state.bible.title if state.bible else None,
                "error": state.error,
            },
        )

    def load(self) -> CampaignState:
        return CampaignState.model_validate(json.loads((self.path / "checkpoint.json").read_text(encoding="utf-8")))

    def record(self, stream: str, value) -> None:
        if not stream.replace("_", "").isalnum():
            raise ValueError("invalid campaign record stream")
        with (self.path / f"{stream}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(jsonable(value), ensure_ascii=False) + "\n")

    def save_scenario(self, act_number: int, raw: dict) -> None:
        self._write(f"scenarios/act-{act_number:03d}.json", raw)

    def act_root(self, act_number: int) -> Path:
        path = self.path / "acts" / f"act-{act_number:03d}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def operation(self, operation_id: str) -> dict | None:
        path = self.operations / (self._safe(operation_id) + ".json")
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def save_operation(self, operation_id: str, value: dict) -> None:
        self._write(f"operations/{self._safe(operation_id)}.json", value)

    @staticmethod
    def list_checkpoints(root: str | Path) -> list[dict]:
        result = []
        for path in sorted(Path(root).glob("*/manifest.json"), reverse=True):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if row.get("schema_version") == CAMPAIGN_CHECKPOINT_SCHEMA_VERSION:
                result.append(row)
        return result[:20]

    @staticmethod
    def _safe(operation_id: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", operation_id)
        if not safe:
            raise ValueError("invalid operation id")
        return safe

    def _write(self, relative: str, value) -> None:
        path = self.path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(jsonable(value), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
