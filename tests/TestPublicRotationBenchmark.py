import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/skill-data/benchmark_public_rotation.py"
_SPEC = importlib.util.spec_from_file_location("benchmark_public_rotation", _SCRIPT)
benchmark = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(benchmark)


class TestBenchmarkReportPaths(unittest.TestCase):
    def test_missing_options_report_unavailable_plan(self):
        with patch.object(benchmark, "build_options", return_value=()):
            with self.assertRaisesRegex(ValueError, "No periodic plan"):
                benchmark.benchmark(seconds=1)

    def test_unstable_baseline_is_reported(self):
        with patch.object(benchmark, "evaluate_cycle", return_value=None):
            with self.assertRaisesRegex(ValueError, "baseline periodic plan"):
                benchmark.benchmark(seconds=1)

    def test_replay_data_paths_are_independent_of_current_directory(self):
        import os
        import tempfile

        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                self.assertTrue(benchmark.benchmark(seconds=1)["laevatain_battle_quote"])
            finally:
                os.chdir(previous)

    def test_offline_replay_runs_with_precise_sp_and_simulated_time(self):
        result = benchmark.benchmark(seconds=2)
        casts = result["runtime_replay"]["first_12_casts"]
        self.assertGreaterEqual(len(casts), 2)
        self.assertGreater(casts[1]["seconds"], casts[0]["seconds"])
        self.assertIsNotNone(result["ultimate_guard_replay"]["first_normal_attack_input_seconds"])

    def test_json_report_under_scratch_directory_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / benchmark.REPORT_FILE
            with patch.object(benchmark, "ROOT", root), \
                 patch.object(benchmark, "benchmark", return_value={"scope": "offline"}):
                self.assertEqual(benchmark.main(["--output", benchmark.REPORT_FILE]), 0)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"scope": "offline"})

    def test_output_flag_uses_the_fixed_report_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(benchmark, "ROOT", root), \
                 patch.object(benchmark, "benchmark", return_value={}):
                self.assertEqual(benchmark.main(["--output"]), 0)
            self.assertTrue((root / benchmark.REPORT_FILE).is_file())

    def assert_invalid_output(self, path):
        with patch.object(benchmark, "benchmark") as calculate:
            with self.assertRaises(SystemExit) as error:
                benchmark.main(["--output", str(path)])
            self.assertEqual(error.exception.code, 2)
            calculate.assert_not_called()

    def test_parent_traversal_outside_scratch_directory_is_rejected(self):
        self.assert_invalid_output(benchmark.ROOT / "tmp" / ".." / "report.json")

    def test_absolute_path_outside_repository_is_rejected(self):
        self.assert_invalid_output(benchmark.ROOT.parent / "outside.json")

    def test_sibling_directory_with_same_prefix_is_rejected(self):
        self.assert_invalid_output(benchmark.ROOT / "tmp_evil" / "report.json")

    def test_non_json_file_is_rejected(self):
        self.assert_invalid_output(benchmark.ROOT / "tmp" / "script.py")
