"""Qt 参数预览的宿主窗口生命周期回归（最小化/关闭/失活）。"""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtWidgets import QApplication, QWidget

from src.gui.param_preview_popup import (
    ParamPreviewController,
    ParamPreviewPopup,
    _CardHoverFilter,
)


class TestParamPreviewPopupLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.old_state = (
            ParamPreviewController._popup,
            ParamPreviewController._owner,
            ParamPreviewController._hide_timer,
        )
        self.window = QWidget()
        self.window.resize(600, 400)
        self.card = QWidget(self.window)
        self.card.setGeometry(20, 20, 400, 70)
        self.card.card = QWidget(self.card)
        self.card.card.setGeometry(0, 0, 400, 70)
        self.card.task = SimpleNamespace(name="接取委托")
        self.window.show()
        self.app.processEvents()
        self.popup = MagicMock()
        self.popup.show_for_task.return_value = True
        ParamPreviewController._popup = self.popup
        ParamPreviewController._owner = None
        ParamPreviewController._hide_timer = QTimer(self.window)
        self.hover = _CardHoverFilter(self.card)
        self.window.installEventFilter(self.hover)

    def tearDown(self):
        self.hover._show_timer.stop()
        ParamPreviewController._hide_timer.stop()
        self.window.removeEventFilter(self.hover)
        self.window.close()
        self.window.deleteLater()
        (
            ParamPreviewController._popup,
            ParamPreviewController._owner,
            ParamPreviewController._hide_timer,
        ) = self.old_state
        self.app.processEvents()

    def test_visible_card_can_show_preview(self):
        ParamPreviewController.show_for(self.card)
        self.popup.show_for_task.assert_called_once_with(self.card)
        self.popup.show.assert_called_once()
        self.assertIs(ParamPreviewController._owner, self.card)

    def test_minimizing_host_cancels_pending_hover_and_hides_without_animation(self):
        ParamPreviewController.show_for(self.card)
        self.hover._show_timer.start(500)
        self.window.showMinimized()
        self.app.processEvents()
        self.assertTrue(self.window.isMinimized())
        self.assertFalse(self.hover._show_timer.isActive())
        self.popup.hide_immediately.assert_called()
        self.assertIsNone(ParamPreviewController._owner)

    def test_hidden_host_cannot_resurrect_preview_from_queued_hover(self):
        self.window.hide()
        self.app.processEvents()
        self.popup.reset_mock()
        self.hover._show_popup()
        ParamPreviewController.show_for(self.card)
        self.popup.show.assert_not_called()
        self.popup.show_for_task.assert_not_called()
        self.popup.hide_immediately.assert_called()

    def test_window_close_or_deactivate_hides_preview(self):
        ParamPreviewController.show_for(self.card)
        self.app.sendEvent(self.window, QEvent(QEvent.WindowDeactivate))
        self.popup.hide_immediately.assert_called()
        self.popup.reset_mock()
        self.window.close()
        self.popup.hide_immediately.assert_called()
        self.assertIsNone(ParamPreviewController._owner)

    def test_immediate_hide_stops_all_animations(self):
        fake = SimpleNamespace(
            _show_anim=MagicMock(),
            _slide_anim=MagicMock(),
            _hide_anim=MagicMock(),
            hide=MagicMock(),
            setWindowOpacity=MagicMock(),
        )
        ParamPreviewPopup.hide_immediately(fake)
        fake._show_anim.stop.assert_called_once()
        fake._slide_anim.stop.assert_called_once()
        fake._hide_anim.stop.assert_called_once()
        fake.hide.assert_called_once()
        fake.setWindowOpacity.assert_called_once_with(1.0)


if __name__ == "__main__":
    unittest.main()
