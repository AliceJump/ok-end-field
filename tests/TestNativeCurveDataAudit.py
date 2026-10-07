"""The corpus audit distinguishes malformed stored data from runtime completeness."""

import importlib.util
import json
import unittest
from pathlib import Path

from src.data.native_gameplay import native_record
from src.data.skill_timing import SkillTimingStore


class TestNativeCurveDataAudit(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "scripts/skill-data/audit_native_curve_data.py"
        spec = importlib.util.spec_from_file_location("native_curve_audit", path)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_real_record_round_trip_is_separate_from_execution(self):
        record = native_record(SkillTimingStore(), "chr_0002_endminm_combo_skill")
        report = self.module.audit({"sample": record})
        self.assertGreater(report["curve_nodes"], 0)
        self.assertEqual(report["curve_nodes"], report["decoded_nodes"])
        self.assertFalse(report["errors"])
        self.assertTrue(any(row["infinity_tangents"] for row in report["curves"]))
        self.assertIn("unbound", report["execution_unresolved"])
        json.dumps(report, allow_nan=False)

    def test_malformed_node_reports_error_and_does_not_count_as_decoded(self):
        record = {"data": {"$type": "Beyond.Gameplay.Core.CurveEvaluateFloat+Data", "$value": {
            "key": "test", "useCustomCurve": True, "curveTemplate": "Linear",
            "customCurve": {"profile": "curve_profile", "rawHex": "03", "bytes": 1}}}}
        report = self.module.audit({"bad": record})
        self.assertEqual((report["curve_nodes"], report["decoded_nodes"]), (1, 0))
        self.assertEqual(len(report["errors"]), 1)
        self.assertEqual(report["errors"][0]["source"], "bad")
