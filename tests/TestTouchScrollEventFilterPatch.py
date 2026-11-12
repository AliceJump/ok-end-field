"""Compare the optimized guard with the pinned framework using real Qt objects."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ok.ui.qt.util.touch_scroll import _TouchScrollClickGuard, enable_touch_scrolling
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QScrollArea, QScroller, QWidget
from shiboken6 import delete, isValid

from src.patches.touch_scroll_event_filter_patch import install_touch_scroll_event_filter_patch


class _PressedWidget(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.isPressed = True


class TestTouchScrollEventFilterPatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        original = _TouchScrollClickGuard.eventFilter
        if getattr(original, "_okef_mouse_events_only", False):
            original = original.__wrapped__
        self.original = original
        # Restore the prior class method even when another suite installed it.
        self.enterContext(patch.object(_TouchScrollClickGuard, "eventFilter", original))
        install_touch_scroll_event_filter_patch()
        self.optimized = _TouchScrollClickGuard.eventFilter

    def _cleanup_scene(self, scene):
        if isValid(scene.guard):
            self.app.removeEventFilter(scene.guard)
        if isValid(scene.scroller):
            scene.scroller.stop()
        if isValid(scene.area):
            scene.area.hide()
            scene.area.deleteLater()
        if isValid(scene.guard) and scene.guard.parent() is None:
            scene.guard.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def _scene(self, *, global_filter=False):
        area = QScrollArea()
        area.resize(240, 160)
        content = QWidget()
        content.resize(220, 2000)
        button = QPushButton("click", content)
        button.setGeometry(10, 10, 80, 30)
        custom = _PressedWidget(content)
        custom.setGeometry(100, 10, 80, 30)
        area.setWidget(content)
        area.show()
        self.app.processEvents()
        scroller = QScroller.scroller(area.viewport())
        if global_filter:
            enable_touch_scrolling(area)
            guard = area._touch_scroll_click_guard
        else:
            guard = _TouchScrollClickGuard(area, scroller)
        scene = SimpleNamespace(
            area=area, content=content, button=button, custom=custom, scroller=scroller, guard=guard
        )
        self.addCleanup(self._cleanup_scene, scene)
        return scene

    @staticmethod
    def _mouse(event_type, *, fake=False):
        position = QPointF(-2_000_000, -2_000_000) if fake else QPointF(15, 15)
        buttons = Qt.MouseButton.LeftButton if event_type == QEvent.Type.MouseButtonPress else Qt.MouseButton.NoButton
        event = QMouseEvent(
            event_type,
            QPointF(5, 5),
            position,
            Qt.MouseButton.LeftButton,
            buttons,
            Qt.KeyboardModifier.NoModifier,
        )
        event.setAccepted(False)
        return event

    def _drag(self, scene):
        scene.scroller.handleInput(QScroller.Input.InputPress, QPointF(100, 120), 1000)
        scene.scroller.handleInput(QScroller.Input.InputMove, QPointF(100, 20), 1050)
        self.assertEqual(scene.scroller.state(), QScroller.State.Dragging)
        self.assertTrue(scene.guard.dragged)

    def _ended_drag(self, scene):
        self._drag(scene)
        scene.scroller.stop()
        self.assertEqual(scene.scroller.state(), QScroller.State.Inactive)
        self.assertTrue(scene.guard.dragged)

    @staticmethod
    def _filter_outcome(scene, filtered, event_type, target, *, fake=False):
        event = TestTouchScrollEventFilterPatch._mouse(event_type, fake=fake)
        result = filtered(scene.guard, target, event)
        return result, event.isAccepted(), scene.guard.dragged, scene.button.isDown(), scene.custom.isPressed

    def _compare(self, event_type, prepare, *, target_name="button", fake=False):
        outcomes = []
        for filtered in (self.original, self.optimized):
            scene = self._scene()
            prepare(scene)
            outcomes.append(self._filter_outcome(scene, filtered, event_type, getattr(scene, target_name), fake=fake))
            scene.scroller.stop()
        self.assertEqual(outcomes[1], outcomes[0])
        return outcomes[1]

    def test_unrelated_events_skip_original_and_content_hierarchy(self):
        scene = self._scene()
        scene.guard.dragged = True
        scene.button.setDown(True)
        calls = []

        def original(guard, obj, event):
            calls.append((guard, obj, event))
            return self.original(guard, obj, event)

        with patch.object(_TouchScrollClickGuard, "eventFilter", original):
            install_touch_scroll_event_filter_patch()
            with patch.object(scene.guard, "_is_content_widget", side_effect=AssertionError("unneeded hierarchy walk")):
                for event_type in (
                    QEvent.Type.Paint,
                    QEvent.Type.LayoutRequest,
                    QEvent.Type.StyleChange,
                    QEvent.Type.Show,
                    QEvent.Type.Hide,
                    QEvent.Type.Resize,
                    QEvent.Type.PolishRequest,
                    QEvent.Type.DeferredDelete,
                    QEvent.Type.MouseMove,
                    QEvent.Type.MouseButtonDblClick,
                    QEvent.Type.TouchBegin,
                    QEvent.Type.TouchEnd,
                ):
                    with self.subTest(event_type=event_type):
                        event = QEvent(event_type)
                        event.setAccepted(False)
                        self.assertFalse(scene.guard.eventFilter(scene.button, event))
                        self.assertFalse(event.isAccepted())
                        self.assertTrue(scene.guard.dragged)
                        self.assertTrue(scene.button.isDown())
                        self.assertTrue(scene.custom.isPressed)
        self.assertEqual(calls, [])

    def test_mouse_events_reach_original_once_with_identical_arguments(self):
        scene = self._scene()
        calls = []

        def original(guard, obj, event):
            calls.append((guard, obj, event))
            return self.original(guard, obj, event)

        with patch.object(_TouchScrollClickGuard, "eventFilter", original):
            install_touch_scroll_event_filter_patch()
            press = self._mouse(QEvent.Type.MouseButtonPress)
            self.assertFalse(scene.guard.eventFilter(scene.button, press))
            scene.guard.dragged = True
            release = self._mouse(QEvent.Type.MouseButtonRelease)
            self.assertTrue(scene.guard.eventFilter(scene.button, release))
        self.assertEqual(calls, [(scene.guard, scene.button, press), (scene.guard, scene.button, release)])

    def test_installing_twice_does_not_stack_wrappers(self):
        first = _TouchScrollClickGuard.eventFilter
        install_touch_scroll_event_filter_patch()
        self.assertIs(_TouchScrollClickGuard.eventFilter, first)
        self.assertIs(first.__wrapped__, self.original)

    def test_new_press_matches_upstream_and_clears_old_drag(self):
        result = self._compare(QEvent.Type.MouseButtonPress, lambda scene: setattr(scene.guard, "dragged", True))
        self.assertEqual(result[:3], (False, False, False))

    def test_press_during_real_drag_or_scroll_matches_upstream(self):
        def scrolling(scene):
            scene.scroller.scrollTo(QPointF(0, 400), 1000)
            self.assertEqual(scene.scroller.state(), QScroller.State.Scrolling)

        for name, prepare in (("dragging", self._drag), ("scrolling", scrolling)):
            with self.subTest(state=name):
                result = self._compare(QEvent.Type.MouseButtonPress, prepare)
                self.assertTrue(result[0])
                self.assertTrue(result[2])

    def test_real_release_after_drag_matches_upstream_and_clears_drag(self):
        result = self._compare(QEvent.Type.MouseButtonRelease, self._ended_drag)
        self.assertEqual(result[:3], (True, True, False))

    def test_fake_release_preserves_native_and_custom_pressed_state_reset(self):
        def prepare(scene):
            self._ended_drag(scene)
            scene.button.setDown(True)

        for target in ("button", "custom"):
            with self.subTest(target=target):
                result = self._compare(QEvent.Type.MouseButtonRelease, prepare, target_name=target, fake=True)
                self.assertEqual(result[:3], (True, True, True))
                self.assertEqual(result[3], target != "button")
                self.assertEqual(result[4], target != "custom")

    def test_release_without_drag_matches_upstream(self):
        result = self._compare(QEvent.Type.MouseButtonRelease, lambda scene: None)
        self.assertEqual(result[:3], (False, False, False))

    def test_double_click_does_not_change_drag_state(self):
        result = self._compare(QEvent.Type.MouseButtonDblClick, self._ended_drag)
        self.assertEqual(result[:3], (False, False, True))

    def test_external_and_nonwidget_targets_remain_unconsumed(self):
        for target_type in (QWidget, QObject):
            with self.subTest(target_type=target_type):
                results = []
                for filtered in (self.original, self.optimized):
                    scene = self._scene()
                    self._ended_drag(scene)
                    target = target_type(scene.area)
                    result = self._filter_outcome(scene, filtered, QEvent.Type.MouseButtonRelease, target, fake=True)
                    results.append(result)
                self.assertEqual(results[1], results[0])
                self.assertEqual(results[1][:3], (False, False, True))

    def test_destroyed_scroll_area_keeps_the_original_lifetime_guards(self):
        for active_after_delete in (False, True):
            with self.subTest(active_after_delete=active_after_delete):
                for filtered in (self.original, self.optimized):
                    scene = self._scene()
                    # Extend the guard's lifetime through its parent's teardown
                    # to exercise the same narrow window as the global filter.
                    scene.guard.setParent(None)
                    delete(scene.area)
                    self.assertTrue(isValid(scene.guard))
                    self.assertFalse(isValid(scene.area))
                    self.assertFalse(scene.guard._active)
                    if active_after_delete:
                        scene.guard._active = True  # Also check isValid fallback.
                    event = self._mouse(QEvent.Type.MouseButtonRelease, fake=True)
                    self.assertFalse(filtered(scene.guard, None, event))
                    self.assertFalse(event.isAccepted())

    def test_global_filter_suppresses_fake_release_then_allows_next_click(self):
        scene = self._scene(global_filter=True)
        clicks = []
        scene.button.clicked.connect(lambda: clicks.append(True))
        QTest.mouseClick(scene.button, Qt.MouseButton.LeftButton)
        self.assertEqual(len(clicks), 1)

        self._ended_drag(scene)
        scene.button.setDown(True)
        release = self._mouse(QEvent.Type.MouseButtonRelease, fake=True)
        QCoreApplication.sendEvent(scene.button, release)
        self.assertTrue(release.isAccepted())
        self.assertFalse(scene.button.isDown())
        self.assertEqual(len(clicks), 1)

        scene.area.verticalScrollBar().setValue(0)
        QTest.mouseClick(scene.button, Qt.MouseButton.LeftButton)
        self.assertFalse(scene.guard.dragged)
        self.assertEqual(len(clicks), 2)


if __name__ == "__main__":
    unittest.main()
