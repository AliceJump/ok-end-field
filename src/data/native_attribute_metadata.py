"""Verified raw defaults and bounds, distinct from armed/final combat values."""

import gzip
import hashlib
import json
import math
import struct
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "assets/data/attribute_metadata/20261008"
ATTRIBUTES = {39: "力量", 40: "敏捷", 41: "智识", 42: "意志",
              57: "PulseAbnormalDamageIncrease", 58: "CrystAbnormalDamageIncrease",
              76: "AtkIncreaseFactorFromStr", 77: "AtkIncreaseFactorFromAgi",
              78: "AtkIncreaseFactorFromWisd", 79: "AtkIncreaseFactorFromWill"}
DOMAIN = "raw_default_and_bounds; not_current_final_value"


def read_metadata_snapshot(source=SNAPSHOT, *, root=ROOT):
    manifest = json.loads((source / "index.json").read_text(encoding="utf-8"))
    payload = (source / "metadata.json").read_bytes()
    raw = gzip.decompress((source / "AttributeMetaTable.bytes.gz").read_bytes())
    inputs = json.loads((root / "assets/data/skill_timings/20261002/index.json").read_text(encoding="utf-8"))["native_inputs"]
    if (manifest["schema_version"] != 1 or manifest["native_inputs"] != inputs
            or manifest["attributes"] != list(ATTRIBUTES)):
        raise ValueError("Native attribute metadata domain/build mismatch")
    if (len(raw) != manifest["source_bytes"] or hashlib.sha256(raw).hexdigest() != manifest["source_sha256"]
            or hashlib.sha256(payload).hexdigest() != manifest["metadata_sha256"]):
        raise ValueError("Native attribute metadata hash mismatch")
    data = json.loads(payload)
    if (data["schema_version"] != 1 or data["domain"] != DOMAIN
            or set(data["attributes"]) != {str(key) for key in ATTRIBUTES}):
        raise ValueError("Native attribute metadata domain/selection mismatch")
    layouts = {"attribute_type": (0, "<i"), "defaultValue": (8, "<d"),
               "hasMaxValue": (16, "<?"), "hasMinValue": (17, "<?"),
               "maxValue": (24, "<d"), "minValue": (32, "<d")}
    for key, row in data["attributes"].items():
        value, proof = row["data"], row["byte_proof"]
        base = proof["attribute_type"]["offset"]
        if type(base) is not int or base % 8 or base < 0 or base + 40 > len(raw):
            raise ValueError("Native attribute metadata row offset mismatch")
        if set(proof) != set(layouts):
            raise ValueError("Native attribute metadata byte proof selection mismatch")
        for field, (relative, fmt) in layouts.items():
            item = proof[field]
            expected = int(key) if field == "attribute_type" else value[field]
            actual = struct.unpack_from(fmt, raw, base + relative)[0]
            if (item["offset"] != base + relative or item["format"] != fmt
                    or item["raw_hex"] != raw[base + relative:base + relative + struct.calcsize(fmt)].hex()
                    or type(item["value"]) is not type(actual) or type(expected) is not type(actual)
                    or item["value"] != actual or actual != expected or not math.isfinite(actual)):
                raise ValueError("Native attribute metadata byte proof mismatch")
        if value["attributeType"] != int(key):
            raise ValueError("Native attribute metadata identity mismatch")
        if value["hasMinValue"] and value["hasMaxValue"] and value["minValue"] > value["maxValue"]:
            raise ValueError("Native attribute metadata bounds mismatch")
    return data["attributes"]


@lru_cache(maxsize=1)
def attribute_metadata():
    return read_metadata_snapshot()


def metadata_audit(basis):
    """Validate fixed final totals against native bounds without seeding defaults."""
    rows = attribute_metadata()
    for key, name in ATTRIBUTES.items():
        if key not in (39, 40, 41, 42):
            continue
        value, total = rows[str(key)]["data"], basis["totals"][name]
        if (not math.isfinite(total) or value["hasMinValue"] and total < value["minValue"]
                or value["hasMaxValue"] and total > value["maxValue"]):
            raise ValueError(f"Fixed final attribute outside native bounds: {name}")
    return {"source": "assets/data/attribute_metadata/20261008/index.json", "domain": DOMAIN,
            "fixed_four_stat_bounds": "verified",
            "attributes": {key: {"name": ATTRIBUTES[int(key)], "raw_default": row["data"]["defaultValue"],
                                 "minimum": row["data"]["minValue"] if row["data"]["hasMinValue"] else None,
                                 "maximum": row["data"]["maxValue"] if row["data"]["hasMaxValue"] else None}
                           for key, row in rows.items()},
            "armed_final_producer_status": "not_bound; raw defaults cannot supply current final inputs"}
