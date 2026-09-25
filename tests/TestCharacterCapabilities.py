"""TestCharacterCapabilities — 角色能力注册表与队伍感知口径选择。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.data.character_capabilities import (
    CharacterCapabilities,
    clear_capabilities_cache,
    load_character_capabilities,
)
from src.data.skill_rotation import (
    clear_cache,
    load_damage_baseline_for_team,
)


def _caps(name: str, attach: tuple[str, ...] = (), combo: bool = False) -> CharacterCapabilities:
    return CharacterCapabilities(key=name, name=name, attach_elements=attach, combo_applier=combo)


class TestCapabilityRegistry(unittest.TestCase):
    """真实快照解析：施加者识别与消耗者排除。"""

    def setUp(self):
        clear_capabilities_cache()

    def tearDown(self):
        clear_capabilities_cache()

    def test_registry_covers_all_snapshots_keyed_by_chinese_name(self):
        caps = load_character_capabilities()
        self.assertGreaterEqual(len(caps), 32)
        self.assertIn("提弗洛斯", caps)
        self.assertIn("黎风", caps)
        self.assertEqual(caps["提弗洛斯"].key, "tifuluosi")

    def test_nature_appliers_detected(self):
        caps = load_character_capabilities()
        # 洁尔佩塔/黎智研/噗切娜/阿格琳娜系快照中可施加自然附着
        self.assertIn("自然", caps["洁尔佩塔"].attach_elements)
        self.assertIn("自然", caps["噗切娜"].attach_elements)

    def test_consumer_only_character_is_not_an_applier(self):
        # 提弗洛斯只「消耗自然附着」，全文件无施加动词——不得误标
        self.assertEqual(load_character_capabilities()["提弗洛斯"].attach_elements, ())

    def test_combo_applier_detected(self):
        # 黎风「获得连击」；消耗侧（「消耗了连击」）不触发
        self.assertTrue(load_character_capabilities()["黎风"].combo_applier)
        self.assertFalse(load_character_capabilities()["莱万汀"].combo_applier)

    def test_elemental_appliers(self):
        caps = load_character_capabilities()
        self.assertIn("电磁", caps["庄方宜"].attach_elements)
        self.assertIn("灼热", caps["莱万汀"].attach_elements)


class TestTeamAwareBaselineSelection(unittest.TestCase):
    """load_damage_baseline_for_team：满口径依赖回退与满连击口径。"""

    _ENTRIES = [
        {
            "character": "提弗洛斯",
            "cycle_expect": 129661.5,
            "cycle_expect_link4": 170308.0,
            "cycle_expect_conservative": 103074.8,
            "full_caliber_requires": {"attach": "自然"},
        },
        {"character": "莱万汀", "cycle_expect": 200.0, "cycle_expect_link4": 350.0},
        {"character": "黎风", "cycle_expect": 100.0, "cycle_expect_link4": 175.0},
        {"character": "弭弗", "cycle_expect": 500.0},
    ]

    _CAPS = {
        "提弗洛斯": _caps("提弗洛斯"),
        "洁尔佩塔": _caps("洁尔佩塔", attach=("自然",)),
        "黎风": _caps("黎风", combo=True),
        "莱万汀": _caps("莱万汀"),
    }

    def setUp(self):
        clear_cache()
        clear_capabilities_cache()
        self._tmp = tempfile.TemporaryDirectory()
        self._path = Path(self._tmp.name) / "damage_baseline.json"
        self._path.write_text(json.dumps(self._ENTRIES, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        clear_cache()
        clear_capabilities_cache()
        self._tmp.cleanup()

    def _load(self, team):
        return load_damage_baseline_for_team(team, path=self._path, capabilities=self._CAPS)

    def test_full_caliber_when_attachment_source_present(self):
        values = self._load(["提弗洛斯", "洁尔佩塔", "?", "?"])
        self.assertEqual(values["提弗洛斯"], 129661.5)

    def test_conservative_fallback_without_attachment_source(self):
        values = self._load(["提弗洛斯", "莱万汀", "?", "?"])
        self.assertEqual(values["提弗洛斯"], 103074.8)

    def test_self_does_not_count_as_attachment_source(self):
        # 提弗洛斯自己不施加自然附着，单体队伍必须回退
        values = self._load(["提弗洛斯", "?", "?", "?"])
        self.assertEqual(values["提弗洛斯"], 103074.8)

    def test_link4_caliber_when_combo_applier_present(self):
        values = self._load(["莱万汀", "黎风", "?", "?"])
        self.assertEqual(values["莱万汀"], 350.0)
        self.assertEqual(values["黎风"], 175.0)

    def test_link4_not_applied_without_combo_applier(self):
        values = self._load(["莱万汀", "提弗洛斯", "?", "?"])
        self.assertEqual(values["莱万汀"], 200.0)

    def test_conservative_takes_precedence_over_link4(self):
        # 回退保守口径时不叠加满连击口径（后者基于满口径计算）
        values = self._load(["提弗洛斯", "黎风", "?", "?"])
        self.assertEqual(values["提弗洛斯"], 103074.8)

    def test_characters_without_requirements_keep_full_caliber(self):
        values = self._load(["弭弗", "?", "?", "?"])
        self.assertEqual(values["弭弗"], 500.0)

    def test_result_cached_per_team(self):
        first = self._load(["莱万汀", "黎风", "?", "?"])
        second = self._load(["莱万汀", "黎风", "?", "?"])
        self.assertIs(first, second)
        clear_cache()
        third = self._load(["莱万汀", "黎风", "?", "?"])
        self.assertIsNot(first, third)
        self.assertEqual(third["莱万汀"], 350.0)


    def test_real_requirements_use_chinese_element_names(self):
        # full_caliber_requires.attach 必须用中文元素名（与 attach_elements 同口径），
        # 防止再引入英文键导致比对恒 False（回归防护）
        real = Path(__file__).resolve().parent.parent / "assets" / "data" / "damage_baseline.json"
        data = json.loads(real.read_text(encoding="utf-8"))
        checked = 0
        for entry in data:
            requirement = entry.get("full_caliber_requires") or {}
            if "attach" in requirement:
                self.assertIn(requirement["attach"], {"灼热", "寒冷", "电磁", "自然"})
                self.assertIn("cycle_expect_conservative", entry)
                checked += 1
        self.assertGreaterEqual(checked, 1, "至少应有提弗洛斯的满口径依赖条目")


if __name__ == "__main__":
    unittest.main()
