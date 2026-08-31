"""Structured developer-mode tracing."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel
from rich.console import Console
from rich.panel import Panel


class DebugSink(Protocol):
    def emit(self, category: str, message: str, payload: Any | None = None) -> None: ...


class NullDebugSink:
    def emit(self, category: str, message: str, payload: Any | None = None) -> None:
        return None


@dataclass
class MemoryDebugSink:
    records: list[dict[str, Any]] = field(default_factory=list)

    def emit(self, category: str, message: str, payload: Any | None = None) -> None:
        self.records.append({"category": category, "message": message, "payload": payload})


class ConsoleDebugSink:
    def __init__(
        self,
        console: Console | None = None,
        categories: set[str] | None = None,
    ) -> None:
        self.console = console or Console()
        self.categories = categories

    def emit(self, category: str, message: str, payload: Any | None = None) -> None:
        if self.categories is not None and category not in self.categories:
            return
        body = message
        if payload is not None:
            body += "\n" + json.dumps(_jsonable(payload), ensure_ascii=False, indent=2)
        self.console.print(Panel(body, title=f"developer · {category}", border_style="dim cyan"))


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value
