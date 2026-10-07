"""Validated native control flow cannot silently authorize a priced action."""

import copy
import unittest
from dataclasses import replace

from src.data.character_skills import get_character
from src.data.combat_simulation import CombatWorldState, walk_combat_events
from src.data.native_action_program import _SCENARIO_IGNORED, _nodes, compile_native_action
from src.data.native_gameplay import native_record
from src.data.skill_timing import SkillTimingStore


class TestNativeControlFlowBoundaries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = SkillTimingStore()
        cls.character = get_character("perlica")
        cls.profile = cls.store.profiles("佩丽卡", "battle")[0]

    def assert_native_boundary(self, record_id, node_type, expected_fields):
        record = native_record(self.store, record_id)["data"]
        node = copy.deepcopy(next(n for n in _nodes(record) if n["$type"].rsplit(".", 1)[-1] == node_type))
        for field, value in expected_fields.items():
            self.assertEqual(node["$value"][field], value)
        self.assertNotIn(node_type, _SCENARIO_IGNORED)
        program = compile_native_action(self.store, self.character, self.profile, "1", "normal",
                                        event_sequence={"actionData": [node]})
        reasons = [reason for event in walk_combat_events(program.events) for reason in event.unresolved]
        self.assertTrue(any(node_type in reason for reason in reasons), reasons)
        world = CombatWorldState(("1",), regen=0)
        program = replace(program, sp_cost=20, energy_cost=0, duration=.1, cooldown=0, gate=None)
        before = world.snapshot()
        self.assertIsNone(world.simulate(program))
        self.assertEqual(before, world.snapshot())

    def test_native_interrupt_keeps_targeted_immobilization_unresolved(self):
        self.assert_native_boundary("chr_0004_pelica_normal_skill", "InterruptAction+Data",
                                    {"immobilizedTime": 1.0, "overrideSuperArmorLimit": -1})

    def test_native_jump_remains_unresolved(self):
        self.assert_native_boundary('chr_0024_deepfin_combo_skill', 'JumpToAction+Data', {'destFrame': 4})

    def test_native_combo_cache_remains_unresolved(self):
        self.assert_native_boundary('chr_0019_karin_combo_skill', 'ComboCacheAction+Data', {})

    def test_native_temporary_unlock_remains_unresolved(self):
        self.assert_native_boundary('chr_0019_karin_combo_skill', 'TemporaryUnlockAction+Data', {'blockManualLock': False, 'disableLockAimPriority': 30.0})

    def test_native_curve_remains_unresolved(self):
        self.assert_native_boundary('chr_0019_karin_normal_skill', 'CurveEvaluateFloat+Data', {'key': 'cam_angle', 'useCustomCurve': True})
