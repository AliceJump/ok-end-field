"""Hash-validated native gameplay records with explicit ranked/passive parameters."""

from __future__ import annotations

import gzip
import hashlib
import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "assets/data"


@lru_cache(maxsize=1)
def _supplements():
    result = {}
    for family, payload, hash_key in (
        ("character_progression/20261003", "supplement.json.gz", "supplement_sha256"),
        ("common_mechanics/20261003", "records.json.gz", "records_sha256"),
    ):
        source = ROOT / family
        manifest = json.loads((source / "index.json").read_text(encoding="utf-8"))
        raw = (source / payload).read_bytes()
        if hashlib.sha256(raw).hexdigest() != manifest[hash_key]:
            raise ValueError(f"Native gameplay snapshot hash mismatch: {family}")
        result.update(json.loads(gzip.decompress(raw)))
    return result


def native_record(store, name):
    try:
        return store.record(name)
    except KeyError:
        return deepcopy(_supplements()[name])


def native_number(value, blackboard):
    """A native reference's literal fallback is inactive when useBlackboardKey is true."""
    if value is None:
        return None
    amount = blackboard.get(value["blackboardKey"]) if value["useBlackboardKey"] else value["value"]
    return float(amount) if isinstance(amount, (int, float)) else None


@lru_cache(maxsize=1)
def native_enums():
    source = ROOT / "common_mechanics/20261003"
    manifest = json.loads((source / "index.json").read_text(encoding="utf-8"))
    raw = (source / "enums.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest["enums_sha256"]:
        raise ValueError("Native enum snapshot hash mismatch")
    return json.loads(raw)["enums"]


@lru_cache(maxsize=1)
def _assets():
    source = ROOT / "common_mechanics/20261003"
    manifest = json.loads((source / "index.json").read_text(encoding="utf-8"))
    raw = (source / "assets.json.gz").read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest["assets_sha256"]:
        raise ValueError("Native combat asset snapshot hash mismatch")
    records = json.loads(gzip.decompress(raw))
    if len(records) != manifest["assets_count"]:
        raise ValueError("Native combat asset snapshot count mismatch")
    return records


def native_asset(name):
    """Resolve Unity managed references without confusing an ID with its payload."""
    record = _assets()[name]
    if "references" not in record:
        return deepcopy(record)
    references = {r["rid"]: r for r in record["references"]["RefIds"]}

    def resolve(value, ancestors=()):
        if isinstance(value, list):
            return [resolve(v, ancestors) for v in value]
        if not isinstance(value, dict):
            return value
        if value.keys() == {"rid"}:
            rid = value["rid"]
            if rid in {-1, -2}:
                return None
            if rid in ancestors:
                raise ValueError(f"Cyclic combat asset reference: {name}/{rid}")
            reference = references[rid]
            if not reference["type"]["class"]:
                return None
            return {"$type": reference["type"]["ns"] + "." + reference["type"]["class"].replace("/", "+"),
                    "$value": resolve(reference["data"], (*ancestors, rid))}
        return {k: resolve(v, ancestors) for k, v in value.items()}

    return resolve(record["data"])
