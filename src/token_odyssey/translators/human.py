"""Web-facing seam: view DTO out, form/button fields in. No terminal JSON UI."""

from token_odyssey.agents.contracts import DecisionRequest
from token_odyssey.kernel.actions.registry import ActionRegistry


class HumanTranslator:
    def __init__(self, registry: ActionRegistry):
        self.registry = registry

    def present(self, request: DecisionRequest) -> dict:
        return request.model_dump(mode="json")

    def actions_from_form(self, actions: list[dict]):
        return self.registry.parse_batch({"actions": actions})
