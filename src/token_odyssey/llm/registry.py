"""Explicit dictionaries for backend instances and named model modes."""

from __future__ import annotations

from token_odyssey.llm.contracts import LLMBackend, LLMProfile


class LLMBackendRegistry:
    def __init__(self, backends: dict[str, LLMBackend]) -> None:
        self._backends = dict(backends)

    def get(self, backend_id: str) -> LLMBackend:
        try:
            return self._backends[backend_id]
        except KeyError as exc:
            raise KeyError(f"unknown LLM backend {backend_id!r}") from exc


class LLMProfileRegistry:
    def __init__(self, profiles: dict[str, LLMProfile]) -> None:
        self._profiles = dict(profiles)

    def get(self, mode: str) -> LLMProfile:
        try:
            return self._profiles[mode]
        except KeyError as exc:
            raise KeyError(f"unknown LLM mode {mode!r}") from exc
