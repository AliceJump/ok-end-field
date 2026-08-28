import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QWidget

from src.gui.ConditionalRotationPanel import _ConditionEditDialog


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
    def test_dialog_drops_else(self, _):
        # else 已从 GUI 移除，含 else 的 node 编辑后 else 被丢弃
        node = {"if": {"all": ["ult1", "link"]}, "then": ["1", "2"], "else": ["3"]}
        dlg = _ConditionEditDialog(node, self.parent)
        result = dlg.to_node()
        self.assertEqual(result["if"], {"all": ["ult1", "link"]})
        self.assertEqual(result["then"], ["1", "2"])
        self.assertNotIn("else", result)


if __name__ == "__main__":
    unittest.main()
