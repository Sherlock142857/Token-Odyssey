"""Action contract: typed intent -> Poss -> direct effect; no state ownership."""

from dataclasses import dataclass
from typing import ClassVar, Generic, Literal, TypeVar

from pydantic import Field, SerializeAsAny

from token_odyssey.common import FrozenModel
from token_odyssey.kernel.definitions import Character, Item
from token_odyssey.kernel.events import Cue, EventDraft, EvidenceAnchor, Fact, Issue
from token_odyssey.kernel.fluents import Fluents
from token_odyssey.kernel.state import World


class Intent(FrozenModel):
    kind: str
    amplitude: Literal["subtle", "normal", "overt"] = "normal"


class ActionBatch(FrozenModel):
    # Private cognition is adapter/runtime data and is never a WorldEvent.
    private_thought: str = ""
    actions: tuple[SerializeAsAny[Intent], ...] = Field(min_length=1)


@dataclass(frozen=True)
class EffectPlan:
    event: EventDraft | None
    notices: tuple[Issue, ...] = ()
    rescan_actor: bool = False
    ends_batch: bool = False


@dataclass(frozen=True)
class ActionContext:
    actor_id: str
    world: World

    @property
    def fluents(self) -> Fluents:
        return Fluents(self.world)


class Rejected(ValueError):
    def __init__(self, code: str, **details):
        self.issue = Issue(code=code, details=details)
        super().__init__(code)


def require(condition: bool, code: str, **details) -> None:
    if not condition:
        raise Rejected(code, **details)


def item(context: ActionContext, item_id: str, *, held: bool = False) -> Item:
    obj = context.world.definition.entities.get(item_id)
    require(isinstance(obj, Item), "EXPECTED_ITEM", item_id=item_id)
    assert isinstance(obj, Item)
    if held:
        require(context.fluents.controller(item_id) == context.actor_id, "NOT_HELD", item_id=item_id)
    reachable(context, item_id)
    return obj


def reachable(context: ActionContext, object_id: str) -> None:
    d = context.world.definition
    require(object_id in d.entities or object_id in d.passages, "UNKNOWN_OBJECT", object_id=object_id)
    # Deliberately do not return a hidden object's actual room or blocking parent.
    problem = context.fluents.access_problem(context.actor_id, object_id)
    if problem:
        raise Rejected(problem, object_id=object_id)


def colocated(context: ActionContext, character_id: str) -> None:
    require(isinstance(context.world.definition.entities.get(character_id), Character),
            "EXPECTED_CHARACTER", character_id=character_id)
    require(context.actor_id != character_id, "SELF_TARGET")
    require(context.fluents.same_room(context.actor_id, character_id), "NOT_COLOCATED", character_id=character_id)
    reachable(context, character_id)


T = TypeVar("T", bound=Intent)


class Action(Generic[T]):
    kind: ClassVar[str]
    intent_type: type[T]
    # Each action owns its amplitude response, rather than a global multiplier.
    salience: ClassVar[dict[str, float]] = {"subtle": 0.3, "normal": 1.0, "overt": 1.5}

    def references(self, intent: T) -> set[str]:
        return {v for k, v in intent.model_dump().items() if k.endswith("_id") and isinstance(v, str)}

    def poss(self, context: ActionContext, intent: T) -> tuple[Issue, ...]:
        try:
            self.check(context, intent)
        except Rejected as exc:
            return (exc.issue,)
        return ()

    def check(self, context: ActionContext, intent: T) -> None:
        raise NotImplementedError

    def effects(self, context: ActionContext, intent: T) -> EffectPlan:
        raise NotImplementedError

    def cue(self, intent: T, kind: str, anchor_id: str, fields: dict, **kwargs) -> Cue:
        # A detailed fact naming several objects needs evidence for each of them,
        # not just the most visible one. Explicit private receipts can bypass
        # visual evidence for their participants, but never for bystanders.
        moment = kwargs.get("moment", "after")
        kwargs.setdefault("requires", tuple(EvidenceAnchor(object_id=value, moment=moment)
                          for key, value in fields.items()
                          if key.endswith("_id") and isinstance(value, str) and value != anchor_id))
        return Cue(fact=Fact(kind=kind, fields=fields), anchor_id=anchor_id,
                   salience=self.salience[intent.amplitude], **kwargs)
