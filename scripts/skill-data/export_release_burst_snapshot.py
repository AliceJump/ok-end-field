"""Export the reviewed release-only layer from a clean, pinned research checkout.

Research/native compilers run only here, never in the master scheduler. The
snapshot contains resolved rules and fixed panels, with their source revision.
"""

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from reviewed_support_release import support_release

ROOT = Path(__file__).resolve().parents[2]


def export(source, revision, destination):
    source = Path(source).resolve()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    wanted = subprocess.check_output(["git", "rev-parse", revision], cwd=source, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=source, text=True)
    if head != wanted or dirty:
        raise ValueError("Export requires the requested clean research revision")
    sys.path.insert(0, str(source))
    from src.data.release_burst_planner import ReleaseBurstPlanner
    from src.data.skill_timing import load_skill_timings

    baseline_path = source / "assets/data/fixed_damage_baseline.json"
    rows = json.loads(baseline_path.read_text(encoding="utf8"))
    names = [row["character"] for row in rows]
    planner = ReleaseBurstPlanner(names, store=load_skill_timings())
    actors, supports = [], []
    store = load_skill_timings()
    for index, row in enumerate(rows, 1):
        rules = []
        for kind in ("battle", "ult"):
            for spec in planner.rules.get((str(index), kind), ()):
                data = asdict(spec)
                data["kind"] = kind
                data["ends_on_source_action"] = spec.key == "buff_chr_0019_karin_potential_3"
                data["starts_after"] = 0.0
                data["blocks_source_actions"] = []
                rules.append(data)
        additions, evidence = support_release(row, store)
        rules.extend(additions)
        if evidence:
            supports.append(evidence)
        quotes = {}
        for kind in ("normal", "battle", "ult"):
            quote = planner.quotes.get((str(index), kind))
            if quote is not None:
                quotes[kind] = {key: quote[key] for key in ("non_crit", "crit_expect", "bonus_pct", "quote_basis")}
                if row["key"] == "liino" and kind == "ult":
                    # Ongoing pulses and the non-interrupted closing explosion
                    # are not an immediate release quote. Do not invent hits.
                    quotes[kind].update(non_crit=0.0, crit_expect=0.0,
                                        pricing_scope="support_opener_only; sustained_and_closing_damage_not_forecast")
        actors.append({"character": row["character"], "key": row["key"], "profile": row["profile"],
                       "build": row["build"], "panel": row["panel"]["damage_basis"],
                       "attribute_basis": row["attribute_basis"], "quotes": quotes, "rules": rules})
    # This export is a reviewed boundary, not automatic admission of new rules.
    expected = {"安塔尔": 2, "秋栗": 1, "佩丽卡": 1, "庄方宜": 1,
                "莱万汀": 1, "艾维文娜": 1, "埃特拉": 1, "赛希": 2, "梨诺": 3}
    actual = {row["character"]: len(row["rules"]) for row in actors if row["rules"]}
    if len(actors) != 32 or actual != expected:
        raise ValueError("Release-only export scope changed; review before widening it")
    paths = {"assets/data/fixed_damage_baseline.json", "src/data/release_burst_planner.py",
             "src/data/damage_release_rules.py", "src/data/native_attribute_modifiers.py",
             "src/data/reviewed_weapon_release.py", "assets/data/skill_timings/20261002/index.json",
             "assets/data/skill_timings/20261002/records.json.gz",
             "assets/data/skill_timings/20261002/ranked_blackboards.json.gz"}
    for row in rows:
        paths.update(row["data_flow"]["sources"])
    hashes = {path: hashlib.sha256((source / path).read_bytes()).hexdigest() for path in sorted(paths)}
    data = {"schema_version": 1, "scope": "confirmed_release_bonuses", "actors": actors,
            "excluded": ["hit_or_target_outcomes", "field_occupancy", "combo_release",
                         "native_four_stat_producers", "unconfirmed_nonconverted_attributes"]}
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf8")
    (destination / "snapshot.json").write_bytes(payload)
    support_payload = (json.dumps(supports, ensure_ascii=False, indent=2) + "\n").encode("utf8")
    (destination / "support-evidence.json").write_bytes(support_payload)
    manifest = {"schema_version": 1, "source_revision": head,
                "source_repository": "https://github.com/AliceJump/ok-end-field",
                "source_branch": "codex/effect-semantics-normalization", "source_hashes": hashes,
                "snapshot_sha256": hashlib.sha256(payload).hexdigest(), "actors": 32, "rules": 13,
                "support_evidence_sha256": hashlib.sha256(support_payload).hexdigest(),
                "exporter_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                                    (Path(__file__), ROOT / "scripts/skill-data/reviewed_support_release.py")}}
    (destination / "index.json").write_bytes((json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf8"))
    print(f"Exported {len(actors)} fixed panels and {sum(actual.values())} release rules from {head}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-worktree", required=True)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "assets/data/release_burst")
    args = parser.parse_args()
    export(args.source_worktree, args.source_ref, args.out)
