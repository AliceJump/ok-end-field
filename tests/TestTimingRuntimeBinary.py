import unittest

from src.data.skill_timing import SNAPSHOT, load_skill_timings
from src.data.timing_runtime_binary import (
    DEFAULT_RUNTIME_BUNDLE,
    RuntimeTimingBundle,
    build_runtime_payload,
    dumps_runtime_payload,
)


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

    def test_unknown_finisher_uses_global_upper_bound_for_team_threshold(self):
        unknown = next(
            (
                row["n"]
                for row in self.bundle.payload["c"].values()
                if row["g"] is None and row["p"].get("battle")
            ),
            None,
        )
        self.assertIsNotNone(unknown)
        result = self.bundle.team([unknown])
        self.assertEqual(
            result["max_normal_attack_sp_gain"],
            self.bundle.global_normal_attack_sp_gain(),
        )
        self.assertEqual(
            result["assume_success_sp_threshold"],
            self.bundle.global_normal_attack_sp_gain() + 5,
        )

    def test_state_data_is_precomputed(self):
        battle = self.bundle.battle_state("梨诺")
        ultimate = self.bundle.ultimate_state("梨诺")

        self.assertIsNotNone(battle)
        self.assertIsNotNone(ultimate)
        self.assertEqual(battle.end_skill_id, "chr_0035_liino_normal_skill_end")
        self.assertEqual(battle.end_cooldown, 3)

    def test_all_runtime_values_match_source_store(self):
        self.assertEqual(
            self.bundle.global_normal_attack_sp_gain(),
            self.store.global_normal_attack_sp_gain(),
        )
        for cid in self.store.index["characters"]:
            self.assertEqual(
                self.bundle.normal_attack_sp_gain(cid),
                self.store.normal_attack_sp_gain(cid),
                cid,
            )
            for kind in ("battle", "link", "ult"):
                self.assertEqual(
                    self.bundle.profiles(cid, kind),
                    self.store.profiles(cid, kind),
                    f"{cid}:{kind}",
                )
            self.assertEqual(
                self.bundle.battle_state(cid),
                self.store.battle_state(cid),
                f"{cid}:battle_state",
            )
            self.assertEqual(
                self.bundle.ultimate_state(cid),
                self.store.ultimate_state(cid),
                f"{cid}:ult_state",
            )

    def test_committed_bundle_is_exact_current_export(self):
        committed = DEFAULT_RUNTIME_BUNDLE.read_bytes()
        self.assertEqual(committed, self.binary)

    def test_binary_is_much_smaller_than_current_runtime_sources(self):
        source_size = (SNAPSHOT / "index.json").stat().st_size + (SNAPSHOT / "records.json.gz").stat().st_size

        self.assertLess(len(self.binary), source_size // 5)
        self.assertLess(len(self.binary), 500_000)


if __name__ == "__main__":
    unittest.main()
