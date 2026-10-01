import unittest

from src.data.skill_timing import SNAPSHOT, load_skill_timings
from src.data.timing_runtime_binary import RuntimeTimingBundle, build_runtime_payload, dumps_runtime_payload


class TestTimingRuntimeBinary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = load_skill_timings()
        cls.payload = build_runtime_payload(cls.store)
        cls.binary = dumps_runtime_payload(cls.payload)
        cls.bundle = RuntimeTimingBundle(data=cls.binary)

    def test_roundtrip_preserves_runtime_profiles(self):
        legacy = self.store.profiles("佩丽卡", "battle")
        packed = self.bundle.profiles("佩丽卡", "battle")

        self.assertEqual(len(packed), len(legacy))
        self.assertEqual(packed[0].skill_id, legacy[0].skill_id)
        self.assertEqual(packed[0].sp_cost, legacy[0].sp_cost)
        self.assertAlmostEqual(packed[0].effect_start, legacy[0].effect_start)
        self.assertEqual(packed[0].allow_next, legacy[0].allow_next)

    def test_partial_team_query_preserves_slots(self):
        result = self.bundle.team(["梨诺", "?", "诀"])

        self.assertEqual(len(result["slots"]), 3)
        self.assertEqual(result["slots"][0]["name"], "梨诺")
        self.assertIsNone(result["slots"][1]["character_id"])
        self.assertEqual(result["slots"][2]["name"], "诀")
        self.assertEqual(result["max_normal_attack_sp_gain"], 20)
        self.assertEqual(result["assume_success_sp_threshold"], 25)

    def test_state_data_is_precomputed(self):
        battle = self.bundle.battle_state("梨诺")
        ultimate = self.bundle.ultimate_state("梨诺")

        self.assertIsNotNone(battle)
        self.assertIsNotNone(ultimate)
        self.assertEqual(battle.end_skill_id, "chr_0035_liino_normal_skill_end")
        self.assertEqual(battle.end_cooldown, 3)

    def test_binary_is_much_smaller_than_current_runtime_sources(self):
        source_size = (SNAPSHOT / "index.json").stat().st_size + (SNAPSHOT / "records.json.gz").stat().st_size

        self.assertLess(len(self.binary), source_size // 5)
        self.assertLess(len(self.binary), 500_000)


if __name__ == "__main__":
    unittest.main()
