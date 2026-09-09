"""Routing selects one participant at a time and can consume committed events."""

import random
from collections.abc import Callable
from typing import Protocol

from token_odyssey.kernel.events import WorldEvent
from token_odyssey.perception.models import Observation
from token_odyssey.runtime.routing_policy import DEFAULT_DIRECT_WEIGHTS, DEFAULT_FACT_WEIGHTS, RoutingPolicy


class TurnRouter(Protocol):
    def next_actor(self, actor_ids: tuple[str, ...], recent_events: tuple[WorldEvent, ...]) -> str: ...


class ShuffledRouter:
    """Baseline fairness: one shuffled bag per round, available for comparisons."""

    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.pending: list[str] = []
        self.round = 0
        self.last_decision: dict = {}

    def next_actor(self, actor_ids: tuple[str, ...], recent_events: tuple[WorldEvent, ...]) -> str:
        del recent_events
        _validate_actors(actor_ids)
        if not self.pending:
            self.pending = list(actor_ids)
            self.rng.shuffle(self.pending)
            self.round += 1
        selected = self.pending.pop()
        self.last_decision = {"strategy": "shuffled", "round": self.round, "actor_id": selected}
        return selected


class WeightedRouter:
    """Independent weighted draws with configurable immediate-repeat policy."""

    def __init__(self, seed: int, policy: RoutingPolicy | None = None):
        self.policy = policy or RoutingPolicy(strategy="weighted")
        self.rng = random.Random(seed)
        self.last_actor: str | None = None
        self.last_decision: dict = {}

    def next_actor(self, actor_ids: tuple[str, ...], recent_events: tuple[WorldEvent, ...]) -> str:
        del recent_events
        _validate_actors(actor_ids)
        eligible = list(actor_ids)
        if not self.policy.allow_immediate_repeat and len(actor_ids) > 1:
            eligible = [actor for actor in eligible if actor != self.last_actor]
        weights = {actor: self.policy.actor_weights.get(actor, 1.0) for actor in actor_ids}
        selected, roll, probabilities = _weighted_draw(self.rng, eligible, weights)
        self.last_decision = {
            "strategy": "weighted",
            "actors": {actor: {"weight": weight, "probability": probabilities.get(actor, 0.0)}
                       for actor, weight in weights.items()},
            "roll": roll,
            "actor_id": selected,
        }
        self.last_actor = selected
        return selected


# Values describe the urgency of a fact the character actually perceived.
# Multiple cues/actions in one turn compete by maximum, never by repetition.
FACT_WEIGHT = dict(DEFAULT_FACT_WEIGHTS)
DIRECT_WEIGHT = dict(DEFAULT_DIRECT_WEIGHTS)


def _validate_actors(actor_ids: tuple[str, ...]) -> None:
    if not actor_ids or len(set(actor_ids)) != len(actor_ids):
        raise ValueError("router requires a nonempty set of distinct Characters")


def _weighted_draw(rng: random.Random, eligible: list[str], weights: dict[str, float]):
    total = sum(weights[actor] for actor in eligible)
    roll = rng.random()
    cumulative = 0.0
    selected = eligible[-1]
    probabilities = {actor: weights[actor] / total for actor in eligible}
    for actor in eligible:
        cumulative += probabilities[actor]
        if roll < cumulative:
            selected = actor
            break
    return selected, roll, probabilities


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
                weight = self.policy.fact_weights.get(fact.kind, 0.0)
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
                    weight = max(weight, self.policy.direct_weights.get(event.kind, 0.0))
                if weight > best:
                    best = weight
                    reason = {"event_sequence": event.sequence, "fact": fact.kind,
                              "direct": direct, "interest": interest, "impulse": weight}
            if best > self.pending.get(actor, 0):
                self.pending[actor] = best
                self.reasons[actor] = [reason]

    def next_actor(self, actor_ids: tuple[str, ...], recent_events: tuple[WorldEvent, ...]) -> str:
        _validate_actors(actor_ids)
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
            raw_impulse = self.pending.get(actor, 0)
            effective_impulse = min(self.policy.attention_cap, raw_impulse * self.policy.impulse_scale)
            attention = min(self.policy.attention_cap,
                            self.attention.get(actor, 0) * self.policy.decay + effective_impulse)
            self.attention[actor] = attention
            if effective_impulse >= 1:
                self.idle[actor] = 0
            age = self.age.setdefault(actor, 0)
            base = self.policy.actor_weights.get(actor, 1.0) / (
                1 + self.policy.idle_penalty * self.idle.get(actor, 0)
            )
            rows[actor] = {"age": age, "base": base, "attention": attention,
                           "impulse": raw_impulse, "effective_impulse": effective_impulse,
                           "weight": base + self.policy.age_weight * age + attention,
                           "reasons": self.reasons.get(actor, [])}
        eligible = list(actor_ids)
        if not self.policy.allow_immediate_repeat and len(actor_ids) > 1:
            eligible = [actor for actor in eligible if actor != self.last_actor]
        overdue = [a for a in eligible if self.age[a] >= self.policy.fairness_rounds * len(actor_ids)]
        if overdue:
            oldest = max(self.age[a] for a in overdue)
            eligible = [a for a in overdue if self.age[a] == oldest]
        selected, roll, probabilities = _weighted_draw(
            self.rng, eligible, {actor: row["weight"] for actor, row in rows.items()}
        )
        for actor in actor_ids:
            rows[actor]["probability"] = probabilities.get(actor, 0.0)
            self.age[actor] = 0 if actor == selected else self.age[actor] + 1
        self.last_decision = {"strategy": "interaction", "actors": rows, "roll": roll,
                              "fairness_override": bool(overdue), "actor_id": selected}
        self.attention[selected] = 0
        self.last_actor = selected
        self.pending.clear()
        self.reasons.clear()
        return selected


RouterFactory = Callable[[int, RoutingPolicy], TurnRouter]
ROUTER_FACTORIES: dict[str, RouterFactory] = {}


def register_router_strategy(name: str, factory: RouterFactory, *, replace: bool = False) -> None:
    """Register a routing strategy at the composition seam."""

    if not name or (name in ROUTER_FACTORIES and not replace):
        raise ValueError(f"router strategy already registered: {name!r}")
    ROUTER_FACTORIES[name] = factory


def build_router(seed: int, policy: RoutingPolicy) -> TurnRouter:
    try:
        factory = ROUTER_FACTORIES[policy.strategy]
    except KeyError as exc:
        available = ", ".join(ROUTER_FACTORIES)
        raise ValueError(f"unknown router strategy {policy.strategy!r}; available: {available}") from exc
    return factory(seed, policy)


register_router_strategy("shuffled", lambda seed, policy: ShuffledRouter(seed))
register_router_strategy("weighted", lambda seed, policy: WeightedRouter(seed, policy))
register_router_strategy("interaction", lambda seed, policy: InteractionWeightedRouter(seed, policy))
