"""Compare the existing all-slot rotation with the periodic timing DPS model."""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.data.skill_rotation import generate_damage_rotation
from src.data.skill_timing import load_skill_timings
from src.data.timing_dps import build_options, evaluate_cycle, load_damage_quotes, optimize_cycle


def compare(team, regen=8.0):
    options = build_options(team, load_skill_timings(), load_damage_quotes(team))
    by_slot = {option.slot: option for option in options}
    baseline = evaluate_cycle(tuple(by_slot[slot] for slot in generate_damage_rotation(team) if slot in by_slot), regen)
    plan = optimize_cycle(options, regen)
    return {
        "team": team,
        "nominal_sp_regen": regen,
        "baseline": {**asdict(baseline), "dps": baseline.dps} if baseline else None,
        "optimized": {**asdict(plan), "dps": plan.dps} if plan else None,
        "scope": "Expected battle-skill damage only; nominal timings; live monitoring overrides planning.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("team", nargs=4)
    parser.add_argument("--regen", type=float, default=8.0)
    args = parser.parse_args()
    print(json.dumps(compare(args.team, args.regen), ensure_ascii=False, indent=2))
