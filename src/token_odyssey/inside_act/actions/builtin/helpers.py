"""Small helpers shared by builtin actions; rendering remains action-local."""

from __future__ import annotations

from token_odyssey.inside_act.actions.contracts import ActionContext
from token_odyssey.inside_act.domain.entities import Item
from token_odyssey.inside_act.domain.events import AnchorSnapshot, VisibilityAnchor


def require_item(context: ActionContext, item_id: str) -> tuple[Item | None, list[str]]:
    entity = context.state.entities.get(item_id)
    if not isinstance(entity, Item):
        return None, [f"未知物品 {item_id!r}"]
    return entity, []


def actor_anchor(actor_id: str, *, after: bool = False) -> VisibilityAnchor:
    return VisibilityAnchor(
        entity_id=actor_id,
        snapshot=AnchorSnapshot.AFTER if after else AnchorSnapshot.BEFORE,
    )
