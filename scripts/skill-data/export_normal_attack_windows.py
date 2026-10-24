"""Export only reviewed normal-window contracts from a clean registered source."""

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path

from export_release_burst_snapshot import ROOT, _clean_revision, reviewed_locations


def export(source, revision, destination):
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("Source revision must be a complete lowercase SHA")
    # Reuse the registered-worktree boundary; output remains one named family.
    source, _ = reviewed_locations(source, ROOT / "assets/data/release_burst")
    destination = Path(destination).resolve()
    allowed = (ROOT / "assets/data/normal_attack_windows", ROOT / "tmp/normal-window-reexport")
    if destination not in tuple(p.resolve() for p in allowed):
        raise ValueError("Output must be the normal-window snapshot or its verification directory")
    _clean_revision(source, revision)
    sys.path.insert(0, str(source))
    from src.data.native_gameplay import native_record
    from src.data.yvonne_normal_window import PREFIX, PRIMARY, YvonneNormalWindow

    from src.data.skill_timing import load_skill_timings

    rows = json.loads((source / "assets/data/fixed_damage_baseline.json").read_text(encoding="utf8"))
    row = next(r for r in rows if r["key"] == "yvonne")
    profile = {key: row["profile"][key] for key in ("character_level", "skill_rank", "potential")}
    if profile != {"character_level": 90, "skill_rank": 12, "potential": 0}:
        raise ValueError("Review changed Yvonne fixed baseline before exporting")
    store = load_skill_timings()
    model = YvonneNormalWindow.from_store(store, profile["skill_rank"])
    ids = [PREFIX + "ultimate_skill", PREFIX + "ult_attack_end", *(PREFIX + "ult_attack" + s for s in PRIMARY)]
    panel_hash = hashlib.sha256(json.dumps(row["panel"]["damage_basis"], sort_keys=True,
                                separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    payload = {"schema_version": 1, "actors": {"yvonne": {"profile": profile, "panel_sha256": panel_hash,
               "model": asdict(model), "native_hashes": {key: native_record(store, key)["source"]["sha256"] for key in ids}}}}
    paths = ("src/data/yvonne_normal_window.py", "assets/data/fixed_damage_baseline.json",
             "assets/data/skill_timings/20261002/index.json", "assets/data/skill_timings/20261002/records.json.gz",
             "assets/data/skill_timings/20261002/ranked_blackboards.json.gz",
             "assets/data/character_progression/20261003/index.json")
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf8")
    manifest = {"schema_version": 1, "source_revision": revision, "payload_sha256": hashlib.sha256(raw).hexdigest(),
                "source_hashes": {p: hashlib.sha256((source / p).read_bytes()).hexdigest() for p in paths},
                "exporter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "scope": "nominal_primary_held_normals; not_native_execution_or_observed_hits"}
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "windows.json").write_bytes(raw)
    (destination / "index.json").write_bytes((json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf8"))
    print(f"Exported one P0 Yvonne window from {revision}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-worktree", required=True)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "assets/data/normal_attack_windows")
    args = parser.parse_args()
    export(args.source_worktree, args.source_ref, args.out)
