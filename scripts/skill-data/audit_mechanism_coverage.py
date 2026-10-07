"""Read-only mechanic completeness audit and repeatable offline timing baseline."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.data.combat_catalog import BASELINE, build_combat_catalog
from src.data.combat_simulation import CombatSearchLimit, plan_action_sequence, walk_combat_events
from src.data.skill_timing import SkillTimingStore

BENCHMARK_TEAM = ("弭弗", "骏卫", "余烬", "卡缪")


def reason_type(reason):
    return re.sub(r"(?:data_|buff_|chr_)\w+", "<native_id>", reason)


def benchmark(store, samples):
    catalog = build_combat_catalog(BENCHMARK_TEAM, store)
    forks = []
    for _ in range(samples):
        before = time.perf_counter()
        catalog.world.fork()
        forks.append((time.perf_counter() - before) * 1000)
    timings = []
    candidates = catalog.candidates(kind="battle")
    for program in candidates:
        elapsed = []
        result = None
        for _ in range(samples):
            before = time.perf_counter()
            result = catalog.world.simulate(program, strict=False)
            elapsed.append((time.perf_counter() - before) * 1000)
        timings.append({"key": program.key, "actor": program.actor, "mean_ms": mean(elapsed),
                        "legal": result is not None})
    before = time.perf_counter()
    try:
        plan = plan_action_sequence(catalog.world, candidates, depth=2, horizon=6, beam_width=8, max_expansions=144)
        search = {"status": "plan" if plan is not None else "no_complete_plan"}
    except CombatSearchLimit as error:
        search = {"status": "budget", "reason": str(error)}
    search["elapsed_ms"] = (time.perf_counter() - before) * 1000
    return {"team": list(BENCHMARK_TEAM), "samples": samples, "fork_mean_ms": mean(forks),
            "simulate": timings, "search": search,
            "note": "Unresolved programs are measured with strict=False; speed does not imply priceability."}


def audit(characters=None, *, samples=5, with_benchmark=True):
    if samples < 1:
        raise ValueError("samples must be positive")
    store = SkillTimingStore()
    names = characters or [row["character"] for row in json.loads(BASELINE.read_text(encoding="utf-8"))]
    rows, blocking = [], Counter()
    total = complete = 0
    for name in names:
        catalog = build_combat_catalog((name,), store)
        programs = []
        for program in catalog.candidates():
            unknown = sorted({reason for event in walk_combat_events(program.events) for reason in event.unresolved})
            programs.append({"key": program.key, "kind": program.kind, "unresolved": unknown})
            total += 1
            complete += not unknown
            blocking.update({reason_type(reason) for reason in unknown})
        rows.append({"character": name, "programs": programs, "catalog_diagnostics": list(catalog.diagnostics),
                     "world_unresolved": sorted(catalog.world.unresolved)})
    return {"schema_version": 1, "total_programs": total, "complete_programs": complete,
            "completeness_scope": "program event tree only; startup/observations/legality are reported separately",
            "characters": rows,
            "blockers": [{"type": reason, "affected_programs": count}
                         for reason, count in sorted(blocking.items(), key=lambda row: (-row[1], row[0]))],
            "benchmark": benchmark(store, samples) if with_benchmark else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "tmp/mechanism_coverage.json")
    parser.add_argument("--characters", nargs="+")
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--skip-benchmark", action="store_true")
    args = parser.parse_args()
    report = audit(args.characters, samples=args.samples, with_benchmark=not args.skip_benchmark)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Complete program trees: {report['complete_programs']}/{report['total_programs']}; report: {args.out}")


if __name__ == "__main__":
    main()
