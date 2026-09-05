from copy import deepcopy
from pathlib import Path

import pytest

from token_odyssey.kernel.actions.registry import builtin_registry
from token_odyssey.scenario import compile_scenario

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def registry():
    return builtin_registry()


@pytest.fixture
def scenario_data():
    return {
        "schema_version": 3, "id": "laboratory", "title": "规则实验室", "max_rounds": 3,
        "world": {
            "entities": {
                "a": {"kind": "room", "name": "甲室"},
                "b": {"kind": "room", "name": "乙室"},
                "c": {"kind": "room", "name": "丙室"},
                "alice": {"kind": "character", "name": "甲角色"},
                "bob": {"kind": "character", "name": "乙角色"},
                "eve": {"kind": "character", "name": "丙角色"},
                "key": {"kind": "item", "name": "钥匙", "size": 1},
                "wrong_key": {"kind": "item", "name": "另一把钥匙", "size": 1},
                "box": {"kind": "item", "name": "盒子", "size": 4,
                        "container": {"capacity_size": 10}, "openable": {}, "lockable": {"key_item_ids": ["key"]}},
                "gem": {"kind": "item", "name": "宝石", "size": 1, "description": "带有一道白色纹路。"},
                "glass": {"kind": "item", "name": "透明柜", "container": {},
                          "openable": {"closed_visibility": 1}},
                "bead": {"kind": "item", "name": "珠子", "size": 1},
                "socket": {"kind": "item", "name": "插槽", "portable": False,
                           "slot": {"compatible_item_ids": ["gem"]}, "operable": True},
                "table": {"kind": "item", "name": "桌子", "portable": False},
                "secret": {"kind": "item", "name": "远处物品"},
            },
            "passages": {
                "gate": {"name": "门", "rooms": ["a", "b"],
                         "openable": {"open_visibility": 0, "closed_visibility": 0, "closed_sound": 1},
                         "lockable": {"key_item_ids": ["key"]}},
                "arch": {"name": "拱门", "rooms": ["b", "c"], "forward_visibility": 0, "reverse_visibility": 0},
            },
            "flag_names": ["powered", "released"],
        },
        "initial_state": {
            "placements": {
                "alice": {"parent_id": "a"}, "bob": {"parent_id": "a"}, "eve": {"parent_id": "b"},
                "key": {"parent_id": "alice", "relation": "attached"},
                "wrong_key": {"parent_id": "alice", "relation": "attached"},
                "box": {"parent_id": "a"}, "gem": {"parent_id": "box"},
                "glass": {"parent_id": "a"}, "bead": {"parent_id": "glass"},
                "socket": {"parent_id": "a"}, "table": {"parent_id": "a"}, "secret": {"parent_id": "b"},
            },
            "locks": {"box": True},
        },
    }


@pytest.fixture
def scenario(scenario_data):
    return compile_scenario(scenario_data)


class FixedRouter:
    def __init__(self, order):
        self.order = list(order)
        self.index = 0
        self.received_events = []

    def next_actor(self, actor_ids, recent_events):
        self.received_events.append(recent_events)
        selected = self.order[self.index % len(self.order)]
        self.index += 1
        return selected


class MemoryRecorder:
    def __init__(self):
        self.records = {}
        self.final = None

    def record(self, stream, value):
        self.records.setdefault(stream, []).append(deepcopy(value))

    def finalize(self, **kwargs):
        self.final = kwargs
