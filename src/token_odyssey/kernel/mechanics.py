"""Deterministic immediate reactions operating on the current transaction draft.

Rules listen to explicit signals; merely having a true condition does not run a
rule on every turn. The queue establishes causal order and a bounded closure.
"""

from token_odyssey.kernel.definitions import MechanicRule
from token_odyssey.kernel.events import Cue, EventDraft, Fact, WorldEvent
from token_odyssey.kernel.fluents import Fluents
from token_odyssey.kernel.state import World, change_to


class MechanicsEngine:
    def matching(self, world: World, event: WorldEvent):
        for rule in world.definition.mechanics:
            if rule.trigger not in event.signals:
                continue
            if rule.subject_id and rule.subject_id not in event.subject_ids:
                continue
            if rule.once and world.state.fired_rules.get(rule.id):
                continue
            if all(Fluents(world).satisfies(atom) for atom in rule.when):
                yield rule

    def reaction(self, world: World, rule: MechanicRule) -> EventDraft:
        changes = []
        for effect in rule.effects:
            table = {"open": "openings", "locked": "locks", "flag": "flags"}[effect.kind]
            change = change_to(world.state, table, effect.subject_id, effect.value)
            if change.before != change.after:
                changes.append(change)
        if rule.once:
            changes.append(change_to(world.state, "fired_rules", rule.id, True))
        cues = []
        if rule.visual_description:
            cues.append(Cue(fact=Fact(kind="mechanism_seen", fields={"description": rule.visual_description}),
                            anchor_id=rule.source_id, threshold=0.3, salience=rule.visibility))
        if rule.sound_description:
            # Hearing a mechanism never grants its source Item's name or position.
            cues.append(Cue(fact=Fact(kind="mechanism_heard", fields={"description": rule.sound_description}),
                            anchor_id=rule.source_id, channel="audio", threshold=0.1, salience=rule.audibility))
        changed_subjects = tuple(c.key for c in changes if c.table != "fired_rules")
        return EventDraft(kind="mechanism", source="world", mechanic_id=rule.id,
                          data={"source_id": rule.source_id}, changes=tuple(changes), cues=tuple(cues),
                          signals=("state_changed",) if changed_subjects else (), subject_ids=changed_subjects)
