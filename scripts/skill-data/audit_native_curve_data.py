"""Audit structural decoding of captured Unity curves; this does not price skills."""

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.data.native_action_program import _nodes
from src.data.native_curve_data import decode_curve_profile
from src.data.native_gameplay import _supplements
from src.data.skill_timing import SkillTimingStore


def audit(records=None):
    if records is None:
        records = {**SkillTimingStore()._all_records(), **_supplements()}
    rows, errors, digests = [], [], set()
    weighted_modes, wrap_modes = Counter(), Counter()
    for source, record in records.items():
        for node in _nodes(record["data"]):
            if not node["$type"].endswith("CurveEvaluateFloat+Data"):
                continue
            body = node["$value"]
            try:
                profile = body["customCurve"]
                curve = decode_curve_profile(profile)
                digest = hashlib.sha256(bytes.fromhex(profile["rawHex"])).hexdigest()
                digests.add(digest)
                keys = () if curve is None or curve.keys is None else curve.keys
                weighted_modes.update(key.weighted_mode for key in keys)
                if curve is not None:
                    wrap_modes.update((curve.pre_wrap_mode, curve.post_wrap_mode))
                rows.append({"source": source, "key": body["key"], "sha256": digest,
                             "selected_custom": body["useCustomCurve"], "template": body["curveTemplate"],
                             "null_curve": curve is None, "null_keys": curve is not None and curve.keys is None,
                             "key_count": len(keys),
                             "infinity_tangents": sum(math.isinf(key.in_tangent) + math.isinf(key.out_tangent)
                                                       for key in keys)})
            except (KeyError, TypeError, ValueError) as error:
                errors.append({"source": source, "key": body.get("key"), "error": str(error)})
    return {"schema_version": 1, "scope": "stored Unity curve fields and byte-exact round-trip only",
            "curve_nodes": len(rows) + len(errors), "decoded_nodes": len(rows), "unique_payloads": len(digests),
            "weighted_modes": dict(sorted(weighted_modes.items())), "wrap_modes": dict(sorted(wrap_modes.items())),
            "curves": rows, "errors": errors,
            "execution_unresolved": "Unity Evaluate, float output update threshold and BB consumers remain unbound"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "tmp/native_curve_data_audit.json")
    args = parser.parse_args()
    report = audit()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"Decoded curve data: {report['decoded_nodes']}/{report['curve_nodes']}; "
          f"unique payloads: {report['unique_payloads']}; runtime remains unresolved")
    if report["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
