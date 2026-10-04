"""Real native spell producers, recipients, timing and canonical buff queries."""

import unittest

from src.data.character_skills import get_character
from src.data.combat_expressions import CombatExpression, combat_input
from src.data.combat_model import EnemyCombatState
from src.data.combat_simulation import (
    ActionProgram,
    CombatEvent,
    CombatWorldState,
    NativeBuffChange,
    NativeBuffQuery,
    NativeListener,
    NativeSpellInfliction,
    NativeTarget,
)
from src.data.effects import EffectType
from src.data.native_action_program import compile_native_action
from src.data.native_spell_runtime import attachment_policies
from src.data.native_tags import tag_hash
from src.data.skill_timing import SkillTimingStore


class TestNativeSpellInfliction(unittest.TestCase):
    def setUp(self):
        self.world = CombatWorldState(("1", "2"), regen=0)
        self.world.enemies["other"] = EnemyCombatState()

    def cast(self, kind, *, target="enemy", source="owner", action="spell", extra=False):
        event = CombatEvent(0, "spell", native_spells=(NativeSpellInfliction(
            kind, NativeTarget(source), NativeTarget(target), extra),))
        program = ActionProgram(action, "1", "normal", 0, 0, 0, (event,))
        self.assertTrue(self.world.start(program, action_id=action))

    def test_native_policy_binds_real_buff_duration_cap_and_element_tags(self):
        for kind, (key, effect, duration, tags) in attachment_policies().items():
            self.assertEqual(duration, 20)
            self.assertTrue(tags)
            self.cast(kind, action=f"element{kind}")
            self.assertEqual(self.world.enemies["target"].infliction_time_left, 20)
            self.assertEqual(self.world.native_buff_count("target", key), 1)
            self.world.consume("1", "target", effect)

    def test_real_perlica_timeline_compiles_spell_producer_with_authored_target(self):
        store = SkillTimingStore()
        profile = store.profiles("佩丽卡", "battle")[0]
        program = compile_native_action(store, get_character("perlica"), profile, "1", "battle")
        events = [e for e in program.events if e.native_spells]
        self.assertEqual(len(events), 1)
        spell = events[0].native_spells[0]
        self.assertEqual((spell.infliction_type, spell.source, spell.target),
                         (1, NativeTarget("owner"), NativeTarget("context", "tar")))
        self.assertGreater(events[0].at, 0)
        self.assertFalse(any("SpellInfliction+Data" in error for e in program.events for error in e.unresolved))

    def test_empty_target_and_invalid_recipient_do_not_apply_to_default_enemy(self):
        self.cast(1, target="context", action="empty")
        self.assertEqual(self.world.enemies["target"].infliction_stacks, 0)
        self.cast(1, target="squad", action="invalid")
        self.assertEqual(self.world.enemies["target"].infliction_stacks, 0)
        self.assertTrue(any("recipient" in error for error in self.world.unresolved))

    def test_selected_enemy_stacks_refreshes_caps_consumes_and_expires(self):
        for i in range(6):
            self.cast(1, action=f"stack{i}")
        self.assertEqual(self.world.enemies["target"].infliction_stacks, 4)
        self.assertEqual(self.world.enemies["other"].infliction_stacks, 0)
        self.world.advance(19)
        self.cast(1, action="refresh")
        self.world.advance(20)
        self.assertEqual(self.world.enemies["target"].infliction_stacks, 4)
        key = attachment_policies()[1][0]
        self.world.consume("1", "target", EffectType.ATTACH_ELECTROMAGNETIC, 2)
        self.assertEqual(self.world.native_buff_count("target", key), 2)
        self.world.advance(39)
        self.assertEqual(self.world.native_buff_count("target", key), 0)
        self.assertEqual(self.world.native_buff_count("other", key), 0)

    def test_native_id_and_parent_tag_queries_read_same_canonical_pool(self):
        self.cast(1)
        self.cast(1, action="again")
        key = attachment_policies()[1][0]
        parent = tag_hash("Skill/Character/Common")
        queries = (NativeBuffQuery("query.id", NativeTarget("main_target"), (key,)),
                   NativeBuffQuery("query.tag", NativeTarget("main_target"), (), "HasAny", (parent,)),
                   NativeBuffQuery("query.distinct", NativeTarget("main_target"), (), "HasAny", (parent,), 1))
        event = CombatEvent(0, "read", assignments=tuple(("bb." + q.key, combat_input(q.key)) for q in queries))
        program = ActionProgram("query", "1", "normal", 0, 0, 0, (event,), native_buff_queries=queries)
        self.world.start(program, action_id="read")
        values = self.world._action_inputs["read"]
        self.assertEqual((values["bb.query.id"], values["bb.query.tag"], values["bb.query.distinct"]), (2, 2, 1))

    def test_pre_and_post_callbacks_see_actual_target_before_and_after_transition(self):
        key = attachment_policies()[1][0]
        self.world.native_buffs["2", "listen"] = (1, None)
        query = NativeBuffQuery("q", NativeTarget("action_target"), (key,))
        observer = ActionProgram("observe", "2", "normal", 0, 0, 0, (), native_buff_queries=(query,))
        self.world._action_inputs["observer"] = {}
        self.world._action_targets["observer"] = {"current": ("2",)}
        for trigger, name in (("OnCharBeforeOutputSpellInfliction", "before"),
                              ("OnCharAfterOutputSpellInfliction", "after")):
            callback = CombatEvent(0, name, assignments=(("bb." + name, combat_input("q")),
                                                        ("bb.extra", combat_input("event.is_extra"))))
            self.world.native_listeners.append(("2", "observer", observer, NativeListener("listen", trigger, (callback,))))
        self.cast(1, source="main", extra=True)
        # Main defaults to actor 1; move it to actor 2 and verify source ownership.
        self.world.main_control = "2"
        self.cast(1, source="main", action="source2", extra=True)
        self.assertEqual(self.world._action_inputs["observer"]["bb.before"], 1)
        self.assertEqual(self.world._action_inputs["observer"]["bb.after"], 2)
        self.assertEqual(self.world._action_inputs["observer"]["bb.extra"], 1)
        self.assertEqual(self.world._action_targets["observer"]["current"], ("2",))

    def test_cross_element_consumes_all_and_keeps_missing_damage_explicit(self):
        self.cast(2)
        self.cast(2, action="cold2")
        self.cast(3, action="natural")
        for target in self.world.enemies.values():
            self.assertIsNone(target.infliction_element)
            self.assertEqual(target.infliction_stacks, 0)
        self.assertIn("Unbound spell reaction: STATUS_CORROSION", self.world.unresolved)
        self.assertEqual(self.world.damage, 0)

    def test_authored_target_group_applies_to_each_enemy_and_prediction_isolated(self):
        spell = CombatEvent(.1, "spell", native_spells=(NativeSpellInfliction(
            0, NativeTarget("owner"), NativeTarget("context", "both")),))
        program = ActionProgram("multi", "1", "normal", 0, .1, 0, (spell,))
        prediction = self.world.fork()
        prediction.start(program, action_id="multi")
        # This explicit context is the scenario's observed target group.
        prediction._action_targets["multi"]["both"] = ("target", "other")
        prediction.advance(.1)
        for enemy in prediction.enemies.values():
            self.assertEqual(enemy.infliction_stacks, 1)
        for enemy in self.world.enemies.values():
            self.assertEqual(enemy.infliction_stacks, 0)

    def test_native_finish_removes_canonical_layers_without_a_shadow_pool(self):
        self.cast(0)
        self.cast(0, action="second")
        key = attachment_policies()[0][0]
        change = NativeBuffChange(key, CombatExpression("literal", (-1.0,)), selector=NativeTarget("main_target"))
        program = ActionProgram("remove", "1", "normal", 0, 0, 0,
                                (CombatEvent(0, "remove", native_buffs=(change,)),))
        self.world.start(program, action_id="remove1")
        self.assertEqual(self.world.native_buff_count("target", key), 1)
        self.world.start(program, action_id="remove2")
        self.assertEqual(self.world.native_buff_count("target", key), 0)
        self.assertEqual(self.world.enemies["target"].infliction_time_left, 0)
        self.assertNotIn(("target", key), self.world.native_buffs)


if __name__ == "__main__":
    unittest.main()
