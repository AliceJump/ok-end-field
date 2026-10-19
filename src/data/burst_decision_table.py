"""Bounded local policy results; file generation is explicit, never per-frame I/O."""

import hashlib
import json
import math
from collections import OrderedDict
from dataclasses import fields, is_dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = ROOT / 'data/release-burst-plans.json'
DEFAULT_DIRECTORY = ROOT / 'data/release-burst-plans'
CAPACITY = 256


@lru_cache(maxsize=1)
def algorithm_digest():
    paths = ('src/data/burst_decision_table.py', 'src/data/release_burst_planner.py',
             'src/data/mechanism_prediction.py', 'src/data/battle_conditions.py', 'src/data/highlight_damage.py',
             'src/data/skill_timing.py', 'src/data/combo_queue.py', 'src/tasks/onetime/TimedCombatLogic.py')
    try:
        return hashlib.sha256(b''.join(p.encode()+b'\0'+(ROOT / p).read_bytes().replace(b'\r\n', b'\n')
                                       for p in paths)).hexdigest()
    except OSError:
        return None  # A packaged build without sources cannot import a source-derived disk table.


def freeze(value):
    if is_dataclass(value):
        # Fresh observation timestamps do not change the meaning of the same
        # button evidence. Callers still sample/revalidate the current scene.
        return (type(value).__name__, tuple((f.name, freeze(getattr(value, f.name))) for f in fields(value)
                                           if f.name != 'observed_at'))
    if isinstance(value, dict):
        return tuple(sorted(((freeze(k), freeze(v)) for k, v in value.items()), key=repr))
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((freeze(v) for v in value), key=repr))
    return value


def budget_cell(actions, sp):
    """Exact feasibility intervals for a search with spending and no future SP gains."""
    if not math.isfinite(sp) or sp < 0:
        return sp
    costs = [a.sp_cost for a in actions if a.sp_cost > 0]
    if len(costs) > 4 or any(not math.isfinite(c) or c < 0 for c in costs):
        return sp  # No exponential table construction for unsupported candidate families.
    sums = {0.0}
    for cost in costs:
        sums |= {value+cost for value in sums}
    gates = {max(a.sp_gate, a.sp_cost) for a in actions}
    if any(not math.isfinite(v) or v < 0 or v != int(v) for v in (*costs, *gates)):
        return sp  # Fractional/dynamic costs keep exact SP, avoiding rounding across affordability edges.
    thresholds = {0.0, *(value+gate for value in sums for gate in gates)}
    return max(value for value in thresholds if value <= min(sp, 300))


def team_path(team, directory=DEFAULT_DIRECTORY):
    """Ordered rosters of every supported size have independent local shards."""
    roster = tuple(team)
    if not 1 <= len(roster) <= 4 or any(not isinstance(name, str) or not name for name in roster):
        raise ValueError('A team policy needs one to four named slots')
    key = hashlib.sha256(json.dumps(roster, ensure_ascii=False, separators=(',', ':')).encode('utf8')).hexdigest()
    return Path(directory) / (key + '.json')


class BurstDecisionTable:
    def __init__(self):
        self.entries = OrderedDict()
        self.hits = self.misses = 0

    @staticmethod
    def profile_key(rows):
        # Fixed snapshot rows are selected once when the team/build is rebuilt.
        # Dynamic effects live in the separately keyed runtime state below.
        return hashlib.sha256(json.dumps(freeze(rows), ensure_ascii=False, separators=(',', ':'),
                                         allow_nan=False).encode('utf8')).hexdigest()

    def key(self, planner, actions, sp, now, horizon, width, conditions, repeat_normals):
        model = planner.mechanism_state
        # Temporal state stays exact. No time buckets or guessed buff TTLs.
        # Empty opening states can translate in time; active windows cannot.
        state = (tuple((b.spec, b.source, b.recipient, b.starts_at-now, b.expires_at-now) for b in planner.bonuses),
                 tuple((k, s.starts_at-now, s.expires_at-now, s.free_battle) for k, s in planner.stances.items()),
                 tuple((r.owner, r.source, r.kind, r.nominal_count, r.launch_at-now, r.quote_until-now,
                        r.phase, r.forecast_only) for r in model.requests),
                 model.minimum_spent, tuple((k, end-now) for k, end in model.known_free_until), model.bone_stacks,
                 tuple((w.owner, w.amount, w.starts-now, w.expires-now) for w in model.combo_weapon),
                 tuple((w.owner, w.source, w.amount, w.starts-now, w.expires-now, w.elements, w.key, w.damage_tags)
                       for w in model.consumption_stacks),
                 tuple((w.owner, w.source, w.starts-now, w.expires-now, w.base, w.per_mark,
                        tuple(stamp-now for stamp in w.marks)) for w in model.skill_amplifications))
        data = (planner.team, planner.table_profile_key, planner.rules, planner.normal_durations, planner.condition_transitions,
                actions, budget_cell(actions, sp), horizon, width, conditions, repeat_normals,
                planner.mechanism_assessments, state)
        try:
            raw = json.dumps(freeze(data), ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf8')
        except (TypeError, ValueError):
            return None  # Invalid/unsupported inputs follow the existing validator, not a stale hit.
        return hashlib.sha256(raw).hexdigest()

    def get(self, key):
        value = self.entries.get(key)
        if value is None:
            self.misses += 1
            return None
        self.entries.move_to_end(key)
        return value

    def put(self, key, value):
        if key is None:
            return
        self.entries[key] = value
        self.entries.move_to_end(key)
        while len(self.entries) > CAPACITY:
            self.entries.popitem(last=False)

    @staticmethod
    def valid(value):
        if not isinstance(value, dict) or set(value) != {'sequence', 'damage', 'gain', 'sp_value'}:
            return False
        sequence, sp_value = value['sequence'], value['sp_value']
        return (isinstance(sequence, (list, tuple)) and 0 < len(sequence) <= 128
                and all(isinstance(pair, (list, tuple)) and len(pair) == 2
                        and pair[0] in {'1', '2', '3', '4'} and pair[1] in {'battle', 'ult', 'link', 'normal'}
                        for pair in sequence)
                and all(type(value[k]) in (int, float) and math.isfinite(value[k]) for k in ('damage', 'gain'))
                and isinstance(sp_value, dict) and set(sp_value) == {
                    'direct_damage', 'followup_contribution', 'sp_cost', 'damage_per_sp'}
                and all(type(sp_value[k]) in (int, float) and math.isfinite(sp_value[k])
                        for k in ('direct_damage', 'followup_contribution', 'sp_cost'))
                and (sp_value['damage_per_sp'] is None or type(sp_value['damage_per_sp']) in (int, float)
                     and math.isfinite(sp_value['damage_per_sp'])))

    @classmethod
    def load(cls, path=DEFAULT_PATH):
        table = cls()
        try:
            path = Path(path)
            if path.stat().st_size > 2*1024*1024 or algorithm_digest() is None:
                return table
            data = json.loads(path.read_bytes())
            if (data['schema_version'] != 1 or data['algorithm_sha256'] != algorithm_digest()
                    or not isinstance(data['entries'], dict) or len(data['entries']) > CAPACITY
                    or hashlib.sha256(json.dumps(data['entries'], sort_keys=True, ensure_ascii=False,
                                                 separators=(',', ':'), allow_nan=False).encode('utf8')).hexdigest()
                    != data['entries_sha256']):
                return table
            for key, value in data['entries'].items():
                if len(key) == 64 and all(c in '0123456789abcdef' for c in key) and cls.valid(value):
                    table.put(key, value)
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            return cls()
        return table

    @classmethod
    def for_team(cls, team, *, directory=DEFAULT_DIRECTORY, fallback=DEFAULT_PATH):
        try:
            path = team_path(team, directory)
        except (TypeError, ValueError):
            return cls()  # Optional disk policies must not break an incomplete runtime roster.
        table = cls.load(path)
        return table if table.entries else cls.load(fallback)

    def save(self, path):
        """Offline preparation only. Runtime lookups never write or reread files."""
        digest = algorithm_digest()
        if digest is None:
            raise ValueError('Source-derived table generation unavailable in this build')
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        entries = dict(self.entries)
        checksum = hashlib.sha256(json.dumps(entries, sort_keys=True, ensure_ascii=False,
                                             separators=(',', ':'), allow_nan=False).encode('utf8')).hexdigest()
        payload = {'schema_version': 1, 'algorithm_sha256': digest, 'entries_sha256': checksum, 'entries': entries}
        path.write_bytes((json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)+'\n').encode('utf8'))
