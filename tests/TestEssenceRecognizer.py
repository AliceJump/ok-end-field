# -*- coding: utf-8 -*-
import unittest

from src.essence.essence_recognizer import _attach_levels, parse_essence_panel


class _Box:
    def __init__(self, x: int, y: int, width: int, height: int, name: str, confidence: float = 1.0):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.name = name
        self.confidence = confidence


class TestEssenceRecognizer(unittest.TestCase):
    def test_parse_panel_always_three_entries(self):
        boxes = [
            _Box(100, 10, 300, 24, "无瑕基质：流转"),
            _Box(120, 55, 180, 20, "四号谷底"),
            _Box(120, 85, 180, 20, "附加技能"),
            _Box(120, 120, 260, 20, "智识提升"),
            _Box(120, 150, 260, 20, "o攻击提升"),
            _Box(120, 180, 260, 20, "流转"),
        ]

        panel = parse_essence_panel(boxes)
        self.assertIsNotNone(panel)
        assert panel is not None

        self.assertEqual(panel.entry_names, ("智识提升", "攻击提升", "流转"))
        self.assertEqual(len(panel.entry_boxes), 3)

    def test_attach_levels_keep_order(self):
        boxes = [
            _Box(100, 10, 300, 24, "无瑕基质：流转"),
            _Box(120, 85, 180, 20, "附加技能"),
            _Box(120, 120, 260, 20, "意志提升"),
            _Box(120, 150, 260, 20, "。攻击提升"),
            _Box(120, 180, 260, 20, "流转"),
        ]
        panel = parse_essence_panel(boxes)
        self.assertIsNotNone(panel)
        assert panel is not None

        level_boxes = [
            _Box(460, 120, 60, 18, "+1"),
            _Box(460, 150, 60, 18, "+2"),
            _Box(460, 180, 60, 18, "+3"),
        ]
        entries = _attach_levels(panel, level_boxes)

        self.assertEqual(len(entries), 3)
        self.assertEqual([e.name for e in entries], ["意志提升", "攻击提升", "流转"])
        self.assertEqual([e.level for e in entries], [1, 2, 3])

    def test_parse_panel_handles_spaces_and_symbols(self):
        boxes = [
            _Box(100, 10, 300, 24, "无 瑕 基质 · 流 转"),
            _Box(120, 55, 180, 20, "四 号 谷 底"),
            _Box(120, 85, 180, 20, "附 加 技 能"),
            _Box(120, 120, 260, 20, "意志 提升+1"),
            _Box(120, 150, 260, 20, "。攻击 提升+2"),
            _Box(120, 180, 260, 20, "流 转+1"),
        ]

        panel = parse_essence_panel(boxes)
        self.assertIsNotNone(panel)
        assert panel is not None

        self.assertEqual(panel.name, "无瑕基质：流转")
        self.assertEqual(panel.source, "四号谷底")
        self.assertEqual(panel.entry_names, ("意志提升", "攻击提升", "流转"))
        self.assertEqual(len(panel.entry_boxes), 3)


if __name__ == "__main__":
    unittest.main()
