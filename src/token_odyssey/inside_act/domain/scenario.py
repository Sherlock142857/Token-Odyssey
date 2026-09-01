"""Within-Act authored scenario and canonical v2 loader model."""

from pydantic import Field

from token_odyssey.inside_act.domain.common import StrictModel
from token_odyssey.inside_act.domain.spatial import WorldState


class Scenario(StrictModel):
    schema_version: int = Field(default=2, ge=2, le=2)
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    world_history: str
    act_background: str
    action_guidance: str = ""
    max_rounds: int = Field(default=50, ge=1, le=10000)
    seed: int = 7
    world: WorldState
