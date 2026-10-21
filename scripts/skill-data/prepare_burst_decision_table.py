"""Prepare source-pinned local opening policies without screenshots or input devices."""

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.data.burst_decision_table import DEFAULT_DIRECTORY, BurstDecisionTable, team_path
from src.data.release_burst_planner import read_snapshot
from src.data.skill_timing import load_skill_timings
from src.tasks.onetime.TimedCombatLogic import TimedCombatLogic


class PlanningInputs:
    """Only the read-side task methods used by candidate construction exist."""
    def __init__(self, ready, rings):
        self.ready, self.rings = set(ready), rings

    def log_info(self, message):
        pass

    def _find_battle_ult(self, name):
        return name.removeprefix('ult_') in self.ready

    def is_battle_skill_pulsing(self, slot):
        return self.rings.get(slot)


def observe_declared_conditions(logic):
    """Offline scenarios declare calibrated states, not one captured frame."""
    logic._observe_battle_highlights()
    logic.highlight_stability.clear()
    for sampled_at in (-.16, -.1, -.05, 0.):
        logic.highlight_stability.observe(logic.battle_highlights.observations, sampled_at)
    logic.release_burst.observe_conditions({slot: logic._battle_condition(slot)
                                           for slot in logic.highlight_stability.observations})


def prepare(scenarios, destination):
    """Opening-only inputs. Runtime scene changes always build different keys."""
    if not isinstance(scenarios, list) or len(scenarios) > 256:
        raise ValueError('Provide at most 256 explicit opening scenarios')
    table, store, names = BurstDecisionTable(), load_skill_timings(), read_snapshot()
    for scenario in scenarios:
        if not isinstance(scenario, dict) or set(scenario) != {'team', 'sp', 'ultimate_slots', 'rings', 'main_slot'}:
            raise ValueError('An opening scenario needs team, sp, ultimate_slots, rings and main_slot')
        team, sp, ready, rings, main = (scenario[k] for k in ('team', 'sp', 'ultimate_slots', 'rings', 'main_slot'))
        if (not isinstance(team, list) or not 1 <= len(team) <= 4 or len(set(team)) != len(team)
                or any(name not in names for name in team) or type(sp) not in (int, float)
                or not math.isfinite(sp) or not 0 <= sp <= 300):
            raise ValueError('Unknown team, duplicate member or invalid SP')
        slots = {str(i+1) for i in range(len(team))}
        if (not isinstance(ready, list) or any(slot not in slots for slot in ready)
                or not isinstance(rings, dict) or any(slot not in slots or value is not None and type(value) is not bool
                                                     for slot, value in rings.items())
                or main is not None and main not in slots):
            raise ValueError('Invalid ready slots, ring states or main control')
        logic = TimedCombatLogic(PlanningInputs(ready, rings), store, clock=lambda: 0.0)
        logic._configure_team(team, reset_runtime=True)
        logic.release_burst.decision_table = table
        observe_declared_conditions(logic)
        logic._burst_main_control = main
        logic._release_burst_decision(logic._ready_ultimate_actions(), sp, 0.0)
    table.save(destination)
    return {'scenarios': len(scenarios), 'stored_results': len(table.entries), 'output': str(destination)}


def prepare_teams(scenarios, directory=DEFAULT_DIRECTORY):
    """Partition explicit scenarios by actual ordered roster; never pad slots."""
    if not isinstance(scenarios, list) or len(scenarios) > 4096:
        raise ValueError('Provide at most 4096 explicit scenarios in one offline batch')
    grouped = {}
    for scenario in scenarios:
        if not isinstance(scenario, dict) or not isinstance(scenario.get('team'), list):
            raise ValueError('Each scenario needs a team list')
        team = tuple(scenario['team'])
        team_path(team, directory)  # Validate the roster before using it as a shard key.
        grouped.setdefault(team, []).append(scenario)
    if any(len(group) > 256 for group in grouped.values()):
        raise ValueError('Each team shard accepts at most 256 explicit scenarios per batch')
    outputs = [prepare(group, team_path(team, directory)) for team, group in grouped.items()]
    return {'scenarios': len(scenarios), 'team_shards': len(outputs),
            'team_sizes': sorted({len(team) for team in grouped}), 'outputs': outputs}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenarios', required=True, type=Path)
    output = parser.add_mutually_exclusive_group()
    output.add_argument('--out', type=Path, help='Write all supplied scenarios into one bounded table')
    output.add_argument('--out-dir', type=Path, default=DEFAULT_DIRECTORY, help='Write one shard per actual team')
    args = parser.parse_args()
    scenarios = json.loads(args.scenarios.read_bytes())
    result = prepare(scenarios, args.out) if args.out is not None else prepare_teams(scenarios, args.out_dir)
    print(json.dumps(result, ensure_ascii=False))
