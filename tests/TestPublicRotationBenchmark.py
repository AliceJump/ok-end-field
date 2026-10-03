import argparse
import importlib.util
import unittest
from pathlib import Path


_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/skill-data/benchmark_public_rotation.py"
_SPEC = importlib.util.spec_from_file_location("benchmark_public_rotation", _SCRIPT)
benchmark = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(benchmark)


class TestBenchmarkReportPaths(unittest.TestCase):
    def test_offline_replay_runs_with_precise_sp_and_simulated_time(self):
        result = benchmark.benchmark(seconds=2)
        casts = result["runtime_replay"]["first_12_casts"]
        self.assertGreaterEqual(len(casts), 2)
        self.assertGreater(casts[1]["seconds"], casts[0]["seconds"])
        self.assertIsNotNone(result["ultimate_guard_replay"]["first_normal_attack_input_seconds"])

    def test_json_report_under_scratch_directory_is_accepted(self):
        path = benchmark.ROOT / "tmp" / "rotation-benchmark" / "report.json"
        self.assertEqual(benchmark.resolve_output_path(path), path.resolve())

    def test_parent_traversal_outside_scratch_directory_is_rejected(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            benchmark.resolve_output_path(benchmark.ROOT / "tmp" / ".." / "report.json")

    def test_absolute_path_outside_repository_is_rejected(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            benchmark.resolve_output_path(benchmark.ROOT.parent / "outside.json")

    def test_non_json_file_is_rejected(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            benchmark.resolve_output_path(benchmark.ROOT / "tmp" / "script.py")
