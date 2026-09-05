"""Shared serialization rules; no world, agent, or transport dependencies."""

from pydantic import BaseModel, ConfigDict


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class FrozenModel(Model):
    model_config = ConfigDict(extra="forbid", frozen=True)
