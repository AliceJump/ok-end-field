"""build_loadout_data 装备基础属性解析测试。"""

import importlib.util
import unittest
from pathlib import Path
from unittest import mock

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "skill-data" / "build_loadout_data.py"
_spec = importlib.util.spec_from_file_location("build_loadout_data", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


class TestParseEquip(unittest.TestCase):
    def test_lv70_flat_and_percentage_values(self):
        item = {"document": {"documentMap": {}}}
        table = [["生命值", "+1000"], ["物理伤害加成", "+5%"],
                 ["暴击率加成", "+12.5%"]]
        with mock.patch.object(mod, "_iter_widget_contents", return_value=[("基础属性", "base")]), \
             mock.patch.object(mod, "_document_tables", return_value=[table]):
            parsed = mod.parse_equip(item, "1", {})
        self.assertEqual(parsed["lv70_stats"], {"生命值": 1000,
                                                "物理伤害加成": "+5%",
                                                "暴击率加成": "+12.5%"})


if __name__ == "__main__":
    unittest.main()
