"""Fixed weapon tag/element filters and live enemy predicates stay separate."""

import json
import unittest
from pathlib import Path

from src.data.character_skills import get_character
from src.data.combat_catalog import build_combat_catalog
from src.data.combat_model import EnemyCombatState
from src.data.combat_simulation import ActionProgram, CombatEvent, CombatWorldState
from src.data.damage_quote_data import read_fixed_quote
from src.data.damage_resolution import DamageHit, FixedDamagePanel
from src.data.damage_state_rules import fixed_weapon_bonuses, state_bonus_rules
from src.data.effects import EffectType
from src.data.skill_timing import SkillTimingStore
from src.data.skill_types import SkillEffect

ROOT = Path(__file__).resolve().parents[1]


class TestDamageStateRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = {row["key"]: row for row in json.loads(
            (ROOT / "assets/data/fixed_damage_baseline.json").read_text(encoding="utf-8"))}

    def world(self, key):
        row = self.rows[key]
        character = get_character(key, skill_rank=row["profile"]["skill_rank"], potential=row["profile"]["potential"])
        world = CombatWorldState(("actor", "ally"), regen=0)
        for state in world.characters.values():
            state.panel = FixedDamagePanel(100, 0, 0, 1, 0, .5)
        world.register_damage_passives("actor", state_bonus_rules(character, row))
        return world

    def hit(self, world, actor="actor", enemy="target", *, effects=()):
        event = CombatEvent(0, "hit", hit=DamageHit(actor, enemy, "物理", 1), effects=effects)
        program = ActionProgram("hit", actor, "normal", 0, 0, 0, (event,), enemy=enemy)
        before = world.damage
        self.assertTrue(world.start(program, action_id=f"hit:{world._actions_started}"))
        return world.damage - before

    def test_fuyao_fixed_clause_requires_both_physical_element_and_skill_kind(self):
        row = self.rows["chen_qianyu"]
        bonuses = fixed_weapon_bonuses(get_character("chen_qianyu"), row)
        self.assertEqual(bonuses, {"物理:skill": .42, "物理:ultimate": .42})
        panel = FixedDamagePanel(100, 0, 0, 1, 0, .5, damage_bonus=bonuses)
        self.assertEqual(panel.bonus_for("物理", ("skill",)), .42)
        self.assertEqual(panel.bonus_for("物理", ("ultimate",)), .42)
        self.assertEqual(panel.bonus_for("物理", ("skill", "skill")), .42)
        for element, tag in (("灼热", "skill"), ("物理", "normal"), ("物理", "combo"), ("物理", "physical_anomaly")):
            self.assertEqual(panel.bonus_for(element, (tag,)), 0)
        for skill in row["skills"]:
            quote = read_fixed_quote(row, skill)
            self.assertEqual(quote.panel.damage_bonus["物理:skill"], .42)
            self.assertEqual(quote.panel.damage_bonus["物理:ultimate"], .42)

    def test_stagger_predicate_changes_per_hit_and_never_buffs_teammates_or_other_enemies(self):
        world = self.world("chen_qianyu")
        self.assertEqual(self.hit(world), 100)
        world.apply_effect("actor", "target", SkillEffect(EffectType.STATUS_STAGGER, count=1, duration=1))
        self.assertEqual(self.hit(world), 198)
        self.assertEqual(self.hit(world, actor="ally"), 100)
        self.assertEqual(self.hit(world, enemy="other"), 100)
        world.advance(1)
        self.assertEqual(self.hit(world), 100)

    def test_shred_predicate_uses_current_layers_and_not_the_consumption_event(self):
        world = self.world("lifeng")
        world.apply_effect("actor", "target", SkillEffect(EffectType.STACK_SHRED, count=3))
        self.assertEqual(self.hit(world), 156)
        self.assertEqual(self.hit(world, effects=(SkillEffect(EffectType.STACK_SHRED, count=-1, consumes_all=True),)), 100)
        self.assertEqual(world.count("actor", "target", EffectType.EVENT_SHRED_CONSUMED), 3)
        self.assertEqual(self.hit(world), 100)

    def test_actual_hit_target_overrides_the_initial_action_target_predicate(self):
        world = self.world("lifeng")
        world.apply_effect("actor", "target", SkillEffect(EffectType.STACK_SHRED, count=1))
        world.enemies["other"] = EnemyCombatState()
        program = ActionProgram("redirect", "actor", "normal", 0, 0, 0, ())
        world.start(program, action_id="redirect")
        world._action_targets["redirect"] = {"current": ("other",)}
        world._execute_event("redirect", 900, program, CombatEvent(0, "hit", hit=DamageHit("actor", "target", "物理", 1)))
        self.assertEqual(world.damage, 100)

    def test_quote_requires_explicit_current_target_input_and_fixed_bonus_is_not_double_counted(self):
        row = self.rows["chen_qianyu"]
        quote = read_fixed_quote(row, next(skill for skill in row["skills"] if skill["type"] == "战技"))
        world = self.world("chen_qianyu")
        self.assertIsNone(quote.resolve(world.damage_state, actor="actor", enemy="target", now=0).expected)
        self.assertAlmostEqual(quote.resolve(world.damage_state, actor="actor", enemy="target", now=0,
                               inputs={"enemy.staggered": 0}).non_crit, quote.non_crit, delta=.051)
        active = quote.resolve(world.damage_state, actor="actor", enemy="target", now=0,
                               inputs={"enemy.staggered": 1})
        expected = quote.panel.attack() * quote.multiplier * (1 + quote.panel.bonus_for("物理", ("skill",)) + .98)
        self.assertAlmostEqual(active.non_crit, expected)

    def test_selected_catalog_registers_rules_once_and_prediction_is_isolated(self):
        world = build_combat_catalog(("陈千语", "黎风"), SkillTimingStore()).world
        for actor in ("1", "2"):
            self.assertEqual(len([spec for spec in world.passive_modifiers[actor] if spec.key.startswith("weapon:")]), 1)
        live = self.world("lifeng")
        fork = live.fork()
        fork.apply_effect("actor", "target", SkillEffect(EffectType.STACK_SHRED, count=1))
        self.assertEqual(self.hit(fork), 156)
        self.assertEqual(self.hit(live), 100)

    def test_actual_build_selection_excludes_unrelated_weapon_rules(self):
        row = self.rows["ember"]
        self.assertFalse(fixed_weapon_bonuses(get_character("ember"), row))
        self.assertFalse(state_bonus_rules(get_character("ember"), row))
