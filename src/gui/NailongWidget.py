"""Native Qt Nailong visuals: animated eyes and an unobtrusive task companion."""

from __future__ import annotations

import math

from PySide6.QtCore import QBuffer, QByteArray, QEvent, QIODevice, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QMovie, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from src.themes.theme_pack import ThemePack

# Coordinates measured in MaaEnd's 1448 x 1086 pupil-free background asset.
EYES = ((493, 381), (954, 381))
PUPIL_RADIUS = 56
MAX_PUPIL_TRAVEL = 35


def eye_offset(eye: tuple[float, float], pointer: tuple[float, float]) -> tuple[float, float]:
    """Point the pupil toward a cursor, clamping it inside its green iris."""
    dx, dy = pointer[0] - eye[0], pointer[1] - eye[1]
    factor = min(0.15, MAX_PUPIL_TRAVEL / (math.hypot(dx, dy) or 1))
    return dx * factor, dy * factor


def task_is_running(executor: object | None) -> bool:
    """Reflect the real active task instead of simulating a running state."""
    return bool(
        executor is not None
        and getattr(executor, "current_task", None) is not None
        and not getattr(executor, "paused", False)
    )


class NailongScene(QWidget):
    """Draw a pupil-free face and live eyes, with no WebView or global hooks."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._background = QPixmap()
        self._last_cursor = None
        self._eye_timer = QTimer(self)
        self._eye_timer.setInterval(50)
        self._eye_timer.timeout.connect(self._check_cursor)
        self.setMinimumHeight(300)

    def set_theme(self, pack: ThemePack | None) -> None:
        """Switch the backing image and stop updating when not selected."""
        self._background = QPixmap()
        if pack is not None and pack.kind == "nailong":
            self._background.loadFromData(pack.assets["assets/background.webp"], "WEBP")
        self._last_cursor = None
        self._sync_timer()
        self.update()

    def _sync_timer(self) -> None:
        """Only read the cursor while a visible themed scene needs repaints."""
        if self.isVisible() and not self._background.isNull():
            self._eye_timer.start()
        else:
            self._eye_timer.stop()

    def showEvent(self, event):
        """Resume eye tracking when the scene becomes visible."""
        super().showEvent(event)
        self._sync_timer()

    def hideEvent(self, event):
        """Avoid polling a hidden tab."""
        self._eye_timer.stop()
        super().hideEvent(event)

    def _check_cursor(self) -> None:
        """Repaint only when the cursor has actually moved."""
        cursor = QCursor.pos()
        position = (cursor.x(), cursor.y())
        if position != self._last_cursor:
            self._last_cursor = position
            self.update()

    def paintEvent(self, event):
        """Fit the original face with cover geometry and overlay two pupils."""
        super().paintEvent(event)
        if self._background.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, height = self._background.width(), self._background.height()
        scale = max(self.width() / width, self.height() / height)
        left = (self.width() - width * scale) / 2
        top = (self.height() - height * scale) / 2
        painter.setClipRect(self.rect())
        painter.drawPixmap(QRectF(left, top, width * scale, height * scale), self._background, QRectF(0, 0, width, height))

        mouse = self.mapFromGlobal(QCursor.pos())
        source_pointer = ((mouse.x() - left) / scale, (mouse.y() - top) / scale)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#121612"))
        for eye in EYES:
            dx, dy = eye_offset(eye, source_pointer)
            radius = PUPIL_RADIUS * scale
            painter.drawEllipse(
                QRectF(
                    left + (eye[0] + dx) * scale - radius,
                    top + (eye[1] + dy) * scale - radius,
                    radius * 2,
                    radius * 2,
                )
            )
        painter.end()




class NailongBackdrop(NailongScene):
    """Keep the interactive face behind every page of the main window.

    Unlike the theme-tab preview, this is a sibling of the navigation and
    page stack, not a child of any particular page. It never accepts input.
    """

    def __init__(self, window: QWidget):
        super().__init__(window)
        self.setMinimumHeight(0)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAutoFillBackground(False)
        self.setObjectName("NailongBackdrop")
        window.installEventFilter(self)
        self.setGeometry(window.rect())
        self.hide()

    def set_theme(self, pack: ThemePack | None) -> None:
        """Show the background across navigation tabs only for Nailong packs."""
        super().set_theme(pack)
        if pack is not None and pack.kind == "nailong" and not self._background.isNull():
            parent = self.parentWidget()
            if parent is not None:
                self.setGeometry(parent.rect())
            self.show()
            self.lower()
        else:
            self.hide()

    def eventFilter(self, obj, event):
        """Track root-window resizing without replacing upstream page widgets."""
        if obj is self.parentWidget() and event.type() in {QEvent.Type.Resize, QEvent.Type.Show}:
            self.setGeometry(obj.rect())
            if self.isVisible():
                self.lower()
        return super().eventFilter(obj, event)


class NailongCompanion(QWidget):
    """Small floating character visible across application pages when enabled."""

    def __init__(self, window: QWidget, executor: object | None = None):
        super().__init__(window)
        self.executor = executor
        self._standing = QPixmap()
        self._working = QPixmap()
        self._buffer = QBuffer(self)
        self._movie = QMovie(self)
        self._movie.frameChanged.connect(self.update)
        self._active = False
        self._running = False
        self.setFixedSize(160, 175)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        window.installEventFilter(self)
        self._timer = QTimer(self)
        self._timer.setInterval(700)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def set_theme(self, pack: ThemePack | None) -> None:
        """Install or remove the character's images without changing task state."""
        self._movie.stop()
        self._buffer.close()
        self._standing = QPixmap()
        self._working = QPixmap()
        self._active = pack is not None and pack.kind == "nailong"
        if self._active:
            self._standing.loadFromData(pack.assets["assets/standing.webp"], "WEBP")
            self._working.loadFromData(pack.assets["assets/working.webp"], "WEBP")
            self._buffer.setData(QByteArray(pack.assets["assets/working.webp"]))
            self._buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            self._movie.setDevice(self._buffer)
            self._movie.setFormat(b"webp")
        self._running = task_is_running(self.executor)
        if self._active:
            self._position()
            self.show()
            self.raise_()
            self._timer.start()
            self._tick()
        else:
            self._timer.stop()
            self.hide()
        self.update()

    def _position(self) -> None:
        """Anchor in a window corner without covering the navigation strip."""
        parent = self.parentWidget()
        if parent is not None:
            self.move(max(0, parent.width() - self.width() - 18), max(0, parent.height() - self.height() - 18))

    def eventFilter(self, obj, event):
        """Follow root-window layout changes without polling geometry."""
        if obj is self.parentWidget() and event.type() in {QEvent.Type.Resize, QEvent.Type.Show}:
            self._position()
        return super().eventFilter(obj, event)

    def _tick(self) -> None:
        """Swap character visuals from the executor's actual active task."""
        running = task_is_running(self.executor)
        if self._running != running:
            self._running = running
            self.update()
        if running and self._movie.isValid():
            if self._movie.state() != QMovie.MovieState.Running:
                self._movie.start()
        else:
            self._movie.stop()

    def paintEvent(self, event):
        """Render the idle or working character with a status caption."""
        super().paintEvent(event)
        if not self._active:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        image = (
            self._movie.currentPixmap()
            if self._running and self._movie.isValid() and not self._movie.currentPixmap().isNull()
            else self._working if self._running else self._standing
        )
        if not image.isNull():
            available = QRectF(10, 4, self.width() - 20, self.height() - 37)
            scaled = image.size().scaled(available.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio)
            target = QRectF(
                (self.width() - scaled.width()) / 2,
                available.top() + (available.height() - scaled.height()),
                scaled.width(),
                scaled.height(),
            )
            painter.drawPixmap(target, image, QRectF(0, 0, image.width(), image.height()))
        painter.setPen(QColor("#ffd85d"))
        painter.drawText(self.rect().adjusted(5, 0, -5, -5), Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
                         "奶龙工作中…" if self._running else "奶龙待命中")
        painter.end()
