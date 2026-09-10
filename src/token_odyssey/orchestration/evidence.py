"""Pure projections from committed Act records into Campaign evidence."""

from typing import Any

from token_odyssey.interfaces.web.presentation import event_text
from token_odyssey.kernel.events import Fact
from token_odyssey.scenario import Scenario
from token_odyssey.translators.language import render_observation


def state_delta(initial: dict[str, Any], final: dict[str, Any]) -> list[dict[str, Any]]:
    """Return changed world-state fields without interpreting their meaning."""

    changes = []
    for table in ("placements", "openings", "locks", "connections", "flags", "fired_rules"):
        before = initial.get(table, {})
        after = final.get(table, {})
        if not isinstance(before, dict) or not isinstance(after, dict):
            continue
        for key in sorted(set(before) | set(after)):
            old, new = before.get(key), after.get(key)
            if old != new:
                changes.append({"table": table, "key": key, "before": old, "after": new})
    return changes


def committed_timeline(transactions: list[dict[str, Any]], scenario: Scenario) -> list[dict[str, Any]]:
    """Render the authoritative committed event sequence for a world summary."""

    labels = {object_id: obj.name for object_id, obj in {**scenario.world.entities, **scenario.world.passages}.items()}
    timeline = []
    for transaction in transactions:
        for event in transaction.get("events", []):
            row = {
                "sequence": event.get("sequence"),
                "transaction_id": event.get("transaction_id", transaction.get("id")),
                "kind": event.get("kind"),
                "text": event_text(event, labels),
            }
            for field in ("actor_id", "mechanic_id", "caused_by"):
                if event.get(field) is not None:
                    row[field] = event[field]
            if event.get("changes"):
                row["changes"] = event["changes"]
            timeline.append(row)
    return timeline


def compact_character_observations(
    actor_id: str, observations: list[dict[str, Any]], scenario: Scenario
) -> dict[str, list[dict[str, Any]]]:
    """Keep only evidence actually authorized to one character, with provenance."""

    labels = {object_id: obj.name for object_id, obj in {**scenario.world.entities, **scenario.world.passages}.items()}
    event_rows: dict[int, dict[str, Any]] = {}
    visited_rooms: list[dict[str, Any]] = []
    recognized_entities: list[dict[str, Any]] = []
    seen_rooms: set[str] = set()
    seen_details: set[tuple[Any, str]] = set()
    for observation in observations:
        if observation.get("observer_id") != actor_id:
            continue
        row_labels = {**labels, **observation.get("labels", {})}
        row_labels.update(
            {
                entity["id"]: entity["name"]
                for entity in observation.get("entities", [])
                if isinstance(entity, dict) and entity.get("id") and entity.get("name")
            }
        )
        source = observation.get("source")
        facts = observation.get("facts", [])
        if source == "event":
            sequence = observation.get("source_event_sequence")
            if not isinstance(sequence, int):
                raise ValueError("event observation is missing source_event_sequence")
            event_row = event_rows.setdefault(sequence, {"event_sequence": sequence, "observed_facts": []})
            rendered = render_observation(tuple(Fact.model_validate(fact) for fact in facts), row_labels)
            for rendered_text in rendered:
                if rendered_text not in event_row["observed_facts"]:
                    event_row["observed_facts"].append(rendered_text)
        if source == "room":
            for fact in facts:
                if fact.get("kind") != "location":
                    continue
                room_id = fact.get("fields", {}).get("room_id")
                if isinstance(room_id, str) and room_id not in seen_rooms:
                    seen_rooms.add(room_id)
                    visited_rooms.append({"id": room_id, "name": row_labels.get(room_id, room_id)})
        if source not in {"event", "scan", "inventory"}:
            continue
        for entity in observation.get("entities", []):
            if not isinstance(entity, dict) or entity.get("kind") == "character":
                continue
            entity_id = entity.get("id")
            description = entity.get("description")
            if not isinstance(entity_id, str) or not isinstance(description, str) or not description.strip():
                continue
            marker = (entity_id, description)
            if marker in seen_details:
                continue
            seen_details.add(marker)
            recognized_entities.append(
                {"id": entity_id, "name": entity.get("name", entity_id), "description": description}
            )
    return {
        "observed_events": [row for row in event_rows.values() if row["observed_facts"]],
        "visited_rooms": visited_rooms,
        "recognized_entities": recognized_entities,
    }
