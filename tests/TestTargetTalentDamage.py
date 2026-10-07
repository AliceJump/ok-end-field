"""Hit-time talent predicates use the current enemy and selected talent rank."""

import unittest

from src.data.character_skills import get_character
from src.data.combat_model import EnemyCombatState
from src.data.combat_simulation import ActionProgram, CombatEvent, CombatWorldState
from src.data.damage_resolution import DamageHit, FixedDamagePanel
from src.data.effects import EffectType
from src.data.native_tags import matches_tags, tag_query
from src.data.skill_timing import SkillTimingStore
from src.data.skill_types import SkillEffect


class TestTargetTalentDamage(unittest.TestCase):
    def world(self, character):
        world = CombatWorldState(("owner", "ally"), regen=0)
        for state in world.characters.values():
            state.panel = FixedDamagePanel(100, 0, 0, 1, 1, .5)
        world.register_damage_passives("owner", get_character(character).progression.damage_modifiers)
        return world

    def hit(self, world, *, actor="owner", enemy="target", effects=(), inputs=()):
        before = world.damage
        event = CombatEvent(0, "hit", hit=DamageHit(actor, enemy, "物理", 1), effects=effects, inputs=inputs)
        self.assertTrue(world.start(ActionProgram("hit", actor, "normal", 0, 0, 0, (event,), enemy=enemy),
                                    action_id=f"hit:{world._actions_started}"))
        return world.damage - before

    def apply(self, world, effect, duration=2):
        world.apply_effect("owner", "target", SkillEffect(effect, count=1, duration=duration))

    def test_frozen_native_guards_do_not_add_cold_and_frozen_together(self):
        native = SkillTimingStore().record("buff_chr_0017_yvonne_talent_0")["data"]
        modifiers = native["damageModifier"]
        self.assertEqual(len(modifiers), 3)
        for cold, frozen, expected in ((False, False, []), (True, False, ["inflict_up"]),
                                       (False, True, ["status_up"]), (True, True, ["status_up"])):
            tags = tuple(int.from_bytes(bytes.fromhex(raw), "little", signed=True)
                         for present, raw in ((cold, "1cdba15d"), (frozen, "55af885b")) if present)
            matched = []
            for modifier in modifiers:
                self.assertEqual(modifier["enableSide"], 0)
                tests = [tag_query(action["$value"]["query"]) for action in modifier["condition"]["actionData"]]
                processor, = modifier["damageProcessors"]
                self.assertEqual(processor["$type"], "Beyond.Gameplay.Core.InstantModifyAttribute")
                self.assertEqual(processor["$value"]["modifyTargetSide"], 0)
                attr = processor["$value"]["modifier"]
                self.assertEqual((attr["attributeType"], attr["formulaItem"], attr["modifyAttributeType"]), (10, 5, 0))
                if all(matches_tags(tags, mode, ids) for mode, ids in tests):
                    matched.append(attr["param"]["blackboardKey"])
            self.assertEqual(matched, expected)

    def test_yvonne_current_states_expiry_and_consumption(self):
        world = self.world("yvonne")
        self.assertEqual(self.hit(world), 150)
        self.apply(world, EffectType.ATTACH_COLD)
        self.assertAlmostEqual(self.hit(world), 170, places=5)
        self.apply(world, EffectType.STATUS_FROZEN, duration=1)
        self.assertAlmostEqual(self.hit(world), 190, places=5)
        world.advance(1)
        self.assertAlmostEqual(self.hit(world), 170, places=5)
        self.assertEqual(self.hit(world, effects=(SkillEffect(EffectType.ATTACH_COLD, count=-1, consumes_all=True),)), 150)
        self.apply(world, EffectType.STATUS_FROZEN, duration=1)
        self.assertAlmostEqual(self.hit(world), 190, places=5)
        self.assertEqual(self.hit(world, actor="ally"), 150)
        self.assertEqual(self.hit(world, enemy="other"), 150)
        world.advance(2)
        self.assertEqual(self.hit(world), 150)

    def test_yvonne_modifies_only_critical_component_without_inventing_critical_events(self):
        world = self.world("yvonne")
        self.apply(world, EffectType.STATUS_FROZEN)
        hit = DamageHit("owner", "target", "电磁", 1, can_crit=False)
        result = world.damage_state.resolve_hit(world.characters["owner"].panel, hit, now=0,
                                              inputs=world.damage_inputs("owner"))
        self.assertEqual(result.non_crit, 100)
        self.assertEqual(result.expected, 100)
        self.assertAlmostEqual(result.buckets["crit_damage"], .4, places=6)

    def test_redirect_and_stale_event_inputs_cannot_retain_original_target_bonuses(self):
        for character, effect in (("yvonne", EffectType.STATUS_FROZEN), ("fluorite", EffectType.STATUS_SLOW),
                                   ("perlica", EffectType.STATUS_STAGGER)):
            with self.subTest(character=character):
                world = self.world(character)
                self.apply(world, effect)
                world.enemies["other"] = EnemyCombatState()
                program = ActionProgram("redirect", "owner", "normal", 0, 0, 0, ())
                world.start(program, action_id="redirect")
                world._action_targets["redirect"] = {"current": ("other",)}
                event = CombatEvent(0, "hit", hit=DamageHit("owner", "target", "物理", 1),
                                    inputs=tuple(world.damage_inputs("owner").items()))
                world._execute_event("redirect", 900, program, event)
                self.assertEqual(world.damage, 150)

    def test_slow_and_stagger_talents_follow_current_state_cleanup(self):
        for character, effect, amount in (("fluorite", EffectType.STATUS_SLOW, .2),
                                         ("perlica", EffectType.STATUS_STAGGER, .3)):
            with self.subTest(character=character):
                world = self.world(character)
                self.assertEqual(self.hit(world), 150)
                self.apply(world, effect, duration=1)
                self.assertAlmostEqual(self.hit(world), 150 * (1 + amount), places=5)
                world.advance(1)
                self.assertEqual(self.hit(world), 150)

    def test_standalone_calculation_requires_current_predicates(self):
        for character in ("yvonne", "fluorite", "perlica"):
            with self.subTest(character=character):
                world = self.world(character)
                hit = DamageHit("owner", "target", "物理", 1)
                result = world.damage_state.resolve_hit(world.characters["owner"].panel, hit, now=0)
                self.assertIsNone(result.expected)
                self.assertTrue(result.unknown)
                known = world.damage_state.resolve_hit(world.characters["owner"].panel, hit, now=0,
                                                      inputs=world.damage_inputs("owner"))
                self.assertEqual(known.expected, 150)
                fork = world.fork()
                self.apply(fork, EffectType.STATUS_FROZEN)
                self.assertEqual(world.count("owner", "target", EffectType.STATUS_FROZEN), 0)
