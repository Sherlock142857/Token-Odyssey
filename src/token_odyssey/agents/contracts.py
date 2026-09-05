"""Controller-neutral port shared by models, scripts and a future web player."""

from typing import Protocol

from pydantic import model_validator

from token_odyssey.common import FrozenModel
from token_odyssey.kernel.actions.base import ActionBatch
from token_odyssey.kernel.events import Issue
from token_odyssey.perception.models import ActorView


class DecisionRequest(FrozenModel):
    request_id: str
    actor_id: str
    view: ActorView
    issues: tuple[Issue, ...] = ()


class Decision(FrozenModel):
    actor_id: str
    batch: ActionBatch | None = None
    error: str | None = None

    @model_validator(mode="after")
    def one_result(self):
        if (self.batch is None) == (self.error is None):
            raise ValueError("Decision requires exactly one of batch or error")
        return self


class Participant(Protocol):
    def decide(self, request: DecisionRequest) -> Decision: ...


class AgentUnavailableError(RuntimeError):
    """Transport/service failure. Do not retry it as a malformed game action."""


class InputRequired(Exception):
    """A Human participant has published its request and awaits a submission."""
