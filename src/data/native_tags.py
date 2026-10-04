"""Client CRC32 tag identities and ancestor membership from verified assets."""

import zlib
from functools import lru_cache

from src.data.combat_simulation import UnresolvedMechanic
from src.data.native_gameplay import _assets, native_asset, native_enums


def native_tag_id(value):
    if "tagId" in value:
        result = value["tagId"]
    else:
        raw = bytes.fromhex(value["raw"])
        if len(raw) != 4:
            raise UnresolvedMechanic("Native tag identity must be four bytes")
        result = int.from_bytes(raw, "little", signed=True)
    if type(result) is not int or not -(2 ** 31) <= result < 2 ** 31:
        raise UnresolvedMechanic("Invalid native tag identity")
    return result


def tag_hash(name):
    # Native string CRC uses UTF-8, initial 0xffffffff and final complement.
    result = zlib.crc32(name.encode("utf-8"))
    return result if result < 2 ** 31 else result - 2 ** 32


@lru_cache(maxsize=1)
def tag_names():
    names = {name for key in _assets() if key == "GameplayTagConfig" or key.startswith("data_tag_")
             for name in native_asset(key)["allTags"]["_keyData"]}
    # GameplayTagConfigSet._Build constructs the shared tree, including parents.
    names |= {"/".join(name.split("/")[:i]) for name in names for i in range(1, len(name.split("/")))}
    result = {tag_hash(name): name for name in names}
    if len(result) != len(names):
        raise UnresolvedMechanic("Native gameplay tag hash collision")
    return result


def expand_tags(values):
    result = set()
    for value in values:
        identity = native_tag_id(value)
        if identity == 0:
            continue  # INVALID_TAG_ID is not a tag node.
        name = tag_names().get(identity)
        if name is None:
            raise UnresolvedMechanic(f"Unbound native tag identity: {identity}")
        result.update(tag_hash("/".join(name.split("/")[:i])) for i in range(1, len(name.split("/")) + 1))
    return tuple(sorted(result))


def tag_query(query):
    modes = native_enums()["Beyond.Gameplay.Core.GameplayTagQuery+QueryType"]
    mode = next((k for k, v in modes.items() if v["value"] == query["queryType"]), None)
    if mode not in {"HasAny", "HasAll", "ExceptAny", "ExceptAll"}:
        raise UnresolvedMechanic(f"Unknown native tag query: {query['queryType']}")
    tags = tuple(native_tag_id(tag) for tag in query["tags"])
    if any(tag != 0 and tag not in tag_names() for tag in tags):
        raise UnresolvedMechanic(f"Unbound native query tag identity: {tags}")
    return mode, tags


def matches_tags(expanded, mode, tags):
    matches = (tag in expanded for tag in tags)
    result = any(matches) if mode in {"HasAny", "ExceptAny"} else all(matches)
    return not result if mode.startswith("Except") else result
