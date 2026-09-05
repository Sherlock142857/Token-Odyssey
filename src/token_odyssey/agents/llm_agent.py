"""Model session orchestration. Translation and transport have independent seams."""

from collections.abc import Callable

from token_odyssey.agents.contracts import AgentUnavailableError, Decision, DecisionRequest
from token_odyssey.llm.contracts import ChatMessage, ChatRole, LLMBackend, LLMExchange, LLMProfile, LLMRequest
from token_odyssey.translators.llm import LLMTranslator


class LLMAgent:
    def __init__(self, actor_id: str, translator: LLMTranslator, backend: LLMBackend,
                 profile: LLMProfile, on_exchange: Callable[[LLMExchange], None] | None = None):
        self.actor_id, self.translator = actor_id, translator
        self.backend, self.profile = backend, profile
        self.on_exchange = on_exchange or (lambda exchange: None)
        self.messages: list[ChatMessage] = []

    def decide(self, request: DecisionRequest) -> Decision:
        if request.actor_id != self.actor_id:
            raise ValueError("LLMAgent cannot decide for another Character")
        if not self.messages:
            self.messages.append(ChatMessage(role=ChatRole.SYSTEM, content=self.translator.system_prompt()))
        self.messages.append(ChatMessage(role=ChatRole.USER, content=self.translator.render_request(request)))
        call = LLMRequest(profile=self.profile, messages=list(self.messages))
        try:
            response = self.backend.complete(call)
        except Exception as exc:
            self.on_exchange(LLMExchange(actor_id=self.actor_id, request_id=request.request_id, request=call,
                                         error=type(exc).__name__))
            raise AgentUnavailableError(f"backend unavailable: {type(exc).__name__}") from exc
        self.messages.append(ChatMessage(role=ChatRole.ASSISTANT, content=response.content))
        self.on_exchange(LLMExchange(actor_id=self.actor_id, request_id=request.request_id,
                                     request=call, response=response))
        try:
            batch = self.translator.parse_response(response.content)
        except ValueError as exc:
            return Decision(actor_id=self.actor_id, error=str(exc))
        return Decision(actor_id=self.actor_id, batch=batch)
