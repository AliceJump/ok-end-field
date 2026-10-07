"""Explicit per-skill fixed quotes, independent of scheduling and recognition.

These quotes replay the generator's selected rank-row sum. Replay verification
does not prove a full native skill timeline or its conditional attacks.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path

from src.data.damage_resolution import DamageHit, FixedDamagePanel, TimedDamageState

ROOT = Path(__file__).resolve().parents[2]
SKILL_TAGS = {"普通攻击": "normal", "战技": "skill", "连携技": "combo", "终结技": "ultimate"}
ELEMENTS = {"物理", "寒冷", "灼热", "电磁", "自然"}


def verify_sources(row, root=ROOT):
    """Reject stale/missing evidence, including changed calculation scripts."""
    flow = row["data_flow"]
    if (flow["schema_version"] != 1 or flow["runtime_modifiers"] != "not_applied"
            or flow["enemy_basis"] != "standard_dummy_def0_res0_no_stagger"
            or flow["fixed_passive_sources"] != row["profile"]["constant_passive_sources"]):
        raise ValueError("Unsupported fixed quote data flow")
    sources = flow["sources"]
    expected = {
        f"assets/data/character_skills/{row['key']}.json",
        f"assets/data/character_builds/{row['key']}.json",
        "assets/data/weapons.json", "assets/data/equipments.json",
        "assets/data/character_progression/20261003/characters.json",
        "assets/data/character_progression/20261003/tables.json.gz",
        "assets/data/character_progression/20261003/index.json",
        "assets/data/reaction_attributes/20261004/index.json",
        "assets/data/reaction_attributes/20261004/scalars.json.gz",
        "assets/data/reaction_attributes/20261004/CharacterTable.bytes.gz",
        "scripts/skill-data/compute_fixed_damage_baseline.py",
        "scripts/skill-data/compute_damage_baseline.py",
        "src/data/character_progression.py", "src/data/native_damage_scalars.py",
        "assets/data/skill_damage_row_semantics.json",
    }
    if set(sources) != expected:
        raise ValueError("Incomplete fixed quote source ledger")
    root = Path(root).resolve()
    for name, digest in sources.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"Missing fixed quote source: {name}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Stale fixed quote source: {name}")


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("Non-numeric fixed quote input")
    return float(value)


@dataclass(frozen=True)
class FixedSkillDamageQuote:
    skill_id: str
    skill_type: str
    panel: FixedDamagePanel
    element: str
    damage_tags: tuple[str, ...]
    multiplier: float
    non_crit: float
    expected: float
    aggregation_scope: str

    def resolve(self, state, *, actor, enemy, now, inputs=None):
        """Uniform-state quote only; never create hits or infer a sequence's timing."""
        hit = DamageHit(actor, enemy, self.element, self.multiplier,
                        damage_bonus=self.panel.bonus_for(self.element, self.damage_tags),
                        damage_tag=self.damage_tags[0], damage_tags=self.damage_tags)
        return state.resolve_hit(self.panel, hit, now=now, inputs=inputs)


def read_fixed_quote(row, skill):
    basis = skill["quote_basis"]
    if (basis["scope"] not in {"rank_row_sum_without_conditional_rows", "reviewed_base_rows"}
            or basis["skill_rank"] != row["profile"]["skill_rank"]
            or basis["crit_policy"] != "baseline_expectation"
            or basis["element"] not in ELEMENTS
            or basis["damage_tags"] != [SKILL_TAGS[skill["type"]]]):
        raise ValueError("Unsupported per-skill quote basis")
    values = {key: _number(value) for key, value in row["panel"]["damage_basis"].items()
              if key not in {"amplification", "damage_bonus"}}
    for key in ("amplification", "damage_bonus"):
        values[key] = {name: _number(value) for name, value in row["panel"]["damage_basis"][key].items()}
    panel = FixedDamagePanel(**values)
    multiplier = _number(basis["multiplier"])
    non_crit, expected = _number(skill["non_crit"]), _number(skill["crit_expect"])
    if multiplier < 0 or non_crit < 0 or expected < 0:
        raise ValueError("Negative fixed damage quote")
    quote = FixedSkillDamageQuote(skill["skill_id"], skill["type"], panel, basis["element"],
                                 tuple(basis["damage_tags"]), multiplier, non_crit, expected,
                                 (skill.get("row_semantics") or {}).get("base_scope", "unreviewed_row_sum"))
    replay = quote.resolve(TimedDamageState(("actor",)), actor="actor", enemy="target", now=0)
    if abs(replay.non_crit - non_crit) > .051 or abs(replay.expected - expected) > .051:
        raise ValueError(f"Fixed quote replay mismatch: {quote.skill_id}")
    return quote
