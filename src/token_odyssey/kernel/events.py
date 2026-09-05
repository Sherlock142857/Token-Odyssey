"""Committed facts and field-level perception cues, independent of language.

A Cue is a candidate observation, not a grant of the entire canonical Event.
Every cue has its own spatial anchor and disclosure threshold. This prevents a
departure witnessed in one room from revealing an unseen arrival elsewhere.
"""

from dataclasses import dataclass
from typing import Literal

from pydantic import Field, JsonValue, model_validator

from token_odyssey.common import FrozenModel
from token_odyssey.kernel.definitions import Coefficient
from token_odyssey.kernel.state import Change, World


class Issue(FrozenModel):
    code: str
    details: dict[str, JsonValue] = Field(default_factory=dict)


class Fact(FrozenModel):
    kind: str
    fields: dict[str, JsonValue] = Field(default_factory=dict)


class EvidenceAnchor(FrozenModel):
    object_id: str
    moment: Literal["before", "after"] = "after"


class Cue(FrozenModel):
    fact: Fact
    anchor_id: str
    moment: Literal["before", "after"] = "after"
    channel: Literal["visual", "audio"] = "visual"
    threshold: Coefficient = 0.5
    salience: float = Field(default=1, ge=0, le=10)
    requires: tuple[EvidenceAnchor, ...] = ()
    certain_for: tuple[str, ...] = ()
    only_for: tuple[str, ...] | None = None
    # Identification and localization are different disclosures. Neither copies
    # a hidden parent ID into a character's view without separate authorization.
    identifies: tuple[str, ...] = ()
    locates: tuple[str, ...] = ()


class EventDraft(FrozenModel):
    kind: str
    source: Literal["action", "world"] = "action"
    actor_id: str | None = None
    mechanic_id: str | None = None
    data: dict[str, JsonValue] = Field(default_factory=dict)
    signals: tuple[str, ...] = ()
    subject_ids: tuple[str, ...] = ()
    changes: tuple[Change, ...] = ()
    cues: tuple[Cue, ...] = ()

    @model_validator(mode="after")
    def author(self):
        if self.source == "action" and (self.actor_id is None or self.mechanic_id is not None):
            raise ValueError("action event requires actor_id and no mechanic_id")
        if self.source == "world" and (self.mechanic_id is None or self.actor_id is not None):
            raise ValueError("world event requires mechanic_id and no actor_id")
        return self


class WorldEvent(EventDraft):
    sequence: int = Field(ge=1)
    transaction_id: int = Field(ge=1)
    caused_by: int | None = None


class Transaction(FrozenModel):
    id: int = Field(ge=1)
    actor_id: str
    action_kind: str
    before_revision: int = Field(ge=0)
    after_revision: int = Field(ge=1)
    events: tuple[WorldEvent, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def root_event(self):
        root = self.events[0]
        if root.source != "action" or root.actor_id != self.actor_id or root.kind != self.action_kind or root.caused_by is not None:
            raise ValueError("transaction must start with its actor's action event")
        if self.after_revision != self.before_revision + 1:
            raise ValueError("transaction must advance one revision")
        if any(event.transaction_id != self.id for event in self.events):
            raise ValueError("event belongs to another transaction")
        return self


@dataclass(frozen=True)
class EventFrame:
    event: WorldEvent
    before: World
    after: World


@dataclass(frozen=True)
class ActionResult:
    accepted: bool
    transaction: Transaction | None = None
    frames: tuple[EventFrame, ...] = ()
    issues: tuple[Issue, ...] = ()
    notices: tuple[Issue, ...] = ()
    rescan_actor: bool = False
    ends_batch: bool = False

    @property
    def performed(self) -> bool:
        return self.transaction is not None
