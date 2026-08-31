"""Explicit action registry; adding an action does not grow the facade."""

from airpg.harness.actions import (
    GiveHandler,
    HideHandler,
    MoveHandler,
    PlaceHandler,
    SearchHandler,
    ShowHandler,
    TakeHandler,
    WaitHandler,
)
from airpg.models import ActionKind


ACTION_HANDLERS = {
    ActionKind.MOVE: MoveHandler(),
    ActionKind.SEARCH: SearchHandler(),
    ActionKind.TAKE: TakeHandler(),
    ActionKind.GIVE: GiveHandler(),
    ActionKind.PLACE: PlaceHandler(),
    ActionKind.SHOW: ShowHandler(),
    ActionKind.HIDE: HideHandler(),
    ActionKind.WAIT: WaitHandler(),
}

