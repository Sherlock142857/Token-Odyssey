"""Shared strict-model configuration for public schemas."""

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)
