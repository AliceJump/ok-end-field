"""The read-only audit reports program and world gaps separately."""

import importlib.util
import unittest
from pathlib import Path


class TestMechanismCoverageAudit(unittest.TestCase):
    def test_single_character_report_schema(self):
        path = Path(__file__).resolve().parents[1] / "scripts/skill-data/audit_mechanism_coverage.py"
        spec = importlib.util.spec_from_file_location("coverage_audit", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        report = module.audit(("卡缪",), with_benchmark=False)
        self.assertGreater(report["total_programs"], 0)
        programs = report["characters"][0]["programs"]
        self.assertEqual(report["total_programs"], len(programs))
        self.assertEqual(report["complete_programs"], sum(not p["unresolved"] for p in programs))
        self.assertTrue(all({"key", "kind", "unresolved"} <= p.keys() for p in programs))
        self.assertIn("world_unresolved", report["characters"][0])
        self.assertTrue(all({"type", "affected_programs"} <= row.keys() for row in report["blockers"]))
        self.assertIsNone(report["benchmark"])
