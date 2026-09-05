"""Saved API profiles and controller bindings, separate from world facts."""

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from token_odyssey.common import FrozenModel
from token_odyssey.config.yaml import load_mapping
from token_odyssey.llm.contracts import LLMProfile


class BackendConfig(FrozenModel):
    driver: str = "openai_compatible"
    base_url: str
    api_key_env: str | None = None
    api_key_file: str | None = None

    @model_validator(mode="after")
    def key_source(self):
        if (self.api_key_env is None) == (self.api_key_file is None):
            raise ValueError("backend requires exactly one key source")
        return self


class ParticipantConfig(FrozenModel):
    adapter: Literal["scripted", "llm", "human"] = "scripted"
    profile: str | None = None

    @model_validator(mode="after")
    def llm_profile(self):
        if (self.adapter == "llm") != (self.profile is not None):
            raise ValueError("only LLM participants require a profile")
        return self


class RunConfig(FrozenModel):
    schema_version: Literal[3] = 3
    backends: dict[str, BackendConfig] = Field(default_factory=dict)
    profiles: dict[str, LLMProfile] = Field(default_factory=dict)
    # Optional per-run overrides of a Scenario's cast. Credentials are never
    # copied into the Scenario or the run's public world definition.
    cast: dict[str, ParticipantConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def references(self):
        for name, profile in self.profiles.items():
            if profile.backend_id not in self.backends:
                raise ValueError(f"profile {name}: unknown backend {profile.backend_id}")
        for actor, binding in self.cast.items():
            if binding.profile and binding.profile not in self.profiles:
                raise ValueError(f"cast {actor}: unknown profile {binding.profile}")
        return self


def load_run_config(path: str | Path) -> RunConfig:
    config_path = Path(path).resolve()
    config = RunConfig.model_validate(load_mapping(config_path))
    # Key file paths are relative to their configuration file, not shell cwd.
    backends = {
        key: backend.model_copy(update={"api_key_file": str(config_path.parent / backend.api_key_file)})
        if backend.api_key_file else backend
        for key, backend in config.backends.items()
    }
    return config.model_copy(update={"backends": backends})


def read_api_key(path: str | Path) -> str:
    lines = [line.strip() for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError("API key file must contain exactly one non-empty line")
    return lines[0]
