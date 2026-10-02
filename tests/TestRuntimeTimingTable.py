import unittest

from src.data.runtime_timing import RUNTIME_TABLE, RuntimeTimingTable


class TestRuntimeTimingTable(unittest.TestCase):
    def test_compact_table_loads_and_resolves_team_by_slot(self):
        table = RuntimeTimingTable()
        self.assertLess(RUNTIME_TABLE.stat().st_size, 10 * 1024)
        team = table.team(["梨诺", "庄方宜", "?", "佩丽卡"])
        self.assertTrue(team[0])
        self.assertTrue(team[1])
        self.assertIsNone(team[2])
        self.assertTrue(team[3])

    def test_core_skill_values_match_current_snapshot(self):
        table = RuntimeTimingTable()
        liino = table.resolve("梨诺")[0]
        battle = liino["skills"]["battle"]
        self.assertEqual(battle.duration_frame, 2100)
        self.assertEqual(battle.exclusive_frame, 1821)
        self.assertEqual(battle.sp_cost, 25)

        pelica = table.resolve("佩丽卡")[0]
        self.assertEqual(pelica["skills"]["battle"].duration_frame, 155)
        self.assertEqual(pelica["skills"]["battle"].sp_cost, 100)


if __name__ == "__main__":
    unittest.main()
