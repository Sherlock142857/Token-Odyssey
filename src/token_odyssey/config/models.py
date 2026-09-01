"""Runtime controller and model configuration, kept outside Scenario world facts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import Field

from token_odyssey.inside_act.domain.common import StrictModel
from token_odyssey.llm.contracts import LLMProfile


class BackendConfig(StrictModel):
    driver: str = "openai_compatible"
    base_url: str
    api_key_env: str | None = None
    api_key_file: str | None = None


class ParticipantConfig(StrictModel):
    adapter: str
    mode: str | None = None


class RunConfig(StrictModel):
    schema_version: int = Field(default=2, ge=2, le=2)
    backends: dict[str, BackendConfig] = Field(default_factory=dict)
    llm_profiles: dict[str, LLMProfile] = Field(default_factory=dict)
    cast: dict[str, ParticipantConfig]


def load_run_config(path: str | Path) -> RunConfig:
    raw: Any = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("RunConfig root must be a mapping")
    return RunConfig.model_validate(raw)


def demo_run_config(actor_ids: list[str]) -> RunConfig:
    return RunConfig(cast={actor_id: ParticipantConfig(adapter="demo") for actor_id in actor_ids})
