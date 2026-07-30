# -*- coding: utf-8 -*-
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QWidget

from src.gui.ConditionalRotationPanel import (
    _ConditionDisplayCard, _ConditionEditDialog, _ConditionEditor,
    _ActionListEditor, _fmt_cond, _fmt_actions, _fmt_action, _fmt_atom,
)
from src.core.rotation_ast import normalize_ast


class TestFormatters(unittest.TestCase):
    """友好名格式化函数测试。"""

    @patch("src.gui.ConditionalRotationPanel._tr", side_effect=lambda x: x)
    def test_fmt_action(self, _):
        self.assertEqual(_fmt_action("1"), "战技1")
        self.assertEqual(_fmt_action("e"), "连携技")
        self.assertEqual(_fmt_action("ult_2"), "终极技2")
        self.assertEqual(_fmt_action("sleep_5"), "等待5秒")
        self.assertEqual(_fmt_action("normal_3"), "普通3秒")

    @patch("src.gui.ConditionalRotationPanel._tr", side_effect=lambda x: x)
    def test_fmt_atom(self, _):
        self.assertEqual(_fmt_atom("ult1"), "终极技1可用")
        self.assertEqual(_fmt_atom("link"), "连携技可用")
        self.assertEqual(_fmt_atom("skill>=2"), "技力≥2")

    @patch("src.gui.ConditionalRotationPanel._tr", side_effect=lambda x: x)
    def test_fmt_cond_atom(self, _):
        typ, desc = _fmt_cond("link")
        self.assertEqual(typ, "满足条件")
        self.assertEqual(desc, "连携技可用")

    @patch("src.gui.ConditionalRotationPanel._tr", side_effect=lambda x: x)
    def test_fmt_cond_all_any(self, _):
        typ, desc = _fmt_cond({"all": ["ult1", "link"]})
        self.assertEqual(typ, "全部满足")
        self.assertEqual(desc, "终极技1可用;连携技可用")

        typ, desc = _fmt_cond({"any": ["skill>=2"]})
        self.assertEqual(typ, "任一满足")
        self.assertEqual(desc, "技力≥2")

    @patch("src.gui.ConditionalRotationPanel._tr", side_effect=lambda x: x)
    def test_fmt_actions(self, _):
        self.assertEqual(_fmt_actions(["1", "e", "ult_2"]), "战技1;连携技;终极技2")
        self.assertEqual(_fmt_actions([]), "无")
        self.assertEqual(_fmt_actions("notalist"), "无")


class TestConditionDisplayCard(unittest.TestCase):
    """只读显示卡片往返测试。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @patch("src.gui.ConditionalRotationPanel._tr", side_effect=lambda x: x)
    def test_card_holds_node(self, _):
        node = {"if": "link", "then": ["e"], "else": ["1"]}
        card = _ConditionDisplayCard(node)
        self.assertEqual(card._node, node)

    @patch("src.gui.ConditionalRotationPanel._tr", side_effect=lambda x: x)
    def test_card_update_node(self, _):
        card = _ConditionDisplayCard({"if": "link", "then": ["1"]})
        new_node = {"if": "ult2", "then": ["e", "2"], "else": ["3"]}
        card.update_node(new_node)
        self.assertEqual(card._node, new_node)


class TestConditionEditDialog(unittest.TestCase):
    """编辑弹窗往返测试：node → 弹窗 → to_node 应保持语义。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.parent = QWidget()
        cls.parent.resize(800, 600)
        cls.parent.show()

    @classmethod
    def tearDownClass(cls):
        cls.parent.close()

    @patch("src.gui.ConditionalRotationPanel._tr", side_effect=lambda x: x)
    def test_dialog_roundtrip_simple(self, _):
        node = {"if": "link", "then": ["e"]}
        dlg = _ConditionEditDialog(node, self.parent)
        result = dlg.to_node()
        self.assertEqual(result["if"], "link")
        self.assertEqual(result["then"], ["e"])
        self.assertNotIn("else", result)

    @patch("src.gui.ConditionalRotationPanel._tr", side_effect=lambda x: x)
    def test_dialog_roundtrip_with_else(self, _):
        node = {"if": {"all": ["ult1", "link"]}, "then": ["1", "2"], "else": ["3"]}
        dlg = _ConditionEditDialog(node, self.parent)
        result = dlg.to_node()
        self.assertEqual(result["if"], {"all": ["ult1", "link"]})
        self.assertEqual(result["then"], ["1", "2"])
        self.assertEqual(result["else"], ["3"])

    @patch("src.gui.ConditionalRotationPanel._tr", side_effect=lambda x: x)
    def test_dialog_skill_cond(self, _):
        node = {"if": "skill>=2", "then": ["1"]}
        dlg = _ConditionEditDialog(node, self.parent)
        self.assertEqual(dlg.to_node()["if"], "skill>=2")


class TestConditionEditor(unittest.TestCase):
    """条件编辑器往返测试。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @patch("src.gui.ConditionalRotationPanel._tr", side_effect=lambda x: x)
    def test_editor_atom(self, _):
        self.assertEqual(_ConditionEditor("link").to_cond(), "link")
        self.assertEqual(_ConditionEditor("ult2").to_cond(), "ult2")

    @patch("src.gui.ConditionalRotationPanel._tr", side_effect=lambda x: x)
    def test_editor_skill(self, _):
        self.assertEqual(_ConditionEditor("skill>=2").to_cond(), "skill>=2")

    @patch("src.gui.ConditionalRotationPanel._tr", side_effect=lambda x: x)
    def test_editor_all_any(self, _):
        self.assertEqual(
            _ConditionEditor({"all": ["ult1", "link"]}).to_cond(),
            {"all": ["ult1", "link"]},
        )
        self.assertEqual(
            _ConditionEditor({"any": ["skill>=2"]}).to_cond(),
            {"any": ["skill>=2"]},
        )


class TestActionListEditor(unittest.TestCase):
    """动作列表编辑器往返测试。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @patch("src.gui.ConditionalRotationPanel._tr", side_effect=lambda x: x)
    def test_editor_roundtrip(self, _):
        editor = _ActionListEditor("运行：")
        editor.load(["1", "e", "ult_2", "sleep_5"])
        self.assertEqual(editor.to_list(), ["1", "e", "ult_2", "sleep_5"])

    @patch("src.gui.ConditionalRotationPanel._tr", side_effect=lambda x: x)
    def test_editor_empty(self, _):
        editor = _ActionListEditor("运行：")
        editor.load([])
        self.assertEqual(editor.to_list(), [])


if __name__ == "__main__":
    unittest.main()
