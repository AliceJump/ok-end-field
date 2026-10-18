"""Focused, display-independent Nailong eye-movement and live-task tests."""

from __future__ import annotations

import math
import os
import unittest
from pathlib import Path
from types import SimpleNamespace

# The GUI test must work without a Windows display session.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QStackedWidget, QWidget  # noqa: E402

from src.gui.NailongWidget import EYES, MAX_PUPIL_TRAVEL, NailongBackdrop, eye_offset, task_is_running  # noqa: E402
from src.themes.theme_pack import read_theme_pack  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class TestNailongWidget(unittest.TestCase):
    def test_wallpaper_persists_when_navigation_changes_and_resizes(self):
        """Keep the real face alive and input-transparent across page switches."""
        app = QApplication.instance() or QApplication([])
        window = QWidget()
        window.resize(900, 600)
        pages = QStackedWidget(window)
        pages.setGeometry(window.rect())
        pages.addWidget(QWidget())
        pages.addWidget(QWidget())
        face = NailongBackdrop(window)
        pack = read_theme_pack(ROOT / "theme-packs" / "nailong" / "nailong.okef-theme.zip")
        try:
            window.show()
            face.set_theme(pack)
            app.processEvents()
            self.assertFalse(face._background.isNull(), "Qt must decode the bundled WebP face")
            self.assertTrue(face.isVisible())
            self.assertTrue(face.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
            self.assertEqual(face.geometry(), window.rect())

            pages.setCurrentIndex(1)
            app.processEvents()
            self.assertTrue(face.isVisible(), "Switching to another tab must not hide the face")
            self.assertTrue(face._eye_timer.isActive())

            window.resize(1100, 740)
            app.processEvents()
            self.assertEqual(face.geometry(), window.rect())

            window.hide()
            app.processEvents()
            self.assertFalse(face._eye_timer.isActive(), "A hidden window must not poll the cursor")
            window.show()
            app.processEvents()
            self.assertTrue(face._eye_timer.isActive(), "Eye tracking must resume after restoring the window")

            face.set_theme(None)
            self.assertFalse(face.isVisible())
            self.assertFalse(face._eye_timer.isActive())
        finally:
            window.close()

    def test_stationary_cursor_returns_centered_pupil(self):
        """Avoid drift when the pointer lies at the center of an iris."""
        for eye in EYES:
            self.assertEqual(eye_offset(eye, eye), (0.0, 0.0))

    def test_off_window_cursor_stays_inside_iris(self):
        """Keep pupil centers bounded even for very distant pointers."""
        for eye in EYES:
            for pointer in ((-10000, -10000), (15000, 15000), (-9999, 5000)):
                dx, dy = eye_offset(eye, pointer)
                self.assertLessEqual(math.hypot(dx, dy), MAX_PUPIL_TRAVEL + 1e-8)

    def test_working_avatar_reflects_real_executor_state(self):
        """Never pretend to run a task when the executor is idle or paused."""
        self.assertFalse(task_is_running(None))
        self.assertFalse(task_is_running(SimpleNamespace(current_task=None, paused=False)))
        self.assertFalse(task_is_running(SimpleNamespace(current_task=object(), paused=True)))
        self.assertTrue(task_is_running(SimpleNamespace(current_task=object(), paused=False)))


if __name__ == "__main__":
    unittest.main()
