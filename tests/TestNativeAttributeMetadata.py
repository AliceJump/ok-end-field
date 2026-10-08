"""Original attribute bytes, active bounds and raw/final domain separation."""

import hashlib
import json
import shutil
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from src.data.native_attribute_metadata import ROOT, SNAPSHOT, metadata_audit, read_metadata_snapshot
from tests.TestDamageDataFlow import auditor


class TestNativeAttributeMetadata(unittest.TestCase):
    def test_four_stat_bounds_and_inactive_bounds_on_scalar_defaults(self):
        rows = read_metadata_snapshot()
        for key in (39, 40, 41, 42):
            data = rows[str(key)]["data"]
            self.assertEqual((data["defaultValue"], data["minValue"], data["maxValue"]), (0, 0, 100000))
            self.assertTrue(data["hasMinValue"] and data["hasMaxValue"])
        for key in (57, 58, 76, 77, 78, 79):
            data = rows[str(key)]["data"]
            self.assertEqual(data["defaultValue"], 0)
            self.assertFalse(data["hasMinValue"] or data["hasMaxValue"])

    def test_changed_build_raw_bytes_and_rehashed_wrong_proofs_are_rejected(self):
        for problem in ("build", "raw", "offset", "type", "value", "domain"):
            with self.subTest(problem=problem), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / "snapshot"
                shutil.copytree(SNAPSHOT, source)
                timing = root / "assets/data/skill_timings/20261002"
                timing.mkdir(parents=True)
                shutil.copy(ROOT / "assets/data/skill_timings/20261002/index.json", timing)
                manifest = json.loads((source / "index.json").read_text(encoding="utf-8"))
                data = json.loads((source / "metadata.json").read_text(encoding="utf-8"))
                row = data["attributes"]["57"]
                if problem == "build":
                    manifest["native_inputs"]["GameAssembly.dll"] = "other_build"
                elif problem == "raw":
                    manifest["source_sha256"] = "other_source"
                elif problem == "offset":
                    # A genuine zero double in a different field is not a default proof.
                    row["byte_proof"]["defaultValue"] = deepcopy(row["byte_proof"]["minValue"])
                elif problem == "type":
                    row["data"]["hasMinValue"] = 0
                elif problem == "value":
                    row["data"]["defaultValue"] = 1.0
                    row["byte_proof"]["defaultValue"]["value"] = 1.0
                else:
                    data["domain"] = "final_nonconverted"
                payload = json.dumps(data).encode()
                (source / "metadata.json").write_bytes(payload)
                manifest["metadata_sha256"] = hashlib.sha256(payload).hexdigest()
                (source / "index.json").write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "mismatch"):
                    read_metadata_snapshot(source, root=root)

    def test_audit_rejects_out_of_range_fixed_totals_and_never_initializes_final_inputs(self):
        rows = json.loads((ROOT / "assets/data/fixed_damage_baseline.json").read_text(encoding="utf-8"))
        for row in rows:
            result = metadata_audit(row["attribute_basis"])
            self.assertEqual(result["fixed_four_stat_bounds"], "verified")
            self.assertIn("not_bound", result["armed_final_producer_status"])
            self.assertIsNone(result["attributes"]["57"]["minimum"])
            self.assertIsNone(result["attributes"]["57"]["maximum"])
        for invalid in (-1, 100001, float("nan")):
            basis = deepcopy(rows[0]["attribute_basis"])
            basis["totals"]["力量"] = invalid
            with self.assertRaisesRegex(ValueError, "outside native bounds"):
                metadata_audit(basis)
        before = deepcopy(rows)
        report = auditor.audit(rows)
        self.assertEqual(rows, before)
        self.assertEqual(len(report["characters"]), 32)
        self.assertEqual(report["summary"]["native_buff_attribute_damage_bindings"], 2)
        self.assertEqual(report["summary"]["model_release_bonus_bindings"], 14)
        self.assertEqual(report["summary"]["skills_pending_semantic_review"], 128)
        for character in report["characters"]:
            self.assertEqual(character["dynamic_attribute_flow"]["native_raw_metadata"]["attributes"]["57"]["raw_default"], 0)
            self.assertEqual(character["dynamic_attribute_flow"]["native_attribute_change_producers"], "not_verified")
