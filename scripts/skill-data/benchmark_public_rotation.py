"""Reproduce a public-rotation comparison; all execution here is an offline HUD replay."""

import argparse
import json
import math
import os
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.data.skill_rotation import generate_damage_rotation
from src.data.skill_timing import load_skill_timings
from src.data.timing_dps import build_options, evaluate_cycle, load_damage_quotes, optimize_cycle
from src.tasks.onetime.TimedCombatLogic import TimedCombatLogic

TEAM = ["莱万汀", "狼卫", "安塔尔", "艾尔黛拉"]
SOURCE = "https://www.prydwen.gg/arknights-endfield/characters/laevatain/"


class ReplayHud:
    """Accepted casts, nominal SP recovery, no enemies, links or Ultimate energy."""

    def __init__(self, options, regen):
        self.now = 0.0
        self.sp = 300.0
        self.regen = regen
        self.options = {cast.slot: cast for cast in options}
        self.casts = []

    def active_time(self):
        return self.now

    def log_info(self, message):
        pass

    def is_link_skill_ready(self):
        return False

    def _find_battle_ult(self, name):
        return False

    def mouse_up(self, **kwargs):
        pass

    def mouse_down(self, **kwargs):
        pass

    def active_and_send_mouse_delta(self, **kwargs):
        pass

    def get_skill_bar_count(self):
        return int((self.sp + 1e-8) // 100)

    def get_skill_bar_sp(self):
        return self.sp

    def sleep(self, seconds):
        self.advance(seconds)

    def send_key(self, token):
        self.sp -= self.options[token].sp_cost
        self.casts.append({"seconds": round(self.now, 3), "slot": token})

    def advance(self, seconds):
        self.now += seconds
        self.sp = min(300, self.sp + seconds * self.regen)


def serialize_plan(plan):
    return {**asdict(plan), "dps": plan.dps} if plan else None


def benchmark(regen=8.0, seconds=180.0):
    store = load_skill_timings()
    quotes = load_damage_quotes(TEAM)
    options = build_options(TEAM, store, quotes)
    plan = optimize_cycle(options, regen)
    by_slot = {cast.slot: cast for cast in options}
    baseline = evaluate_cycle(tuple(by_slot[slot] for slot in generate_damage_rotation(TEAM) if slot in by_slot), regen)
    if plan is None:
        raise ValueError("No periodic plan")
    if baseline is None or baseline.dps <= 0:
        raise ValueError("No valid baseline periodic plan")
    hud = ReplayHud(options, regen)
    logic = TimedCombatLogic(hud, store, clock=lambda: hud.now)
    logic.team = TEAM
    logic.order = list(plan.slots)
    logic.plan = plan
    logic.damage_quotes = quotes
    holding_seconds = 0.0
    frame = 1 / 30
    for _ in range(round(seconds / frame)):
        logic.step()
        if logic._holding:
            holding_seconds += frame
        hud.advance(frame)

    # Isolate the normal-attack lock after Laevatain's Ultimate. This tests
    # scheduler behavior, not the game's animation length or damage output.
    ult_hud = ReplayHud(options, regen)
    ult_hud.sp = 0
    ult_logic = TimedCombatLogic(ult_hud, store, clock=lambda: ult_hud.now)
    ult_logic.team, ult_logic.order = TEAM, list(plan.slots)
    profiles = store.profiles(TEAM[0], "ult")
    ult_logic._begin(profiles, 0)
    first_attack = None
    for _ in range(600):
        ult_logic.step()
        if ult_logic._holding:
            first_attack = round(ult_hud.now, 3)
            break
        ult_hud.advance(frame)

    battle = next(
        row
        for row in json.loads((ROOT / "assets/data/damage_baseline.json").read_text(encoding="utf-8"))
        if row["character"] == TEAM[0]
    )
    battle = next(skill for skill in battle["skills"] if skill["type"] == "战技")
    return {
        "scope": "Offline model and actual scheduler replay. No in-game damage measurement.",
        "source": SOURCE,
        "team": TEAM,
        "nominal_sp_regen": regen,
        "public_opener_action_counts": {"battle": 3, "final_strike": 2, "combo": 4},
        "public_requirements": [
            "Accumulate 4 Melting Flame before enhanced battle skill",
            "Normal attack strings enable absorption and combos",
            "Align support buffs with Laevatain Ultimate",
        ],
        "baseline_all_slots": serialize_plan(baseline),
        "optimized": serialize_plan(plan),
        "modeled_gain_percent": (plan.dps / baseline.dps - 1) * 100,
        "laevatain_battle_quote": battle,
        "laevatain_quote_checks_melting_flame": bool(quotes[TEAM[0]].requires),
        "runtime_replay": {
            "seconds": seconds,
            "assumptions": "300 initial SP; all casts accepted; 30 Hz HUD; no links, ults, enemies or extra SP",
            "first_12_casts": hud.casts[:12],
            "attack_button_held_seconds": round(holding_seconds, 3),
            "last_5_cycle_windows": [round(sample[0], 3) for sample in logic.cycle_samples],
            "observed_damage": None,
        },
        "ultimate_guard_replay": {
            "native_duration_seconds": max(p.duration for p in profiles),
            "native_exclusive_seconds": max(p.exclusive for p in profiles),
            "scheduler_actionable_seconds": max(p.actionable for p in profiles),
            "first_normal_attack_input_seconds": first_attack,
            "observed_damage": None,
        },
    }


def main(argv: list[str] | None = None) -> int:
    """Run the offline benchmark and confine optional reports to repository tmp."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regen", type=float, default=8.0)
    parser.add_argument("--seconds", type=float, default=180.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    output_path = None
    if args.output:
        # Canonicalize symlinks and require the directory separator after tmp.
        # Keep the checked canonical value as the one used by the write below.
        output_path = os.path.realpath(args.output)
        allowed_root = os.path.realpath(ROOT / "tmp")
        if not output_path.startswith(allowed_root + os.sep) or Path(output_path).suffix.lower() != ".json":
            parser.error("output must be a JSON file under the repository tmp directory")
    if not all(math.isfinite(value) and value > 0 for value in (args.seconds, args.regen)):
        parser.error("seconds and regen must be finite and positive")
    result = json.dumps(benchmark(args.regen, args.seconds), ensure_ascii=False, indent=2)
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(result + "\n", encoding="utf-8")
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
