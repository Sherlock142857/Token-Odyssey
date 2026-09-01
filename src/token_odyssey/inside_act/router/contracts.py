"""Turn-order strategy contract."""

from typing import Protocol


class TurnRouter(Protocol):
    def order(self, actor_ids: list[str], round_number: int) -> list[str]: ...
