"""World entities. Controller type (human or model) is deliberately absent."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from token_odyssey.inside_act.domain.common import StrictModel


class EntityKind(StrEnum):
    ROOM = "room"
    CHARACTER = "character"
    ITEM = "item"


class SpatialEntity(StrictModel):
    id: str = Field(min_length=1)
    kind: EntityKind
    name: str = Field(min_length=1)
    description: str = ""
    is_container: bool
    container_visibility: float = Field(ge=0.0, le=1.0)
    protector_id: str | None = None


class Room(SpatialEntity):
    kind: Literal[EntityKind.ROOM] = EntityKind.ROOM
    is_container: Literal[True] = True


class Character(SpatialEntity):
    kind: Literal[EntityKind.CHARACTER] = EntityKind.CHARACTER
    is_container: Literal[True] = True
    size_class: int = Field(ge=1, le=10)
    personality: str
    appearance: str = ""
    traits: list[str] = Field(default_factory=list)
    pre_act_memory: str = ""
    act_memories: list[str] = Field(default_factory=list)
    private_goal: str = ""


class Item(SpatialEntity):
    kind: Literal[EntityKind.ITEM] = EntityKind.ITEM
    size_class: int = Field(ge=1, le=10)
    intrinsic_visibility: float = Field(default=1.0, ge=0.0, le=1.0)


Entity = Annotated[Room | Character | Item, Field(discriminator="kind")]
