"""Provider-neutral JSON agents used outside the single-act runtime."""

import json
import re
from collections.abc import Callable
from threading import RLock
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from token_odyssey.config.models import RunConfig
from token_odyssey.llm.contracts import ChatMessage, ChatRole, LLMExchange, LLMRequest, LLMResponse
from token_odyssey.runtime.composition import build_backend

from .models import CampaignState
from .store import CampaignStore


T = TypeVar("T", bound=BaseModel)


def concise_validation_error(exc: Exception) -> str:
    """Render validation failures as repair instructions, without Pydantic noise."""
    if isinstance(exc, ValidationError):
        messages = []
        for detail in exc.errors(include_url=False, include_input=False):
            location = ".".join(str(part) for part in detail["loc"])
            message = detail["msg"]
            messages.append(f"{location}: {message}" if location else message)
        return "\n".join(messages)
    return re.sub(
        r"\n\s*For further information visit https://errors\.pydantic\.dev/\S+",
        "",
        str(exc),
    )


def parse_json_object(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if not lines or lines[-1].strip() != "```":
            raise ValueError("unclosed JSON code fence")
        text = "\n".join(lines[1:-1])
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("agent response must be a JSON object")
    return value


class CampaignLLMService:
    """Shared transports plus a durable response cache keyed by operation ID."""

    def __init__(self, config: RunConfig, store: CampaignStore, *, timeout: float = 120):
        self.config, self.store = config, store
        self.backends = {}
        self.timeout = timeout
        self._active: dict[str, dict] = {}
        self._active_lock = RLock()

    def active_exchanges(self) -> list[dict]:
        with self._active_lock:
            return [dict(value) for value in self._active.values()]

    def complete(self, profile_name: str, operation_id: str, actor_id: str,
                 messages: list[ChatMessage]) -> LLMResponse:
        cached = self.store.operation(operation_id)
        if cached is not None:
            return LLMResponse.model_validate(cached["response"])
        profile = self.config.profiles[profile_name]
        backend = self.backends.get(profile.backend_id)
        if backend is None:
            backend = build_backend(self.config.backends[profile.backend_id])
            if hasattr(backend, "client"):
                backend.client = backend.client.with_options(timeout=self.timeout, max_retries=0)
            self.backends[profile.backend_id] = backend
        request = LLMRequest(profile=profile, messages=list(messages), json_object=True)
        with self._active_lock:
            self._active[operation_id] = {
                "actor_id": actor_id, "request_id": operation_id,
                "profile": profile_name, "request": request.model_dump(mode="json"),
            }
        try:
            response = backend.complete(request)
        except Exception as exc:
            try:
                self.store.record("orchestration_exchanges", LLMExchange(
                    actor_id=actor_id, request_id=operation_id, request=request, error=type(exc).__name__))
            finally:
                with self._active_lock:
                    self._active.pop(operation_id, None)
            raise
        try:
            # Persist before the caller mutates its stage. A resumed operation
            # then reuses the exact response instead of paying for or appending
            # it twice.
            self.store.save_operation(operation_id, {
                "actor_id": actor_id, "profile": profile_name,
                "request": request, "response": response,
            })
            self.store.record("orchestration_exchanges", LLMExchange(
                actor_id=actor_id, request_id=operation_id, request=request, response=response))
            return response
        finally:
            with self._active_lock:
                self._active.pop(operation_id, None)

    def typed_call(self, *, profile_name: str, operation_prefix: str, actor_id: str,
                   system_prompt: str, user_prompt: str, result_type: type[T], retries: int) -> T:
        messages = [ChatMessage(role=ChatRole.SYSTEM, content=system_prompt),
                    ChatMessage(role=ChatRole.USER, content=user_prompt)]
        error = ""
        for attempt in range(1, retries + 1):
            if attempt > 1:
                messages.append(ChatMessage(
                    role=ChatRole.USER,
                    content=f"上一个 JSON 无法通过校验：{error}\n请按原契约修正，只输出完整 JSON 对象。",
                ))
            operation_id = f"{operation_prefix}-attempt-{attempt}"
            response = self.complete(profile_name, operation_id, actor_id, messages)
            messages.append(ChatMessage(role=ChatRole.ASSISTANT, content=response.content))
            try:
                return result_type.model_validate(parse_json_object(response.content))
            except (ValueError, TypeError) as exc:
                error = concise_validation_error(exc)
        raise ValueError(f"{actor_id} did not return a valid response after {retries} attempts: {error}")


class DirectorSession:
    """One append-only director conversation for the entire campaign."""

    def __init__(self, llm: CampaignLLMService, state: CampaignState, profile_name: str,
                 system_prompt: str, save: Callable[[], None]):
        self.llm, self.state, self.profile_name, self.save = llm, state, profile_name, save
        if not self.state.director_messages:
            self.state.director_messages.append(ChatMessage(role=ChatRole.SYSTEM, content=system_prompt))
            self.save()

    def call(self, operation_prefix: str, user_prompt: str, result_type: type[T], retries: int) -> T:
        error = ""
        contract = (
            f"\n\n[本次唯一输出契约]\n"
            f"当前调用只允许返回 {result_type.__name__}，不得选用系统消息中的其他 schema。\n"
            f"{json.dumps(result_type.model_json_schema(), ensure_ascii=False)}\n"
            "[本次唯一输出契约结束]"
        )
        for attempt in range(1, retries + 1):
            operation_id = f"{operation_prefix}-attempt-{attempt}"
            marker = f"[operation_id: {operation_id}]"
            if operation_id not in self.state.director_completed_ops:
                prompt = (user_prompt + contract) if attempt == 1 else (
                    f"上一个 JSON 无法通过校验：{error}\n请按原契约修正，只输出完整 JSON 对象。"
                    + contract
                )
                if not any(message.role == ChatRole.USER and marker in message.content
                           for message in self.state.director_messages):
                    self.state.director_messages.append(ChatMessage(
                        role=ChatRole.USER, content=f"{marker}\n{prompt}"))
                    self.save()
                response = self.llm.complete(
                    self.profile_name, operation_id, "director", self.state.director_messages)
                self.state.director_messages.append(ChatMessage(
                    role=ChatRole.ASSISTANT, content=response.content))
                self.state.director_completed_ops.add(operation_id)
                self.save()
            cached = self.llm.store.operation(operation_id)
            if cached is None:
                raise RuntimeError(f"missing cached director operation {operation_id}")
            content = cached["response"]["content"]
            try:
                return result_type.model_validate(parse_json_object(content))
            except (ValueError, TypeError) as exc:
                error = concise_validation_error(exc)
        raise ValueError(f"director did not return a valid response after {retries} attempts: {error}")
