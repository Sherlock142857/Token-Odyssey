"""Seeded shuffled-round routing."""

import random


class ShuffledRoundRouter:
    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)

    def order(self, actor_ids: list[str], round_number: int) -> list[str]:
        result = list(actor_ids)
        self.rng.shuffle(result)
        return result
