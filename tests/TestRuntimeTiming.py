import itertools
import json
import math
import unittest

from src.data.runtime_timing import (
    RUNTIME_TABLE,
    RuntimeTimingTable,
    total_combinations,
)
from src.data.skill_timing import load_skill_timings
from src.data.timing_dps import build_options, load_damage_quotes, optimize_cycle


class TestRuntimeTiming(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = RuntimeTimingTable()
        cls.legacy = load_skill_timings()

    def test_single_binary_is_small_and_contains_no_intermediate_strings(self):
        data = RUNTIME_TABLE.read_bytes()
        self.assertLess(len(data), 140 * 1024)
        for forbidden in (
            "梨诺".encode(),
            "佩丽卡".encode(),
            b"chr_",
            b"normal_skill",
            b"combo_skill",
            b"ultimate_skill",
            b"DamageAction",
        ):
            self.assertNotIn(forbidden, data)

    def test_fixed_id_mapping_and_cartesian_free_combination_count(self):
        self.assertEqual(self.runtime.character_count, 32)
        self.assertEqual(self.runtime.combo_count, 41_448)
        self.assertEqual(
            self.runtime.combo_count,
            sum(math.comb(self.runtime.character_count, size) for size in range(1, 5)),
        )
        self.assertEqual(self.runtime.combo_count, total_combinations(32))

    def test_profile_and_state_runtime_values_match_legacy_source(self):
        characters = json.loads(self.runtime.characters_path.read_text(encoding="utf-8"))
        for key in self.runtime.keys:
            name = characters[key]["zh"]
            self.assertEqual(
                self.runtime.normal_attack_sp_gain(name),
                self.legacy.normal_attack_sp_gain(name),
                name,
            )
            for kind in ("battle", "link", "ult"):
                old = self.legacy.profiles(name, kind)
                new = self.runtime.profiles(name, kind)
                self.assertEqual(len(new), len(old), f"{name}:{kind}")
                if not old:
                    continue
                self.assertAlmostEqual(new[0].duration, old[0].duration, places=5)
                self.assertAlmostEqual(new[0].exclusive, old[0].exclusive, places=5)
                self.assertAlmostEqual(new[0].cooldown, old[0].cooldown, places=2)
                self.assertEqual(new[0].skill_points, old[0].skill_points)
                self.assertEqual(new[0].sp_cost, old[0].sp_cost)
                if old[0].effect_start is None:
                    self.assertIsNone(new[0].effect_start)
                else:
                    self.assertAlmostEqual(new[0].effect_start, old[0].effect_start, places=5)
                self.assertAlmostEqual(new[0].handoff, old[0].handoff, places=5)
                self.assertAlmostEqual(new[0].actionable, old[0].actionable, places=5)

            for kind in ("battle", "ult"):
                old = self.legacy.state_skill(name, kind)
                new = self.runtime.state_skill(name, kind)
                self.assertEqual(new is None, old is None, f"{name}:{kind}:state")
                if old is not None:
                    self.assertAlmostEqual(new.duration, old.duration, places=5)
                    self.assertEqual(new.end_cooldown, old.end_cooldown)

    def test_unordered_lookup_is_order_independent(self):
        names = ["梨诺", "庄方宜", "诀", "佩丽卡"]
        expected = self.runtime.lookup_names(names)
        for permutation in itertools.permutations(names):
            self.assertEqual(self.runtime.lookup_names(permutation), expected)

    def test_dead_member_becomes_a_direct_smaller_set_lookup(self):
        names = ["梨诺", "庄方宜", "诀", "佩丽卡"]
        full = self.runtime.lookup_names(names)
        dead = self.runtime.lookup_names(["梨诺", "诀", "佩丽卡"])
        dead_ids = {
            self.runtime.id_for_name(name)
            for name in ("梨诺", "诀", "佩丽卡")
        }
        self.assertTrue(set(dead.battle_ids) <= dead_ids)
        self.assertTrue(set(dead.ult_ids) <= dead_ids)
        self.assertNotEqual(full.battle_ids, ())

    def test_representative_precomputed_axis_matches_live_optimizer(self):
        ids = tuple(sorted(
            self.runtime.id_for_name(name)
            for name in ("梨诺", "庄方宜", "诀", "佩丽卡")
        ))
        characters = json.loads(self.runtime.characters_path.read_text(encoding="utf-8"))
        team = [characters[self.runtime.keys[runtime_id]]["zh"] for runtime_id in ids]
        quotes = load_damage_quotes(team)
        plan = optimize_cycle(build_options(team, self.legacy, quotes))
        self.assertIsNotNone(plan)

        expected = tuple(ids[int(token) - 1] for token in plan.slots)
        actual = self.runtime.lookup_ids(ids)
        self.assertEqual(actual.battle_ids, expected)

        gains = [self.legacy.normal_attack_sp_gain(name) for name in team]
        known = [value for value in gains if value is not None]
        if any(value is None for value in gains):
            known.append(self.legacy.global_normal_attack_sp_gain())
        expected_threshold = max(known, default=20) + 5
        self.assertEqual(actual.assume_success_sp_threshold, expected_threshold)

    def test_every_precomputed_team_record_decodes(self):
        for size in range(1, 5):
            for ids in itertools.combinations(range(self.runtime.character_count), size):
                decision = self.runtime.lookup_ids(ids)
                allowed = set(ids)
                self.assertTrue(set(decision.battle_ids) <= allowed)
                self.assertTrue(set(decision.ult_ids) <= allowed)
                self.assertGreaterEqual(decision.assume_success_sp_threshold, 0)


if __name__ == "__main__":
    unittest.main()
