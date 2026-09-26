"""analyze_pulse_probe_log 聚合逻辑测试。"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "maintenance" / "analyze_pulse_probe_log.py"
_spec = importlib.util.spec_from_file_location("analyze_pulse_probe_log", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _entry(active_t, char=None, slot=1, ratio=0.9, team=None):
    return {
        "time": "2026-09-26T12:00:00",
        "active_t": active_t,
        "label": f"batch{slot}",
        "slot": slot,
        "key": str(slot),
        "char": char,
        "team": team,
        "ratio": ratio,
        "member_count": 4,
        "task": "BattleTask",
    }


class TestLoadEntries(unittest.TestCase):
    def test_missing_file_returns_empty(self):
        self.assertEqual(mod.load_entries(Path("Z:/no/such/file.jsonl")), [])

    def test_malformed_lines_skipped(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            f.write(json.dumps(_entry(1.0), ensure_ascii=False) + "\n")
            f.write("not json\n")
            f.write("\n")
            path = Path(f.name)
        try:
            entries = mod.load_entries(path)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["active_t"], 1.0)
        finally:
            path.unlink()


class TestSplitBattles(unittest.TestCase):
    def test_active_t_reset_starts_new_battle(self):
        entries = [_entry(5.0), _entry(7.5), _entry(1.0), _entry(3.0)]
        battles = mod.split_battles(entries)
        self.assertEqual(len(battles), 2)
        self.assertEqual([e["active_t"] for e in battles[0]], [5.0, 7.5])
        self.assertEqual([e["active_t"] for e in battles[1]], [1.0, 3.0])

    def test_empty_input_no_battles(self):
        self.assertEqual(mod.split_battles([]), [])


class TestAggregate(unittest.TestCase):
    def test_identity_first_seen_and_intervals(self):
        battles = [[
            _entry(2.0, char="洛茜", slot=1),
            _entry(4.0, char="洛茜", slot=1),
            _entry(6.0, char="洛茜", slot=1),
            _entry(8.0, slot=3),
        ]]
        stats = mod.aggregate(battles)
        self.assertEqual(stats["洛茜"]["pulses"], 3)
        self.assertEqual(stats["洛茜"]["battles"], 1)
        self.assertEqual(stats["洛茜"]["first_seen"], [2.0])
        self.assertEqual(stats["洛茜"]["intervals"], [2.0, 2.0])
        # 无角色映射时退回槽位号
        self.assertIn("槽位3(未识别)", stats)

    def test_first_seen_counts_once_per_battle(self):
        battles = [[_entry(2.0, char="洛茜")], [_entry(9.0, char="洛茜")]]
        stats = mod.aggregate(battles)
        self.assertEqual(stats["洛茜"]["battles"], 2)
        self.assertEqual(stats["洛茜"]["first_seen"], [2.0, 9.0])

    def test_teams_collected(self):
        battles = [[_entry(1.0, char="洛茜", team=["洛茜", "黎风", "汤汤", "管理员"])]]
        stats = mod.aggregate(battles)
        self.assertEqual(stats["洛茜"]["teams"], ["洛茜/黎风/汤汤/管理员"])


class TestMain(unittest.TestCase):
    def _write_log(self, records):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            return Path(f.name)

    def test_end_to_end_with_json_output(self):
        log = self._write_log([
            _entry(2.0, char="洛茜", team=["洛茜", "黎风"]),
            _entry(4.0, char="洛茜", team=["洛茜", "黎风"]),
            _entry(1.0, char="黎风", team=["洛茜", "黎风"]),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "agg.json"
            code = mod.main(["--log", str(log), "--json", str(out)])
            self.assertEqual(code, 0)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(payload["battles"], 2)
            self.assertIn("洛茜", payload["identities"])
        log.unlink()

    def test_char_filter_and_empty_result(self):
        log = self._write_log([_entry(2.0, char="洛茜")])
        try:
            self.assertEqual(mod.main(["--log", str(log), "--char", "洛茜"]), 0)
            self.assertEqual(mod.main(["--log", str(log), "--char", "不存在"]), 1)
        finally:
            log.unlink()


if __name__ == "__main__":
    unittest.main()
