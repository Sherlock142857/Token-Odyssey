"""Safe YAML loading that rejects silent duplicate-key overwrites."""

from pathlib import Path

import yaml


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node):
    result = {}
    loader.flatten_mapping(node)
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in result:
            raise ValueError(f"duplicate YAML key {key!r} at line {key_node.start_mark.line + 1}")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_mapping(path: str | Path) -> dict:
    raw = yaml.load(Path(path).read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    if not isinstance(raw, dict):
        raise ValueError("YAML root must be a mapping")
    return raw
