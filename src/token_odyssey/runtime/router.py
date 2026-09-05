"""Routing selects one participant at a time and can consume committed events."""

import random
from typing import Protocol

from token_odyssey.kernel.events import WorldEvent
from token_odyssey.perception.models import Observation
from token_odyssey.runtime.routing_policy import RoutingPolicy


class TurnRouter(Protocol):
    def next_actor(self, actor_ids: tuple[str, ...], recent_events: tuple[WorldEvent, ...]) -> str: ...


class ShuffledRouter:
    """Baseline fairness: one shuffled bag per round, available for comparisons."""

    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.pending: list[str] = []

    def next_actor(self, actor_ids: tuple[str, ...], recent_events: tuple[WorldEvent, ...]) -> str:
        del recent_events
        if not self.pending:
            self.pending = list(actor_ids)
            self.rng.shuffle(self.pending)
        return self.pending.pop()


# Values describe the urgency of a fact the character actually perceived.
# Multiple cues/actions in one turn compete by maximum, never by repetition.
FACT_WEIGHT = {
    "voice": 0.2, "speaker": 0.3, "speech": 0.8, "handling": 0.25,
    "take": 1.0, "give": 1.2, "place": 0.8, "hide": 1.2,
    "show": 1.0, "item_location": 0.4, "search": 0.7, "discovery": 1.5,
    "open": 1.0, "close": 0.8, "lock": 1.2, "unlock": 1.2,
    "install": 1.4, "operate": 1.0, "arrival": 1.2, "departure": 0.8,
    "mechanism_seen": 1.8, "mechanism_heard": 1.2,
}
DIRECT_WEIGHT = {"say": 4.0, "show": 4.0, "give": 5.0}


class InteractionWeightedRouter:
    """Evidence-gated attention + age, with bounded starvation and seeded draws.

    observe() accepts only projections of committed events. Canonical fields are
    used solely to recognize an addressed participant, never to infer witnesses
    or interests from hidden items, rule IDs, prose, or flags.
    """

    def __init__(self, seed: int, policy: RoutingPolicy | None = None):
        self.policy = policy or RoutingPolicy()
        self.rng = random.Random(seed)
        self.attention: dict[str, float] = {}
        self.age: dict[str, int] = {}
        self.idle: dict[str, int] = {}
        self.pending: dict[str, float] = {}
        self.reasons: dict[str, list[dict]] = {}
        self.last_actor: str | None = None
        self.last_sequence = 0
        self.last_decision: dict = {}

    def observe(self, events: tuple[WorldEvent, ...], observations: tuple[Observation, ...]) -> None:
        fresh = {e.sequence: e for e in events if e.sequence > self.last_sequence}
        if not fresh:
            return
        self.last_sequence = max(fresh)
        for observation in observations:
            event = fresh.get(observation.source_event_sequence)
            if event is None:
                continue
            actor = observation.observer_id
            interests = self.policy.interests.get(actor, {})
            best, reason = 0.0, None
            for fact in observation.facts:
                # Own ordinary actions are not invitations to act again. A real
                # discovery or world reaction can still motivate a later turn.
                if event.actor_id == actor and fact.kind != "discovery":
                    continue
                weight = FACT_WEIGHT.get(fact.kind, 0.0)
                if not weight:
                    continue
                ids = {v for k, v in fact.fields.items() if k.endswith("_id") and isinstance(v, str)}
                interest = max((interests.get(key, 0) for key in ids), default=0)
                weight *= 1 + interest
                direct = (
                    event.kind == "say" and fact.kind == "speech"
                    and fact.fields.get("actor_id") == event.actor_id
                    and actor in event.data.get("listener_ids", [])
                ) or (
                    event.kind == "show" and fact.kind == "show"
                    and actor in event.data.get("observer_ids", [])
                ) or (
                    event.kind == "give" and fact.kind == "give"
                    and actor == fact.fields.get("recipient_id")
                )
                if direct:
                    weight = max(weight, DIRECT_WEIGHT[event.kind])
                if weight > best:
                    best = weight
                    reason = {"event_sequence": event.sequence, "fact": fact.kind,
                              "direct": direct, "interest": interest, "impulse": weight}
            if best > self.pending.get(actor, 0):
                self.pending[actor] = best
                self.reasons[actor] = [reason]

    def next_actor(self, actor_ids: tuple[str, ...], recent_events: tuple[WorldEvent, ...]) -> str:
        if not actor_ids or len(set(actor_ids)) != len(actor_ids):
            raise ValueError("router requires a nonempty set of distinct Characters")
        # Canonical events without observations cannot manufacture attention.
        # They can describe the previous actor's own inactivity, once per turn.
        own = [e for e in recent_events if e.source == "action" and e.actor_id == self.last_actor]
        if self.last_actor is not None:
            inactive = not own or all(e.kind == "wait" for e in own)
            self.idle[self.last_actor] = min(3, self.idle.get(self.last_actor, 0) + 1) if inactive else 0
        for table in (self.attention, self.age, self.idle):
            for actor in set(table) - set(actor_ids):
                del table[actor]
        rows = {}
        for actor in actor_ids:
            impulse = min(self.policy.attention_cap, self.pending.get(actor, 0))
            attention = min(self.policy.attention_cap, self.attention.get(actor, 0) * self.policy.decay + impulse)
            self.attention[actor] = attention
            if impulse >= 1:
                self.idle[actor] = 0
            age = self.age.setdefault(actor, 0)
            base = 1 / (1 + 0.55 * self.idle.get(actor, 0))
            rows[actor] = {"age": age, "base": base, "attention": attention, "impulse": impulse,
                           "weight": base + self.policy.age_weight * age + attention,
                           "reasons": self.reasons.get(actor, [])}
        eligible = [a for a in actor_ids if a != self.last_actor] if len(actor_ids) > 1 else list(actor_ids)
        overdue = [a for a in eligible if self.age[a] >= self.policy.fairness_rounds * len(actor_ids)]
        if overdue:
            oldest = max(self.age[a] for a in overdue)
            eligible = [a for a in overdue if self.age[a] == oldest]
        total = sum(rows[a]["weight"] for a in eligible)
        roll = self.rng.random()
        cumulative = 0.0
        selected = eligible[-1]
        for actor in eligible:
            cumulative += rows[actor]["weight"] / total
            if roll < cumulative:
                selected = actor
                break
        for actor in actor_ids:
            rows[actor]["probability"] = rows[actor]["weight"] / total if actor in eligible else 0.0
            self.age[actor] = 0 if actor == selected else self.age[actor] + 1
        self.last_decision = {"strategy": "weighted", "actors": rows, "roll": roll,
                              "fairness_override": bool(overdue), "actor_id": selected}
        self.attention[selected] = 0
        self.last_actor = selected
        self.pending.clear()
        self.reasons.clear()
        return selected
