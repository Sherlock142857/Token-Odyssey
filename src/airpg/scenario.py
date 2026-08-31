"""Scenario and secret loading helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from airpg.models import Scenario


def load_scenario(path: str | Path) -> Scenario:
    scenario_path = Path(path)
    with scenario_path.open("r", encoding="utf-8") as handle:
        raw: Any = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise ValueError(f"scenario root must be a mapping: {scenario_path}")
    return Scenario.model_validate(raw)


def read_api_key(path: str | Path) -> str:
    key_path = Path(path)
    lines = [line.strip() for line in key_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError(f"API key file must contain exactly one non-empty line: {key_path}")
    return lines[0]

