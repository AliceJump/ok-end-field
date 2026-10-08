"""Successful model releases produce only evidence-reviewed, selected bonuses."""

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from src.data.character_skills import get_character
from src.data.combat_catalog import build_combat_catalog
from src.data.combat_simulation import ActionProgram, CombatWorldState
from src.data.damage_release_rules import release_rules
from src.data.damage_resolution import DamageHit, FixedDamagePanel
from src.data.native_action_program import _nodes
from src.data.native_gameplay import native_enums, native_record
from src.data.reviewed_weapon_release import guzhou_ultimate_rule
from src.data.skill_timing import SkillTimingStore

ROOT = Path(__file__).resolve().parents[1]


class TestDamageReleaseRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = {row["key"]: row for row in json.loads(
            (ROOT / "assets/data/fixed_damage_baseline.json").read_text(encoding="utf-8"))}

    def world(self, key):
        row = self.rows[key]
        character = get_character(key, skill_rank=row["profile"]["skill_rank"], potential=row["profile"]["potential"])
        world = CombatWorldState(("caster", "ally"), regen=0)
        world.characters["caster"].attributes = {k: v for k, v in row["panel"].items() if type(v) in (int, float)}
        for kind, rules in release_rules(character, row).items():
            world.register_release_modifiers("caster", kind, rules)
        return world

    def cast(self, world, kind, action_id="cast"):
        return world.start(ActionProgram(kind, "caster", kind, 0, .1, 0, ()), action_id=action_id)

    def result(self, world, element="寒冷", actor="caster", tag="skill"):
        return world.damage_state.resolve_hit(FixedDamagePanel(100, 0, 0, 1, 0, .5),
            DamageHit(actor, "target", element, 1, damage_tag=tag), now=world.time)

    def test_antal_release_affects_team_only_matching_elements_and_expires(self):
        world = self.world("antal")
        self.assertTrue(self.cast(world, "ult"))
        for actor in ("caster", "ally"):
            self.assertAlmostEqual(self.result(world, "电磁", actor).non_crit, 122, places=5)
            self.assertEqual(self.result(world, "物理", actor).non_crit, 100)
        world.advance(12)
        self.assertEqual(self.result(world, "电磁").non_crit, 100)

    def test_failed_duplicate_and_wrong_skill_type_do_not_produce_release_bonus(self):
        world = self.world("antal")
        world.characters["caster"].alive = False
        self.assertFalse(self.cast(world, "ult"))
        self.assertFalse(world.damage_state.modifiers)
        world.characters["caster"].alive = True
        self.assertTrue(self.cast(world, "battle", "battle"))
        self.assertFalse(world.damage_state.modifiers)
        world.advance(.1)
        self.assertTrue(self.cast(world, "ult", "ult"))
        self.assertFalse(self.cast(world, "ult", "ult"))
        self.assertEqual(len(world.damage_state.modifiers), 4)

    def test_xaihi_snapshots_source_wisdom_and_refreshes_without_stacking(self):
        world = self.world("xaihi")
        world.characters["caster"].attributes["native.final_nonconverted.41"] = 100
        self.cast(world, "ult")
        old = self.result(world, actor="ally").non_crit
        self.assertAlmostEqual(old, 100 * (1 + .264 + .033), places=5)
        world.characters["caster"].attributes["native.final_nonconverted.41"] = 1000
        self.assertEqual(self.result(world, actor="ally").non_crit, old)
        world.advance(1)
        self.cast(world, "ult", "second")
        self.assertEqual(len(world.damage_state.modifiers), 4)
        self.assertAlmostEqual(self.result(world, actor="ally").non_crit, 100 * (1 + .264 + .33), places=5)
        world.advance(12)
        self.assertGreater(self.result(world, actor="ally").non_crit, 100)
        world.advance(13)
        self.assertEqual(self.result(world, actor="ally").non_crit, 100)

    def test_xaihi_native_attribute_domain_never_falls_back_to_display_panel(self):
        world = self.world("xaihi")
        world.characters["caster"].attributes["智识"] = 1000
        self.cast(world, "ult")
        self.assertIsNone(self.result(world, actor="ally").non_crit)

    def test_xaihi_input_domain_is_verified_against_the_native_producer(self):
        store = SkillTimingStore()
        data = native_record(store, "buff_chr_0011_seraph_atk_buff")["data"]
        reads = [node["$value"] for node in _nodes(data) if node["$type"].endswith(".StoreAttributeValue+Data")]
        self.assertEqual(len(reads), 1)
        read = reads[0]
        self.assertEqual(read["attributeType"], native_enums()["Beyond.GEnums.AttributeType"]["Wisd"]["value"])
        self.assertEqual(read["storeAttributeType"], native_enums()["Beyond.Gameplay.Core.StoreAttributeValue+StoreAttributeType"]["FinalNonConverted"]["value"])
        self.assertEqual(read["targetSettings"]["targetSource"], 1)
        character = get_character("xaihi")
        for effect in character.skills[3].effects:
            if effect.damage_modifier:
                self.assertEqual(effect.damage_modifier.magnitude.terms[0].input, "source.native.final_nonconverted.41")

    def test_jet_battle_and_combo_are_independent_and_never_buff_physical_or_allies(self):
        world = self.world("avywenna")
        self.cast(world, "battle")
        self.assertAlmostEqual(self.result(world).non_crit, 133.6)
        world.advance(1)
        self.cast(world, "link", "link")
        self.assertAlmostEqual(self.result(world).non_crit, 167.2)
        self.assertEqual(self.result(world, "物理").non_crit, 100)
        self.assertEqual(self.result(world, actor="ally").non_crit, 100)
        world.advance(15)
        self.assertAlmostEqual(self.result(world).non_crit, 133.6)
        world.advance(16)
        self.assertEqual(self.result(world).non_crit, 100)

    def test_forgotten_modes_are_independent_and_laevatain_bonus_only_normal(self):
        world = self.world("perlica")
        self.cast(world, "ult")
        world.advance(1)
        self.cast(world, "link", "link")
        self.assertAlmostEqual(self.result(world).non_crit, 200.8)
        world = self.world("laevatain")
        self.cast(world, "ult")
        self.assertEqual(self.result(world, "灼热").non_crit, 100)
        self.assertAlmostEqual(self.result(world, "灼热", tag="normal").non_crit, 310)

    def test_outcome_aura_unreviewed_after_release_and_immunity_rules_are_not_auto_bound(self):
        for key in ("camille", "gilberta", "liino", "fluorite"):
            world = self.world(key)
            self.assertFalse(any(spec for specs in world.release_modifiers.values() for spec in specs
                                 if not spec.key.startswith("set:清波:")), key)
        world = self.world("zhuang_fangyi")
        self.assertFalse(world.release_modifiers["caster", "battle"])
        self.assertFalse(world.release_modifiers["caster", "link"])
        self.assertEqual(len(world.release_modifiers["caster", "ult"]), 1)

    def test_guzhou_native_precast_evidence_produces_only_pulse_battle_bonus(self):
        world = self.world("zhuang_fangyi")
        self.cast(world, "battle", "battle")
        self.assertFalse(world.damage_state.modifiers)
        world.advance(.1)
        self.cast(world, "link", "combo")
        self.assertFalse(world.damage_state.modifiers)
        world.advance(.2)
        self.assertTrue(self.cast(world, "ult", "ultimate"))
        self.assertFalse(self.cast(world, "ult", "ultimate"))
        self.assertEqual(len(world.damage_state.modifiers), 1)
        self.assertAlmostEqual(self.result(world, "电磁").non_crit, 212, places=5)
        self.assertEqual(self.result(world, "电磁", actor="ally").non_crit, 100)
        self.assertEqual(self.result(world, "物理").non_crit, 100)
        for tag in ("normal", "combo", "ultimate", "physical_anomaly"):
            self.assertEqual(self.result(world, "电磁", tag=tag).non_crit, 100)
        world.advance(25.2)
        self.assertEqual(self.result(world, "电磁").non_crit, 100)

    def test_guzhou_refresh_early_removal_and_prediction_are_independent(self):
        world = self.world("zhuang_fangyi")
        world.characters["caster"].alive = False
        self.assertFalse(self.cast(world, "ult"))
        self.assertFalse(world.damage_state.modifiers)
        world.characters["caster"].alive = True
        self.cast(world, "ult", "first")
        world.advance(5)
        self.cast(world, "ult", "second")
        self.assertEqual(len(world.damage_state.modifiers), 1)
        fork = world.fork()
        spec, = world.release_modifiers["caster", "ult"]
        fork.damage_state.remove(spec.key, "caster")
        self.assertEqual(self.result(fork, "电磁").non_crit, 100)
        world.advance(25)
        self.assertAlmostEqual(self.result(world, "电磁").non_crit, 212, places=5)
        world.advance(30)
        self.assertEqual(self.result(world, "电磁").non_crit, 100)

    def test_guzhou_native_rank_overrides_default_and_no_consume_proc_is_bound(self):
        data = json.loads((ROOT / "assets/data/equipment_mechanics/20261008/guzhou.json").read_text(encoding="utf-8"))
        defaults = {v["key"]: v["valueDouble"] for v in data["records"]["sk_wpn_funnel_0015"]["data"]["blackboard"]}
        self.assertEqual(defaults["duration2"], 10)
        self.assertEqual(defaults["pulse_dmg_up3"], .5)
        spec, = self.world("zhuang_fangyi").release_modifiers["caster", "ult"]
        self.assertEqual(spec.duration, 25)
        self.assertEqual(spec.magnitude.base, 1.1200000047683716)
        self.assertTrue(any("OnBeforeCastSkill" in s for s in spec.sources))

    def test_guzhou_changed_native_damage_filter_is_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "assets/data/equipment_mechanics/20261008"
            folder.mkdir(parents=True)
            data = json.loads((ROOT / "assets/data/equipment_mechanics/20261008/guzhou.json").read_text(encoding="utf-8"))
            manifest = json.loads((ROOT / "assets/data/equipment_mechanics/20261008/index.json").read_text(encoding="utf-8"))
            data["records"]["buff_wpn_funnel_0015_ultimate"]["data"]["damageModifier"][0]["condition"]["actionData"][1]["$value"]["damageTypeMask"] = 1
            raw = json.dumps(data).encode("utf-8")
            manifest["files"]["guzhou.json"] = hashlib.sha256(raw).hexdigest()
            (folder / "guzhou.json").write_bytes(raw)
            (folder / "index.json").write_text(json.dumps(manifest), encoding="utf-8")
            timing = root / "assets/data/skill_timings/20261002"
            timing.mkdir(parents=True)
            (timing / "index.json").write_text(json.dumps({"native_inputs": manifest["native_inputs"]}), encoding="utf-8")
            weapon = json.loads((ROOT / "assets/data/weapons.json").read_text(encoding="utf-8"))["孤舟"]
            build = json.loads((ROOT / "assets/data/character_builds/zhuang_fangyi.json").read_text(encoding="utf-8"))
            with self.assertRaisesRegex(ValueError, "damage filter"):
                guzhou_ultimate_rule(weapon, build, root=root)

    def test_clearwave_requires_three_equipped_pieces_and_has_independent_two_layer_timers(self):
        # The baseline Tangtang build actually equips three pieces from this set.
        world = self.world("tangtang")
        self.cast(world, "link")
        self.assertAlmostEqual(self.result(world, "物理").non_crit, 120)
        world.advance(1)
        self.cast(world, "link", "second")
        self.assertAlmostEqual(self.result(world, "物理").non_crit, 140)
        self.assertEqual(self.result(world, "物理", tag="physical_anomaly").non_crit, 100)
        world.advance(15)
        self.assertAlmostEqual(self.result(world, "物理").non_crit, 120)
        world.advance(16)
        self.assertEqual(self.result(world, "物理").non_crit, 100)
        row = dict(self.rows["tangtang"], build=dict(self.rows["tangtang"]["build"], pieces=[]))
        character = get_character("tangtang", skill_rank=row["profile"]["skill_rank"], potential=row["profile"]["potential"])
        self.assertFalse(any(spec.key.startswith("set:") for specs in release_rules(character, row).values() for spec in specs))

    def test_catalog_registers_selected_rules_and_prediction_does_not_mutate_live_state(self):
        catalog = build_combat_catalog(("安塔尔", "赛希"), SkillTimingStore())
        world = catalog.world
        self.assertEqual(len(world.release_modifiers["1", "ult"]), 2)
        self.assertEqual(len(world.release_modifiers["2", "ult"]), 2)
        fork = world.fork()
        # The full native program still has independent unresolved nodes. This
        # fixture tests model release dispatch without asserting native coverage.
        program = replace(catalog.candidates("1", "ult")[0], events=(), energy_cost=0,
                          sp_cost=0, gate=None, duration=.1)
        self.assertTrue(fork.start(program, action_id="prediction"))
        self.assertTrue(fork.damage_state.modifiers)
        self.assertFalse(world.damage_state.modifiers)
