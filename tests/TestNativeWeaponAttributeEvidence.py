"""Weapon formula provenance and trigger boundaries, without enabling procs."""

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from tests.TestDamageDataFlow import ROOT, auditor


class TestNativeWeaponAttributeEvidence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = json.loads((ROOT / "assets/data/fixed_damage_baseline.json").read_text(encoding="utf-8"))
        cls.row = next(row for row in cls.rows if row["key"] == "lifeng")
        cls.character = auditor.get_character("lifeng", potential=0)
        cls.path = ROOT / "assets/data/equipment_mechanics/20261008/burden.json"
        cls.evidence = json.loads(cls.path.read_text(encoding="utf-8"))

    def test_rank_patch_overrides_stale_defaults_and_preserves_two_trigger_groups(self):
        candidates = auditor.weapon_attribute_evidence(self.character, self.row)
        self.assertEqual(len(candidates), 2)
        skill = self.evidence["records"]["sk_wpn_lance_0012"]["data"]
        defaults = {entry["key"]: entry["valueDouble"] for entry in skill["blackboard"]}
        self.assertEqual(defaults["duration"], 30)
        self.assertEqual(defaults["all_attr_up2"], .04)
        by_buff = {row["attribute_buff"]: row for row in candidates}
        self.assertEqual(set(by_buff), {"buff_wpn_lance_0012_attribute", "buff_wpn_lance_0012_attribute2"})
        markers = set()
        for candidate in candidates:
            self.assertEqual(candidate["event"], "OnBeforeOutputBuff")
            self.assertEqual(candidate["selected_rank"], 9)
            self.assertEqual(candidate["selected_parameters"]["duration"], 15)
            self.assertEqual(candidate["selected_parameters"]["all_attr_up"], .2240000069141388)
            self.assertEqual(candidate["selected_parameters"]["all_attr_up2"], .2240000069141388)
            self.assertEqual(candidate["selected_parameters"]["cd"], .1)
            self.assertEqual(candidate["origin_skill_condition"]["skillTypeList"], [2])
            self.assertTrue(candidate["create_buff"]["asChildBuff"])
            self.assertEqual(candidate["create_buff"]["targetSettings"]["targetSource"], 1)
            self.assertEqual(candidate["native_stacking"]["stackingType"], 4)
            self.assertTrue(candidate["native_stacking"]["useMaxStackCntKey"])
            self.assertNotIn("max_stack", candidate["selected_parameters"])
            modifiers = candidate["native_attribute_modifier"]
            self.assertFalse(modifiers["isConvertedAttribute"])
            self.assertEqual({entry["attributeType"] for entry in modifiers["attributeModifiers"]}, {39, 40, 41, 42})
            self.assertTrue(all(entry["formulaItem"] == 6 for entry in modifiers["attributeModifiers"]))
            markers.add(candidate["timed_marker"]["markerId"]["value"])
        self.assertEqual(markers, {"wpn_lance_0012", "wpn_lance_0012_2"})
        self.assertEqual(by_buff["buff_wpn_lance_0012_attribute"]["tag_paths"], ["Skill/Character/Common/NoGuard"])
        self.assertEqual(by_buff["buff_wpn_lance_0012_attribute2"]["tag_paths"],
                         ["Skill/Character/Common/Affixes/Vulnerable/VulnerablePhysic"])

    def test_per_skill_evidence_does_not_enable_four_stat_percentage_or_close_conversion(self):
        report = auditor.audit(self.rows)
        lifeng = next(row for row in report["characters"] if row["key"] == "lifeng")
        self.assertEqual(len(lifeng["dynamic_attribute_flow"]["native_weapon_attribute_candidates"]), 2)
        self.assertEqual(lifeng["dynamic_attribute_flow"]["native_attribute_change_producers"], "not_verified")
        self.assertEqual(lifeng["dynamic_attribute_flow"]["basis"]["unverified_attack_dependencies"], ["智识", "意志"])
        for skill in lifeng["skills"]:
            self.assertEqual(len(skill["native_weapon_attribute_checks"]), 2)
            self.assertTrue(all(item["status"] == "not_bound; evidence_candidate_only"
                                for item in skill["native_weapon_attribute_checks"]))
            self.assertTrue(skill["pending_semantic_checks"])
        self.assertEqual(report["summary"]["native_buff_attribute_damage_bindings"], 2)
        self.assertEqual(report["summary"]["model_release_bonus_bindings"], 13)
        self.assertEqual(report["summary"]["skills_pending_semantic_review"], 128)
        other = deepcopy(self.row)
        other["build"]["weapon"] = "扶摇"
        self.assertEqual(auditor.weapon_attribute_evidence(self.character, other), [])

    def test_tampered_evidence_and_changed_native_build_are_rejected(self):
        for mutate_manifest in (False, True):
            with self.subTest(manifest=mutate_manifest), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                folder = root / "assets/data/equipment_mechanics/20261008"
                folder.mkdir(parents=True)
                manifest = json.loads((self.path.parent / "index.json").read_text(encoding="utf-8"))
                payload = self.path.read_bytes()
                timing = root / "assets/data/skill_timings/20261002"
                timing.mkdir(parents=True)
                (timing / "index.json").write_text(json.dumps({"native_inputs": manifest["native_inputs"]}), encoding="utf-8")
                if mutate_manifest:
                    manifest["native_inputs"]["GameAssembly.dll"] = "wrong_build"
                else:
                    payload += b" "
                (folder / "burden.json").write_bytes(payload)
                (folder / "index.json").write_text(json.dumps(manifest), encoding="utf-8")
                with patch.object(auditor, "ROOT", root):
                    with self.assertRaisesRegex(ValueError, "build|hash mismatch"):
                        auditor.weapon_attribute_evidence(self.character, self.row)


if __name__ == "__main__":
    unittest.main()
