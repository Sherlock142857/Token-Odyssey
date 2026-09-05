"""Nonblocking human participant. A local web server can drive this adapter later."""

from token_odyssey.agents.contracts import Decision, DecisionRequest, InputRequired
from token_odyssey.translators.human import HumanTranslator


class HumanAgent:
    def __init__(self, actor_id: str, translator: HumanTranslator):
        self.actor_id, self.translator = actor_id, translator
        self.pending_request: DecisionRequest | None = None
        self._submission: Decision | None = None

    def decide(self, request: DecisionRequest) -> Decision:
        if request.actor_id != self.actor_id:
            raise ValueError("HumanAgent cannot control another Character")
        if self.pending_request is not None and self.pending_request.request_id != request.request_id:
            raise ValueError("cannot replace an unanswered human request")
        self.pending_request = request.model_copy(deep=True)
        if self._submission is None:
            raise InputRequired()
        decision = self._submission
        self._submission, self.pending_request = None, None
        return decision

    def present(self) -> dict | None:
        return self.translator.present(self.pending_request) if self.pending_request else None

    def submit(self, request_id: str, actions: list[dict]) -> None:
        if self.pending_request is None or self.pending_request.request_id != request_id:
            raise ValueError("stale or unknown human request")
        if self._submission is not None:
            raise ValueError("this request already has a submission")
        batch = self.translator.actions_from_form(actions)
        self._submission = Decision(actor_id=self.actor_id, batch=batch)
