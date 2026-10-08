"""Actual marker producers must not become cast-zero or assumed-hit bonuses."""

import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from src.data.character_skills import get_character
from src.data.native_gameplay import native_enums
from src.data.native_zhuangfy_evidence import FAMILY, ROOT, read_snapshot, release_evidence
from tests.TestDamageDataFlow import auditor


class TestNativeZhuangfyReleaseEvidence(unittest.TestCase):
    def test_actual_attribute_root_and_buff_enhance_count_are_not_marker_count(self):
        candidate = release_evidence(get_character("zhuang_fangyi"))[0]
        raw = read_snapshot()
        root = raw[candidate["keyword_template"]]["data"]
        child = raw[candidate["selected_keyword_child"]]["data"]
        attribute = native_enums()["Beyond.GEnums.AttributeType"]["PulseEnhancedDmgIncrease"]["value"]
        modifier = root["attributeModifier"]["attributeModifiers"][0]
        self.assertEqual((modifier["attributeType"], modifier["formulaItem"]), (attribute, 5))
        self.assertEqual(modifier["param"]["blackboardKey"], "rate")
        self.assertFalse(root["attributeModifier"]["isConvertedAttribute"])
        self.assertTrue(next(row for row in root["blackboard"] if row["key"] == "rate")["isDynamic"])
        self.assertFalse(child["attributeModifier"]["attributeModifiers"])
        proof = candidate["keyword_attribute_refresh_evidence"]
        self.assertEqual(proof["template_stacking"], root["stackingSettings"])
        self.assertTrue(proof["template_stacking"]["usePriorityKey"])
        self.assertEqual(proof["template_stacking"]["priorityKey"], "rate")
        self.assertIn("m_enhanceCnt", proof["parameter_domain"])
        self.assertEqual(proof["status"], "evidence_only; attribute_producer_not_bound")
        self.assertTrue(any("not mark count" in item for item in proof["unknown"]))
        self.assertEqual(proof["enhance_count_initialization"]["phase"], "Buff.Reset")
        self.assertEqual(proof["enhance_count_initialization"]["value"], 1)
        self.assertEqual(proof["enhance_count_update"]["order"][:2], ["increment count", "execute buff event 6"])
        # This is the native Reset value, not a supplied final damage input or
        # an assertion that the selected active producer has already run.
        self.assertEqual(candidate["execution_status"], "not_bound; evidence_candidate_only")

    def test_selected_max_talent_patch_and_marker_receiving_event(self):
        candidate = release_evidence(get_character("zhuang_fangyi"))[0]
        self.assertEqual(candidate["selected_parameters"], {"base_rate": .18000000715255737,
                                                          "duration": 5.0, "enhance_rate": .019999999552965164})
        self.assertTrue(all(row["valueDouble"] == 0 for row in candidate["raw_skill_blackboard"]))
        self.assertEqual(candidate["receiving_event"], 9)
        self.assertEqual(candidate["listener_buff_condition"]["buffIdList"],
                         [{"buffId": "buff_chr_0030_zhuangfy_talent1"}])
        self.assertEqual(candidate["base_duration"]["blackboardKey"], "duration")
        # Recasting replaces the previous base instance; it is not another
        # independent five-second layer or plain Refresh preserving old rate.
        self.assertEqual(candidate["base_stacking"]["stackingType"], 2)
        self.assertEqual(candidate["base_stacking"]["maxStackCnt"], 1)
        self.assertFalse(candidate["base_stacking"]["useMaxStackCntKey"])
        self.assertEqual(candidate["enhanced_callback_event"], 5)
        enhanced = candidate["enhanced_action"]
        self.assertEqual(enhanced["enhancingList"][0]["buffIds"], ["buff_chr_0030_zhuangfy_talent1_mark"])
        self.assertTrue(enhanced["asChildBuff"])
        self.assertFalse(enhanced["overrideChildBuffId"])
        self.assertEqual(candidate["execution_status"], "not_bound; evidence_candidate_only")
        binding = candidate["marker_notification_binding"]
        self.assertEqual(binding["events"], ["OnAddedBuff", "OnOutputBuff"])
        self.assertEqual(binding["full_producer"], "not_bound")
        self.assertEqual(binding["producers"]["buff_chr_0030_zhuangfy_talent1_mark"],
                         ["chr_0030_zhuangfy_normal_skill_ult_abilityrange"])

    def test_base_frames_target_gate_and_area_mark_count_stay_distinct(self):
        candidate = release_evidence(get_character("zhuang_fangyi"))[0]
        producers = candidate["marker_producers"]
        base = [row for row in producers if not row["skill"].endswith("abilityrange")]
        self.assertEqual({row["skill"]: row["start_frame"] for row in base},
                         {"chr_0030_zhuangfy_normal_skill": 6, "chr_0030_zhuangfy_normal_skill_ult": 5})
        for row in base:
            self.assertEqual(len(row["creates"]), 2)  # Alternative guarded paths, not two activations.
            gate = [action["data"] for action in row["control_actions"]
                    if action["type"].endswith("CheckEntityNum+Data")][0]
            self.assertEqual(gate["checkTarget"]["targetGroupKey"], "smart_target")
            self.assertEqual((gate["compareType"], gate["minNum"]), (3, 1))
            self.assertTrue(all(create["action"]["targetSettings"]["targetSource"] == 1 for create in row["creates"]))
        area = [row for row in producers if row not in base]
        self.assertEqual([(row["start_frame"], row["end_frame"]) for row in area], [(12, 64), (69, 70)])
        self.assertEqual(len(area[0]["tick_actions"]), 1)
        compare = [item["data"] for item in area[0]["control_actions"] if item["type"].endswith("CompareFloat+Data")]
        self.assertTrue(any(item["valueA"]["blackboardKey"] == "tick_index"
                            and item["valueB"]["blackboardKey"] == "EntityBB_SwordNum" for item in compare))
        self.assertTrue(any(item["type"].endswith("JumpToAction+Data") for item in area[0]["control_actions"]))
        for row in area:
            self.assertTrue(all(create["action"]["buffs"][0]["buffId"] == "buff_chr_0030_zhuangfy_talent1_mark"
                                for create in row["creates"]))
        report = auditor.audit()
        character = next(row for row in report["characters"] if row["key"] == "zhuang_fangyi")
        self.assertEqual(sum(unit["native_marker_release_checks"][0]["this_unit_is_producer_kind"]
                             for unit in character["skills"]), 1)
        self.assertEqual(report["summary"]["model_release_bonus_bindings"], 14)
        self.assertEqual(report["summary"]["native_buff_attribute_damage_bindings"], 2)
        self.assertEqual(report["summary"]["skills_pending_semantic_review"], 128)
        talent = next(row for row in character["passives"] if row["effect_id"] == candidate["passive_id"])
        self.assertEqual(talent["native_marker_release_evidence"], [candidate["passive_id"]])
        # Keeping the known base must not silently supply zero for the unknown
        # actual addition count in the existing full magnitude contract.
        self.assertIn("source.zhuangfy_talent1_marks", talent["damage_rules"][0]["required_inputs"])

    def test_source_hash_build_and_byte_verification_fail_closed(self):
        for change in ("hash", "build", "byte_proof", "self_consistent_new_build"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                folder = root / FAMILY
                folder.mkdir(parents=True)
                source = ROOT / FAMILY
                manifest = json.loads((source / "index.json").read_text(encoding="utf-8"))
                raw = (source / "zhuangfy_release.json.gz").read_bytes()
                if change == "hash":
                    raw += b" "
                elif change == "build":
                    manifest["native_inputs"]["GameAssembly.dll"] = "another_build"
                else:
                    data = json.loads(gzip.decompress(raw))
                    if change == "self_consistent_new_build":
                        manifest["native_inputs"] = {**manifest["native_inputs"], "GameAssembly.dll": "another_build"}
                        data["native_inputs"] = manifest["native_inputs"]
                    else:
                        next(iter(data["records"].values()))["source"]["byte_identical"] = False
                    raw = gzip.compress(json.dumps(data).encode(), mtime=0)
                    manifest["files"]["zhuangfy_release.json.gz"] = hashlib.sha256(raw).hexdigest()
                (folder / "index.json").write_text(json.dumps(manifest), encoding="utf-8")
                (folder / "zhuangfy_release.json.gz").write_bytes(raw)
                timing = root / "assets/data/skill_timings/20261002"
                timing.mkdir(parents=True)
                (timing / "index.json").write_bytes((ROOT / "assets/data/skill_timings/20261002/index.json").read_bytes())
                if change == "self_consistent_new_build":
                    timing_data = json.loads((timing / "index.json").read_text(encoding="utf8"))
                    timing_data["native_inputs"] = manifest["native_inputs"]
                    (timing / "index.json").write_text(json.dumps(timing_data), encoding="utf8")
                with self.assertRaisesRegex(ValueError, "hash|build|verification"):
                    read_snapshot(root=root)
