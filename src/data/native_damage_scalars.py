"""Level-specific reaction attributes with their original CharacterTable bytes."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import struct
from functools import lru_cache
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parents[2] / "assets/data/reaction_attributes/20261004"
ATTRIBUTES = {25: "physical_infliction_damage_scalar", 49: "ignite_damage_scalar"}


def read_scalar_snapshot(source: Path):
    manifest = json.loads((source / "index.json").read_text(encoding="utf-8"))
    if (manifest["schema_version"] != 1
            or manifest["attribute_types"] != {"PhysicalInflictionDamageScalar": 25, "IgniteDamageScalar": 49}):
        raise ValueError("Native reaction attribute manifest mismatch")
    raw = gzip.decompress((source / "CharacterTable.bytes.gz").read_bytes())
    payload = gzip.decompress((source / "scalars.json.gz").read_bytes())
    if (len(raw) != manifest["source_bytes"] or hashlib.sha256(raw).hexdigest() != manifest["source_sha256"]
            or hashlib.sha256(payload).hexdigest() != manifest["data_sha256"]):
        raise ValueError("Native reaction attribute snapshot hash mismatch")
    data = json.loads(payload)

    def check(field, fmt):
        offset = field["offset"]
        width = struct.calcsize(fmt)
        if (field["format"] != fmt or type(offset) is not int or offset < 0 or offset + width > len(raw)
                or raw[offset:offset + width].hex() != field["raw_hex"]
                or struct.unpack_from(fmt, raw, offset)[0] != field["value"]
                or not math.isfinite(field["value"])):
            raise ValueError("Native reaction attribute byte proof mismatch")
        return field["value"]

    rows = 0
    for native_id, character in data.items():
        offset = character["identity_offset"]
        identity = native_id.encode("utf-8") + b"\0"
        if type(offset) is not int or offset < 0 or raw[offset:offset + len(identity)] != identity:
            raise ValueError("Native reaction attribute character identity mismatch")
        for row in character["rows"]:
            rows += 1
            level = check(row["level"], "<d")
            if level != int(level) or level < 1 or check(row["level_attribute_type"], "<i") != 0:
                raise ValueError("Native reaction attribute level mismatch")
            check(row["break_stage"], "<i")
            if set(row["attributes"]) != {str(key) for key in ATTRIBUTES}:
                raise ValueError("Native reaction attribute selection mismatch")
            for key, attribute in row["attributes"].items():
                if check(attribute["attribute_type"], "<i") != int(key) or check(attribute, "<d") <= 0:
                    raise ValueError("Native reaction attribute type/value mismatch")
    if rows != manifest["rows"] or len(data) != manifest["characters"]:
        raise ValueError("Native reaction attribute count mismatch")
    return data


@lru_cache(maxsize=1)
def _scalars():
    return read_scalar_snapshot(SNAPSHOT)


def reaction_scalars(native_id: str, level: int):
    """Read an exact level; differing break-stage values require explicit selection."""
    rows = _scalars().get(native_id, {}).get("rows", ())
    matches = [row for row in rows if row["level"]["value"] == level]
    if not matches:
        raise ValueError(f"Missing native reaction attributes: {native_id}/{level}")
    result = {}
    for key, name in ATTRIBUTES.items():
        values = {row["attributes"][str(key)]["value"] for row in matches}
        if len(values) != 1:
            raise ValueError(f"Ambiguous native reaction attributes: {native_id}/{level}/{key}")
        result[name] = values.pop()
    return result
