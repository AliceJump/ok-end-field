"""CharacterMechanic v1: 核心主C机制必须从快照保真到规划层。"""

from __future__ import annotations

import unittest

from src.data.character_mechanics import (
    clear_mechanics_cache,
    load_character_mechanics,
    mechanic_blockers,
)
from src.data.skill_timing import load_skill_timings
from src.data.timing_dps import DamageQuote, build_options


class TestCharacterMechanics(unittest.TestCase):
    def setUp(self):
        clear_mechanics_cache()

    def tearDown(self):
        clear_mechanics_cache()

    def test_registry_covers_first_four_mechanic_archetypes(self):
        mechanics = load_character_mechanics()
        self.assertEqual(mechanics["弭弗"].archetype, "multi_stage_battle")
        self.assertEqual(mechanics["提弗洛斯"].archetype, "main_control_attack_channel")
        self.assertEqual(mechanics["庄方宜"].archetype, "consume_status_build_stack_burst")
        self.assertEqual(mechanics["伊冯"].archetype, "consume_attachment_freeze_control")
        self.assertTrue(all(not mechanics[name].generic_cycle_safe for name in ("弭弗", "提弗洛斯", "庄方宜", "伊冯")))

    def test_mifu_three_stage_cost_and_refund_are_parsed_from_snapshot(self):
        mechanic = load_character_mechanics()["弭弗"]
        battle = [item for item in mechanic.transitions if item.action == "battle"]
        self.assertEqual([item.phase for item in battle], ["断云", "追形", "开天"])
        self.assertEqual([item.sp_gate for item in battle], [100, 50, 50])
        self.assertEqual([item.sp_cost for item in battle], [100, 50, 50])
        self.assertEqual(battle[0].sp_refund, 50)
        self.assertIn("shred>=3", battle[1].requires)

    def test_typhoeus_private_resources_and_resets_are_preserved(self):
        mechanic = load_character_mechanics()["提弗洛斯"]
        resources = {item.key: item.maximum for item in mechanic.resources}
        self.assertEqual(resources["insight"], 8)
        self.assertEqual(resources["air_shots"], 5)
        link = next(item for item in mechanic.transitions if item.action == "link")
        ult = next(item for item in mechanic.transitions if item.action == "ult")
        self.assertIn("STACK_HUNTING_ARROW:4", link.produces)
        self.assertIn("reset:air_shots", link.produces)
        self.assertIn("STACK_HUNTING_ARROW:2", ult.produces)

    def test_zhuang_ultimate_free_first_battle_is_explicit(self):
        mechanic = load_character_mechanics()["庄方宜"]
        free = next(item for item in mechanic.transitions if item.phase == "天理合真首次惊霆诀")
        self.assertEqual(free.sp_gate, 0)
        self.assertEqual(free.sp_cost, 0)
        self.assertIn("STACK_QINGTING_SWORD:3", free.produces)
        self.assertIn("STACK_QINGTING_SWORD:all_on_attack", free.consumes)
        resources = {item.key: item.maximum for item in mechanic.resources}
        self.assertEqual(resources["conducting"], 4)
        self.assertEqual(resources["qingting_sword"], 9)
        self.assertEqual(mechanic.state_seconds, 25)

    def test_yvonne_freeze_consumption_and_control_window_are_explicit(self):
        mechanic = load_character_mechanics()["伊冯"]
        battle = next(item for item in mechanic.transitions if item.action == "battle")
        ult = next(item for item in mechanic.transitions if item.action == "ult")
        self.assertIn("ATTACH_COLD|ATTACH_NATURAL", battle.requires)
        self.assertIn("spell_attach:all", battle.consumes)
        self.assertIn("STATUS_FROZEN", battle.produces)
        resources = {item.key: item.maximum for item in mechanic.resources}
        self.assertEqual(resources["spell_attach"], 4)
        self.assertEqual(mechanic.forced_main_control_seconds, 7)
        self.assertIn("STATUS_FROZEN:on_final_attack_if_present", ult.consumes)

    def test_native_entry_skill_resolution_keeps_mifu_and_typhoeus_in_rotation(self):
        store = load_skill_timings()
        mifu = store.profiles("弭弗", "battle")
        typhoeus = store.profiles("提弗洛斯", "battle")
        self.assertEqual(mifu[0].skill_id, "chr_0031_mifu_normalskill_1")
        self.assertEqual(mifu[0].sp_cost, 100)
        self.assertEqual(typhoeus[0].skill_id, "chr_0034_typhoea_normal_skill_floating_start")
        self.assertEqual(typhoeus[0].sp_cost, 100)

    def test_native_numbered_battle_phases_keep_each_mifu_cost(self):
        phases = load_skill_timings().battle_phase_profiles("弭弗")
        self.assertEqual([item.skill_id for item in phases], [
            "chr_0031_mifu_normalskill_1",
            "chr_0031_mifu_normalskill_2",
            "chr_0031_mifu_normalskill_3",
        ])
        self.assertEqual([item.sp_cost for item in phases], [100, 50, 50])

    def test_legacy_cycle_optimizer_is_blocked_for_complex_mechanics(self):
        blockers = mechanic_blockers(["弭弗", "佩丽卡", "提弗洛斯", "洛茜"])
        self.assertEqual({item.name for item in blockers}, {"弭弗", "提弗洛斯"})
        self.assertEqual(build_options(["弭弗"], load_skill_timings(), {}), ())

    def test_simple_character_still_builds_legacy_option(self):
        quote = DamageQuote(100, 100, 0, 0)
        options = build_options(["佩丽卡"], load_skill_timings(), {"佩丽卡": quote})
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0].slot, "1")


if __name__ == "__main__":
    unittest.main()
