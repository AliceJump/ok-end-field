"""Confirmed Alesh rare-outcome creation fragment and native Refresh timer."""

import unittest
from dataclasses import replace

from src.data.character_skills import get_character
from src.data.combat_expressions import CombatExpression, combat_input
from src.data.combat_simulation import ActionProgram, CombatEvent, CombatWorldState
from src.data.damage_resolution import DamageHit, FixedDamagePanel
from src.data.native_action_program import _nodes, compile_native_action
from src.data.native_attribute_modifiers import ALESH_TEAM_ATTACK, reviewed_attack_binding
from src.data.native_buff_runtime import _single, finish_buff_instances
from src.data.native_damage_processors import resolve_native_hit
from src.data.skill_timing import SkillTimingStore


class TestNativeRefreshAttack(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = SkillTimingStore()
        cls.character = get_character("alesh")
        cls.profile = cls.store.profiles("阿列什", "link")[0]
        nodes = list(_nodes(cls.store.record(cls.profile.skill_id)["data"]))
        find = next(n for n in nodes if n.get("$type", "").endswith(".FindTargetAction+FindTargetActionData")
                    and n["$value"]["targetGroupKey"] == "team")
        create = next(n for n in nodes if n.get("$type", "").endswith(".CreateBuffAction+Data")
                      and ALESH_TEAM_ATTACK in str(n))
        # This is explicitly the creation fragment AFTER a confirmed rare-fish
        # outcome; its chance/result producer is not replaced with a cast event.
        cls.fragment = {"actionData": [find, create], "onlyExecuteWhenSourceIsGuard": False,
                        "onlyExecuteWhenSourceIsMainChar": False}

    def setUp(self):
        self.world = CombatWorldState(("1", "2", "3"), regen=0)
        for state in self.world.characters.values():
            state.panel = FixedDamagePanel(100, .5, 25, 2, 0, .5)
            state.energy = 1000

    def producer(self, actor="1", *, amount=None, duration=None):
        bb = {}
        if amount is not None:
            bb["atk_up"] = amount
        if duration is not None:
            bb["Duration"] = duration
        return replace(compile_native_action(self.store, self.character, self.profile, actor, "link",
                                             event_sequence=self.fragment, event_blackboard=bb),
                       duration=0, actor_lock=0, cooldown=0)

    def add(self, *, actor="1", amount=None, duration=None, world=None):
        world = world or self.world
        self.assertTrue(world.start(self.producer(actor, amount=amount, duration=duration),
                                    action_id=f"add:{world._actions_started}"))

    def result(self, actor="2", world=None):
        world = world or self.world
        return resolve_native_hit(world, world.characters[actor].panel, DamageHit(actor, "target", "物理", 1), {})

    def test_original_target_group_and_inheritance_give_one_timed_instance_per_live_owner(self):
        self.world.characters["3"].alive = False
        self.add()
        self.assertFalse(self.world.unresolved)
        self.assertEqual({v.owner for v in self.world.native_buff_instances.values()}, {"1", "2"})
        for instance in self.world.native_buff_instances.values():
            self.assertEqual(instance.definition.stacking, 4)
            self.assertEqual(instance.expires, 10)
            self.assertEqual(self.world._action_inputs[instance.uid]["bb.duration"], 10)
        self.assertAlmostEqual(self.result().non_crit, (100 * (1.5 + _single(.15)) + 25) * 2)
        self.world.advance(10)
        self.assertEqual(self.result().non_crit, 350)
        self.assertFalse(self.world.native_buff_instances)
        binding = reviewed_attack_binding(self.character, self.store)
        self.assertIn("rare_fish_outcome_and_full_combo_not_bound", binding["verified_scope"])
        self.assertIsNone(reviewed_attack_binding(get_character("alesh", potential=2), self.store))

    def test_reapply_retains_uid_source_blackboard_and_snapshot_and_ignores_old_deadline(self):
        self.add()
        initial = {v.owner: v.uid for v in self.world.native_buff_instances.values()}
        self.world.advance(3)
        self.add(actor="2", amount=.9, duration=10)
        self.assertEqual({v.owner: v.uid for v in self.world.native_buff_instances.values()}, initial)
        for instance in self.world.native_buff_instances.values():
            self.assertEqual(instance.source, "1")
            self.assertEqual(instance.expires, 13)
            self.assertAlmostEqual(instance.attack_addition, .15, places=6)
            self.assertAlmostEqual(self.world._action_inputs[instance.uid]["bb.atk_up"], .15, places=6)
        self.world.advance(10)
        self.assertEqual(len(self.world.native_buff_instances), 3)
        self.assertAlmostEqual(self.result().non_crit, 380, places=5)
        self.world.advance(13)
        self.assertEqual(self.result().non_crit, 350)

    def test_shorter_reapplication_preserves_long_remaining_and_near_epsilon_can_shorten(self):
        self.add(duration=20)
        self.world.advance(1)
        self.add(duration=2)
        self.assertEqual({v.expires for v in self.world.native_buff_instances.values()}, {20})
        near = _single(19 - 5e-6)
        self.add(duration=near)
        self.assertEqual({v.expires for v in self.world.native_buff_instances.values()}, {1 + near})
        self.assertLess(1 + near, 20)

    def test_removal_and_new_creation_do_not_reuse_old_timeline_or_old_snapshot(self):
        self.add()
        old = tuple(self.world.native_buff_instances)
        finish_buff_instances(self.world, old)
        self.world.advance(2)
        self.add(amount=.2)
        self.assertFalse(set(old).intersection(self.world.native_buff_instances))
        self.world.advance(10)
        self.assertAlmostEqual(self.result().non_crit, 390, places=5)
        self.world.advance(12)
        self.assertEqual(self.result().non_crit, 350)

    def test_fork_refresh_is_isolated_and_does_not_add_a_second_attack_layer(self):
        self.add()
        self.world.advance(1)
        fork = self.world.fork()
        self.add(duration=20, world=fork)
        self.assertEqual({v.expires for v in fork.native_buff_instances.values()}, {21})
        self.assertEqual({v.expires for v in self.world.native_buff_instances.values()}, {10})
        self.assertEqual(self.result(world=fork).non_crit, self.result().non_crit)
        self.world.advance(10)
        fork.advance(10)
        self.assertEqual(self.result().non_crit, 350)
        self.assertAlmostEqual(self.result(world=fork).non_crit, 380, places=5)

    def test_refresh_does_not_read_stale_maximum_and_long_or_permanent_timer_is_preserved(self):
        program = self.producer()
        event = next(e for e in program.events if e.native_buffs)
        change, = event.native_buffs
        definition = replace(change.definition, maximum=combat_input("bb.missing_maximum"))
        changed = replace(change, definition=definition)
        program = replace(program, events=tuple(replace(e, native_buffs=(changed,)) if e == event else e
                                                for e in program.events))
        self.assertTrue(self.world.start(program, action_id="stale_maximum"))
        self.assertFalse(self.world.unresolved)
        permanent = replace(changed, definition=replace(definition, duration=None))
        program = replace(program, events=tuple(replace(e, native_buffs=(permanent,)) if e.native_buffs else e
                                                for e in program.events))
        self.assertTrue(self.world.start(program, action_id="permanent"))
        self.assertTrue(all(v.expires is None for v in self.world.native_buff_instances.values()))
        self.add(duration=2)
        self.world.advance(10)
        self.assertEqual(len(self.world.native_buff_instances), 3)
        self.assertAlmostEqual(self.result().non_crit, 380, places=5)

    def test_unreviewed_refresh_callbacks_periodic_or_action_roots_stay_unresolved(self):
        program = self.producer()
        event = next(e for e in program.events if e.native_buffs)
        change, = event.native_buffs
        definition = change.definition
        self.assertFalse(definition.unresolved)
        callback = ActionProgram("callback", "1", "normal", 0, 0, 0, (CombatEvent(0, "callback"),))
        cases = [replace(change, key="unknown_refresh"), replace(change, action_finish_after=1),
                 replace(change, definition=replace(definition, callbacks=((0, callback),))),
                 replace(change, definition=replace(definition, period=CombatExpression("literal", (1.0,)))),
                 replace(change, definition=replace(definition, period=combat_input("bb.missing_period")))]
        for changed in cases:
            world = CombatWorldState(("1",), regen=0)
            changed = replace(changed, selector=None, target="self")
            test = replace(program, events=(replace(event, native_buffs=(changed,)),))
            self.assertTrue(world.start(test, action_id="bad"))
            self.assertFalse(world.native_buff_instances)
            self.assertTrue(world.unresolved)
