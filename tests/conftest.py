from __future__ import annotations

from pathlib import Path

import pytest

from token_odyssey.inside_act.actions import build_builtin_registry
from token_odyssey.scenario import load_scenario


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def scenario():
    return load_scenario(ROOT / "scenarios" / "rainy_night.yaml")


@pytest.fixture
def relay_scenario():
    return load_scenario(ROOT / "scenarios" / "after_storm_relay.yaml")


@pytest.fixture
def registry():
    return build_builtin_registry()


@pytest.fixture
def wait_plan(registry):
    def build(thought: str = "等待"):
        return registry.parse_plan(
            {
                "private_thought": thought,
                "actions": [{"kind": "wait"}],
            }
        )

    return build
