"""Selected set membership and next-cast consume/cleanup evidence boundaries."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.TestDamageDataFlow import ROOT, auditor


class TestNativeNextSkillSetEvidence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = json.loads((ROOT / "assets/data/fixed_damage_baseline.json").read_text(encoding="utf-8"))
        cls.by_key = {row["key"]: row for row in cls.rows}
        cls.path = ROOT / "assets/data/equipment_mechanics/20261008/next_skill_sets.json"
        cls.data = json.loads(cls.path.read_text(encoding="utf-8"))

    def test_actual_pieces_activate_three_profiles_and_not_single_off_pieces(self):
        activated = set()
        for row in self.rows:
            for candidate in auditor.next_skill_set_evidence(row):
                if candidate["activated_by_selected_build"]:
                    activated.add(row["key"])
                else:
                    self.assertIn(row["key"], {"ardelia", "last_rite", "snowshine"})
                    self.assertEqual(len(candidate["selected_piece_ids"]), 1)
                self.assertEqual(candidate["required_pieces"], 3)
        self.assertEqual(activated, {"avywenna", "da_pan", "fluorite"})
        # Two copies in distinct equipment slots both count toward the set.
        candidate = auditor.next_skill_set_evidence(self.by_key["da_pan"])[0]
        self.assertEqual(len(candidate["selected_piece_ids"]), 3)
        self.assertEqual(candidate["selected_piece_ids"][1], candidate["selected_piece_ids"][2])

    def test_rank_patch_stack_snapshot_consume_order_and_cast_identity_not_generic_timer(self):
        for key, maximum, amount, mask in (("avywenna", 2, .30000001192092896, 256),
                                           ("da_pan", 3, .20000000298023224, 8192)):
            candidate = auditor.next_skill_set_evidence(self.by_key[key])[0]
            self.assertEqual(candidate["selected_parameters"], {"atk_up": .15000000596046448,
                                                                "dmg_up": amount, "max_stack": maximum})
            skill = self.data["records"][candidate["producer_skill"]]["data"]
            raw = {r["key"]: r["valueDouble"] for r in skill["blackboard"]}
            self.assertEqual(raw["max_stack"], 0)
            self.assertNotEqual(raw["atk_up"], candidate["selected_parameters"]["atk_up"])
            self.assertEqual(candidate["consumer_actions"], ["CheckSkillType+Data", "SaveBuffStackNumAdvanced+Data",
                             "ModifyDynamicBlackboard+Data", "CreateBuffAction+Data", "FinishBuffAdvanced+Data"])
            pending = self.data["records"][candidate["pending_stack_buff"]]["data"]
            actions = pending["abilityEventAction"][0]["actions"][0]["actionData"]
            self.assertEqual(actions[2]["$value"]["operation"], 2)
            self.assertTrue(actions[4]["$value"]["finishAll"])
            self.assertFalse(actions[3]["$value"]["asChildBuff"])
            self.assertTrue(actions[3]["$value"]["inheritSourceSkillCastInfo"])
            damage = self.data["records"][candidate["cast_damage_buff"]]["data"]
            self.assertEqual(damage["lifeType"], 1)
            self.assertEqual(damage["buffEventAction"][0]["actions"][0]["actionData"][0]["$type"],
                             "Beyond.Gameplay.Core.SkillAffixAction+Data")
            conditions = candidate["damage_conditions"]["actionData"]
            self.assertEqual(conditions[0]["$type"], "Beyond.Gameplay.Core.Conditions.CheckSkillCastId+Data")
            self.assertEqual(conditions[1]["$value"]["mask"], mask)
            self.assertEqual(candidate["execution_status"], "not_bound; evidence_candidate_only")
        report = auditor.audit(self.rows)
        for character in report["characters"]:
            checks = character["native_next_skill_set_candidates"]
            for skill in character["skills"]:
                self.assertEqual(len(skill["native_next_skill_set_checks"]), len(checks))
                self.assertTrue(skill["pending_semantic_checks"])
        self.assertEqual(report["summary"]["model_release_bonus_bindings"], 14)
        self.assertEqual(report["summary"]["native_buff_attribute_damage_bindings"], 2)
        self.assertEqual(report["summary"]["skills_pending_semantic_review"], 128)

    def test_tampered_payload_and_other_native_build_rejected(self):
        for changed_build in (False, True):
            with self.subTest(build=changed_build), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                folder = root / "assets/data/equipment_mechanics/20261008"
                folder.mkdir(parents=True)
                manifest = json.loads((self.path.parent / "index.json").read_text(encoding="utf-8"))
                payload = self.path.read_bytes()
                timing = root / "assets/data/skill_timings/20261002"
                timing.mkdir(parents=True)
                (timing / "index.json").write_text(json.dumps({"native_inputs": manifest["native_inputs"]}), encoding="utf-8")
                if changed_build:
                    manifest["native_inputs"]["GameAssembly.dll"] = "other_build"
                else:
                    payload += b" "
                (folder / "index.json").write_text(json.dumps(manifest), encoding="utf-8")
                (folder / "next_skill_sets.json").write_bytes(payload)
                with patch.object(auditor, "ROOT", root):
                    with self.assertRaisesRegex(ValueError, "hash|build"):
                        auditor.next_skill_set_evidence(self.by_key["avywenna"])
