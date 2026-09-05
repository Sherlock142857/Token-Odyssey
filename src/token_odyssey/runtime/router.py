"""Routing selects one participant at a time and can consume committed events."""

import random
from typing import Protocol

from token_odyssey.kernel.events import WorldEvent


class TurnRouter(Protocol):
    def next_actor(self, actor_ids: tuple[str, ...], recent_events: tuple[WorldEvent, ...]) -> str: ...


class ShuffledRouter:
    """Baseline fairness: one shuffled bag per round. No interaction weights yet."""

    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.pending: list[str] = []

    def next_actor(self, actor_ids: tuple[str, ...], recent_events: tuple[WorldEvent, ...]) -> str:
        # A future weighted strategy may use recipient_id in a committed give.
        # Its implementation can change without touching Harness or actions.
        del recent_events
        if not self.pending:
            self.pending = list(actor_ids)
            self.rng.shuffle(self.pending)
        return self.pending.pop()
