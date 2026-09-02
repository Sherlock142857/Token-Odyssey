"""The only explicit registration site for builtin actions."""

from token_odyssey.inside_act.actions.builtin.give import ACTION as GIVE
from token_odyssey.inside_act.actions.builtin.hide import ACTION as HIDE
from token_odyssey.inside_act.actions.builtin.install import ACTION as INSTALL
from token_odyssey.inside_act.actions.builtin.move import ACTION as MOVE
from token_odyssey.inside_act.actions.builtin.operate import ACTION as OPERATE
from token_odyssey.inside_act.actions.builtin.place import ACTION as PLACE
from token_odyssey.inside_act.actions.builtin.say import ACTION as SAY
from token_odyssey.inside_act.actions.builtin.search import ACTION as SEARCH
from token_odyssey.inside_act.actions.builtin.show import ACTION as SHOW
from token_odyssey.inside_act.actions.builtin.take import ACTION as TAKE
from token_odyssey.inside_act.actions.builtin.wait import ACTION as WAIT
from token_odyssey.inside_act.actions.registry import ActionRegistry


BUILTIN_ACTIONS = (
    SAY,
    MOVE,
    SEARCH,
    TAKE,
    GIVE,
    PLACE,
    SHOW,
    HIDE,
    INSTALL,
    OPERATE,
    WAIT,
)


def build_builtin_registry() -> ActionRegistry:
    return ActionRegistry(BUILTIN_ACTIONS)
