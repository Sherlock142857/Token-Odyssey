from __future__ import annotations

from pathlib import Path

import pytest

from airpg.models import Scenario
from airpg.scenario import load_scenario


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def scenario() -> Scenario:
    return load_scenario(ROOT / "scenarios" / "rainy_night.yaml")

