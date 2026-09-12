import math
from dataclasses import dataclass

import win32gui
from ok.util.logger import Logger
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QApplication, QWidget

logger = Logger.get_logger(__name__)


@dataclass
class ArrowSpec:
    arrow_type: str
    start_x_norm: float
    start_y_norm: float
    end_x_norm: float
    end_y_norm: float
    color: tuple[int, int, int]
    shaft_width_norm: float
    head_len_norm: float | None


@dataclass
class TextSpec:
    """叠层文字块：以左上角为锚点的多行文本，坐标与字号均按窗口尺寸归一化。"""

    text_key: str
    lines: list[str]
    x_norm: float
    y_norm: float
    color: tuple[int, int, int] = (255, 255, 255)
    alpha: int = 235
    font_size_norm: float = 0.024
    bold: bool = True
    line_spacing: float = 1.35
    panel_alpha: int = 150


class WindowArrowOverlay(QWidget):
    """透明叠层窗口，依附到游戏窗口之上绘制箭头。"""

    def __init__(self, hwnd: int, parent=None):
        super().__init__(parent)
        self._hwnd = hwnd
        self._arrows: list[ArrowSpec] = []
        self._texts: list[TextSpec] = []
        self._arrow_head_angle_deg = 28.0
        self._arrow_head_len_ratio = 0.35
        self._base_color = QColor(0, 255, 0, 160)
        # 叠层文字需要覆盖中英文，按可用性依次回退
        self._text_font_families = (
            "Microsoft YaHei UI",
            "Microsoft YaHei",
            "Segoe UI",
            "Noto Sans CJK SC",
            "Noto Sans SC",
            "sans-serif",
        )

        self._sync_timer = QTimer(self)
        self._sync_timer.timeout.connect(self._sync_geometry)
        self._sync_timer.start(50)

        self.setWindowFlags(
            Qt.Tool  # 工具窗口
            | Qt.FramelessWindowHint  # 无边框
            | Qt.WindowTransparentForInput
            | Qt.WindowStaysOnTopHint  # 全局置顶
        )

        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self._sync_geometry()

    def _should_show_overlay(self) -> bool:
        """仅在游戏窗口处于前台时显示箭头叠层。"""
        try:
            return win32gui.GetForegroundWindow() == self._hwnd
        except Exception:
            return False

    def set_style(self, color: tuple[int, int, int], head_angle_deg: float, head_len_ratio: float):
        # 支持三元/四元元组或直接传入 QColor；三元组使用默认 alpha
        try:
            if isinstance(color, QColor):
                self._base_color = color
            elif len(color) == 4:
                self._base_color = QColor(color[0], color[1], color[2], color[3])
            else:
                # 采用默认 alpha（与初始化时一致）
                self._base_color = QColor(color[0], color[1], color[2], self._base_color.alpha())
        except Exception:
            self._base_color = QColor(0, 255, 0, self._base_color.alpha())
        self._arrow_head_angle_deg = head_angle_deg
        self._arrow_head_len_ratio = head_len_ratio

    def set_arrows(self, arrows: list[ArrowSpec]):
        unique_arrows: dict[str, ArrowSpec] = {}
        ordered_types: list[str] = []
        for spec in arrows:
            arrow_type = getattr(spec, "arrow_type", None) or "default"
            if arrow_type not in unique_arrows:
                ordered_types.append(arrow_type)
            unique_arrows[arrow_type] = spec

        self._arrows = [unique_arrows[arrow_type] for arrow_type in ordered_types]
        self._apply_visibility()

    def clear_arrows(self):
        self._arrows = []
        self._refresh()

    def set_texts(self, texts: list[TextSpec]):
        unique_texts: dict[str, TextSpec] = {}
        ordered_keys: list[str] = []
        for spec in texts:
            text_key = getattr(spec, "text_key", None) or "default"
            if text_key not in unique_texts:
                ordered_keys.append(text_key)
            unique_texts[text_key] = spec

        self._texts = [unique_texts[text_key] for text_key in ordered_keys]
        self._apply_visibility()

    def clear_texts(self):
        self._texts = []
        self._refresh()

    def _has_content(self) -> bool:
        return bool(self._arrows or self._texts)

    def _apply_visibility(self):
        self._sync_geometry()
        if self._has_content() and self._should_show_overlay():
            self.show()
            self.raise_()
        else:
            self.hide()
        self._refresh()

    def _refresh(self):
        self.update()
        app = QApplication.instance()
        if app is not None:
            app.processEvents()

    def _sync_geometry(self):
        try:
            if not win32gui.IsWindow(self._hwnd):
                return
            left, top = win32gui.ClientToScreen(self._hwnd, (0, 0))
            right, bottom = win32gui.ClientToScreen(self._hwnd, win32gui.GetClientRect(self._hwnd)[2:])
            width = max(1, right - left)
            height = max(1, bottom - top)

            # 物理像素 -> Qt 逻辑坐标。不能简单用 物理/该屏DPR：
            # 混合 DPI 多屏下 Qt 的屏幕逻辑 origin 不等于 物理origin/DPR，
            # 否则 overlay 会向 x 正方向偏移（见 src/core/screen_coords.py 注释）。
            from src.core.screen_coords import physical_rect_to_logical

            lx, ly, lw, lh = physical_rect_to_logical(left, top, width, height)

            self.setGeometry(
                int(round(lx)),
                int(round(ly)),
                max(1, int(round(lw))),
                max(1, int(round(lh))),
            )
            if self._has_content() and self._should_show_overlay():
                self.show()
                self.raise_()
            else:
                self.hide()
        except Exception as e:
            logger.error(f"同步箭头叠层几何失败: {e}")

    def _color_for(self, color: tuple[int, int, int]) -> QColor:
        try:
            # 支持 QColor 直接传入
            if isinstance(color, QColor):
                return color
            # 支持三元或四元元组 (r,g,b) 或 (r,g,b,a)
            if len(color) == 4:
                return QColor(color[0], color[1], color[2], color[3])
            return QColor(color[0], color[1], color[2], self._base_color.alpha())
        except Exception:
            return QColor(0, 255, 0, self._base_color.alpha())

    def paintEvent(self, event):
        if not self._has_content():
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        width = max(1, self.width())
        height = max(1, self.height())

        for spec in self._arrows:
            self._paint_arrow(painter, spec, width, height)

        for spec in self._texts:
            self._paint_text(painter, spec, width, height)

    def _paint_text(self, painter: QPainter, spec: TextSpec, width: int, height: int):
        """绘制一个文字块：半透明底板 + 多行文本，保证在游戏画面上可读。"""
        lines = [str(line) for line in (spec.lines or []) if str(line)]
        if not lines:
            return

        pixel_size = max(8, round(float(spec.font_size_norm) * height))
        font = QFont()
        try:
            font.setFamilies(list(self._text_font_families))
        except AttributeError:
            # 旧版 Qt 没有 setFamilies，退化为单字体
            font.setFamily(self._text_font_families[0])
        font.setPixelSize(pixel_size)
        font.setBold(bool(spec.bold))
        painter.setFont(font)

        metrics = painter.fontMetrics()
        line_height = metrics.height() * max(1.0, float(spec.line_spacing))
        text_width = max(metrics.horizontalAdvance(line) for line in lines)
        pad_x = max(6.0, pixel_size * 0.45)
        pad_y = max(4.0, pixel_size * 0.28)

        left = float(spec.x_norm) * width
        top = float(spec.y_norm) * height
        panel_width = text_width + pad_x * 2
        panel_height = metrics.height() + line_height * (len(lines) - 1) + pad_y * 2

        text_color = self._color_for(spec.color)
        text_color.setAlpha(max(0, min(255, int(spec.alpha))))
        panel_color = QColor(0, 0, 0)
        panel_color.setAlpha(max(0, min(255, int(spec.panel_alpha))))

        radius = max(2.0, pixel_size * 0.35)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(panel_color))
        painter.drawRoundedRect(QRectF(left, top, panel_width, panel_height), radius, radius)

        painter.setPen(QPen(text_color))
        baseline = top + pad_y + metrics.ascent()
        for index, line in enumerate(lines):
            painter.drawText(QPointF(left + pad_x, baseline + line_height * index), line)

    def _paint_arrow(self, painter: QPainter, spec: ArrowSpec, width: int, height: int):
        start_x = spec.start_x_norm * width
        start_y = spec.start_y_norm * height
        end_x = spec.end_x_norm * width
        end_y = spec.end_y_norm * height

        dx = end_x - start_x
        dy = end_y - start_y
        length = math.hypot(dx, dy)
        if length <= 1e-6:
            return

        color = self._color_for(spec.color)
        # 允许更细的箭身，最小为 1 像素
        shaft_width = max(1, int(min(width, height) * spec.shaft_width_norm))
        head_len = (
            spec.head_len_norm * min(width, height)
            if spec.head_len_norm is not None
            else max(10.0, length * self._arrow_head_len_ratio)
        )
        head_len = max(8.0, min(head_len, min(width, height) * 0.18))

        ux = dx / length
        uy = dy / length
        theta = math.atan2(uy, ux)
        head_angle = math.radians(self._arrow_head_angle_deg)

        wing1 = QPointF(
            end_x + math.cos(theta + math.pi - head_angle) * head_len,
            end_y + math.sin(theta + math.pi - head_angle) * head_len,
        )
        wing2 = QPointF(
            end_x + math.cos(theta + math.pi + head_angle) * head_len,
            end_y + math.sin(theta + math.pi + head_angle) * head_len,
        )
        tip = QPointF(end_x, end_y)

        pen = QPen(color, shaft_width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        # 为避免箭身与三角头重叠并产生穿透视觉，
        # 将箭身绘制到三角基部中心处（tip 往回 head_len 的位置），
        # 三角形再覆盖在 tip 处。
        base_center_x = end_x - ux * head_len
        base_center_y = end_y - uy * head_len
        painter.drawLine(QPointF(start_x, start_y), QPointF(base_center_x, base_center_y))

        painter.setBrush(QBrush(color))
        painter.setPen(Qt.NoPen)
        triangle = QPolygonF([tip, wing1, wing2])
        painter.drawPolygon(triangle)

        # 在三角内部绘制一层白色描边（通过内缩多边形实现），
        # 然后再绘制更小的彩色中心以保留箭头尖端的颜色。
        try:
            cx = (tip.x() + wing1.x() + wing2.x()) / 3.0
            cy = (tip.y() + wing1.y() + wing2.y()) / 3.0

            def shrink_point(p: QPointF, k: float) -> QPointF:
                return QPointF(p.x() * (1.0 - k) + cx * k, p.y() * (1.0 - k) + cy * k)

            # k 值决定内缩量：较小的 k -> 薄的白色带
            inner_band_k = 0.18
            inner_center_k = 0.42

            inner_band = QPolygonF(
                [
                    shrink_point(tip, inner_band_k),
                    shrink_point(wing1, inner_band_k),
                    shrink_point(wing2, inner_band_k),
                ]
            )

            painter.setBrush(QBrush(QColor(255, 255, 255)))
            painter.setPen(Qt.NoPen)
            painter.drawPolygon(inner_band)

            # 再绘制更小的彩色中心三角，避免白色覆盖尖端颜色
            inner_center = QPolygonF(
                [
                    shrink_point(tip, inner_center_k),
                    shrink_point(wing1, inner_center_k),
                    shrink_point(wing2, inner_center_k),
                ]
            )
            painter.setBrush(QBrush(color))
            painter.drawPolygon(inner_center)
        except Exception:
            # 若计算出错，不影响主要绘制
            pass

        # 用一个小圆点稳住起点，视觉上更像正常箭头起点
        painter.drawEllipse(QPointF(start_x, start_y), max(1.0, shaft_width * 0.35), max(1.0, shaft_width * 0.35))


class WindowArrowOverlayController(QObject):
    """把箭头与文字更新切回 GUI 线程执行。"""

    arrow_updated = Signal(object)
    text_updated = Signal(object)
    clear_requested = Signal()
    texts_clear_requested = Signal()
    style_requested = Signal(tuple, float, float)

    def __init__(self, hwnd: int):
        super().__init__()
        self._arrow_timers: dict[str, QTimer] = {}
        self._text_timers: dict[str, QTimer] = {}
        self._arrow_timeout_ms = 2000
        self._hwnd = hwnd
        self._overlay: WindowArrowOverlay | None = None
        self._arrow_map: dict[str, ArrowSpec] = {}
        self._text_map: dict[str, TextSpec] = {}
        self.arrow_updated.connect(self._on_arrow_updated)
        self.text_updated.connect(self._on_text_updated)
        self.clear_requested.connect(self._on_clear_requested)
        self.texts_clear_requested.connect(self._on_texts_clear_requested)
        self.style_requested.connect(self._on_style_requested)

    @staticmethod
    def _drop_timer(timers: dict[str, QTimer], key: str):
        timer = timers.pop(key, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()

    @staticmethod
    def _stop_all_timers(timers: dict[str, QTimer]):
        for timer in timers.values():
            timer.stop()
            timer.deleteLater()
        timers.clear()

    @staticmethod
    def _restart_timer(owner: QObject, timers: dict[str, QTimer], key: str, timeout_ms: int, on_expire):
        timer = timers.get(key)

        if timer is None:
            timer = QTimer(owner)
            timer.setSingleShot(True)

            timer.timeout.connect(lambda k=key: on_expire(k))

            timers[key] = timer

        timer.start(timeout_ms)

    def _expire_arrow(self, arrow_type: str):
        self._arrow_map.pop(arrow_type, None)
        self._drop_timer(self._arrow_timers, arrow_type)
        self._apply_overlay_state()

    def _expire_text(self, text_key: str):
        self._text_map.pop(text_key, None)
        self._drop_timer(self._text_timers, text_key)
        self._apply_overlay_state()

    def _restart_arrow_timer(self, arrow_type: str):
        self._restart_timer(self, self._arrow_timers, arrow_type, self._arrow_timeout_ms, self._expire_arrow)

    def _restart_text_timer(self, text_key: str):
        self._restart_timer(self, self._text_timers, text_key, self._arrow_timeout_ms, self._expire_text)

    def _ensure_overlay(self) -> WindowArrowOverlay:
        if self._overlay is None:
            self._overlay = WindowArrowOverlay(self._hwnd)
        return self._overlay

    def _apply_overlay_state(self):
        overlay = self._ensure_overlay()
        overlay.set_arrows(list(self._arrow_map.values()))
        overlay.set_texts(list(self._text_map.values()))

    @Slot(object)
    def _on_arrow_updated(self, arrow: ArrowSpec):
        arrow_type = getattr(arrow, "arrow_type", None) or "default"

        self._arrow_map[arrow_type] = arrow

        self._restart_arrow_timer(arrow_type)

        self._apply_overlay_state()

    @Slot(object)
    def _on_text_updated(self, text: TextSpec):
        text_key = getattr(text, "text_key", None) or "default"

        self._text_map[text_key] = text

        self._restart_text_timer(text_key)

        self._apply_overlay_state()

    @Slot()
    def _on_clear_requested(self):
        self._arrow_map.clear()
        self._text_map.clear()

        self._stop_all_timers(self._arrow_timers)
        self._stop_all_timers(self._text_timers)

        if self._overlay is not None:
            self._overlay.clear_arrows()
            self._overlay.clear_texts()

    @Slot()
    def _on_texts_clear_requested(self):
        self._text_map.clear()
        self._stop_all_timers(self._text_timers)

        if self._overlay is not None:
            self._overlay.clear_texts()

    @Slot(tuple, float, float)
    def _on_style_requested(self, color: tuple[int, int, int], head_angle_deg: float, head_len_ratio: float):
        overlay = self._ensure_overlay()
        overlay.set_style(color, head_angle_deg, head_len_ratio)


class GdiArrowPainter:
    """headless 回退绘制器：用框架 Win32GdiOverlay 的自定义画笔画箭头与文字（无 Qt）。

    生命周期由任务驱动：draw_window_arrow / draw_window_text 每 ~0.3s 更新一次内容并触发重绘，
    每个箭头/文字块 2s 未刷新即消失（与 WindowArrowOverlayController 的超时语义一致）。
    """

    FRESH_SECONDS = 2.0
    TEXT_FONT_FACE = "Microsoft YaHei UI"
    # GDI 浮层没有逐像素 alpha（框架按「像素是否纯黑」决定透明与否），因此文字底板只能
    # 用不透明的深色填充；纯黑会让整块底板连同字形一起变透明，绝对不能用 (0, 0, 0)。
    PANEL_COLOR = (24, 24, 24)
    # GDI 路径没有逐像素 alpha，无法真正表现半透明底板。这里把底板色与一个中性底色
    # 按 panel_alpha 混合，近似 Qt 路径的半透明观感；panel_alpha > 0 时混合结果不会
    # 等于纯黑，因此不会被叠层的「纯黑即透明」规则误判。
    PANEL_BLEND_BG = (96, 96, 96)
    NULL_PEN = 9  # GetStockObject(NULL_PEN)

    def __init__(self, overlay):
        import threading

        self._overlay = overlay
        self._lock = threading.RLock()
        self._arrows: dict[str, tuple[ArrowSpec, float]] = {}
        self._texts: dict[str, tuple[TextSpec, float]] = {}
        self._head_angle_deg = 28.0
        self._head_len_ratio = 0.35

    def set_style(self, color, head_angle_deg, head_len_ratio):
        # GDI 画笔不支持逐像素 alpha，颜色以各自 spec.color 为准
        self._head_angle_deg = head_angle_deg
        self._head_len_ratio = head_len_ratio

    def add_arrow(self, arrow: ArrowSpec):
        import time as _time

        arrow_type = getattr(arrow, "arrow_type", None) or "default"
        with self._lock:
            self._arrows[arrow_type] = (arrow, _time.monotonic())
        self._overlay._schedule_render()

    def add_text(self, text: TextSpec):
        import time as _time

        text_key = getattr(text, "text_key", None) or "default"
        with self._lock:
            self._texts[text_key] = (text, _time.monotonic())
        self._overlay._schedule_render()

    def clear(self):
        with self._lock:
            self._arrows.clear()
        self._overlay._schedule_render()

    def clear_texts(self):
        with self._lock:
            self._texts.clear()
        self._overlay._schedule_render()

    def clear_all(self):
        with self._lock:
            self._arrows.clear()
            self._texts.clear()
        self._overlay._schedule_render()

    @staticmethod
    def _fresh_specs(store: dict, now: float) -> list:
        for key in [k for k, (_, ts) in store.items() if now - ts > GdiArrowPainter.FRESH_SECONDS]:
            store.pop(key, None)
        return [spec for spec, _ts in store.values()]

    def paint(self, canvas, overlay):
        import time as _time

        width = max(1, int(getattr(overlay, "_width", 0) or 1))
        height = max(1, int(getattr(overlay, "_height", 0) or 1))
        now = _time.monotonic()
        with self._lock:
            arrow_specs = self._fresh_specs(self._arrows, now)
            text_specs = self._fresh_specs(self._texts, now)
        if not arrow_specs and not text_specs:
            return
        try:
            from ok.ui.overlay.win32_gdi import gdi32

            for spec in arrow_specs:
                self._paint_arrow_gdi(gdi32, canvas.hdc, spec, width, height)
            for spec in text_specs:
                self._paint_text_gdi(gdi32, canvas.hdc, spec, width, height)
        except Exception as e:
            from ok.util.logger import Logger

            Logger.get_logger(__name__).error(f"GDI 箭头绘制失败: {e}")

    def _paint_text_gdi(self, gdi32, hdc, spec: TextSpec, width: int, height: int):
        import ctypes

        from ok.ui.overlay.win32_gdi import _rgb

        lines = [str(line) for line in (spec.lines or []) if str(line)]
        if not lines:
            return

        pixel_size = max(8, round(float(spec.font_size_norm) * height))
        weight = 700 if spec.bold else 400
        font = gdi32.CreateFontW(-pixel_size, 0, 0, 0, weight, 0, 0, 0, 1, 0, 0, 5, 0, self.TEXT_FONT_FACE)
        hdc_ref = ctypes.c_void_p(hdc)
        old_font = gdi32.SelectObject(hdc_ref, font)
        try:
            line_height = max(pixel_size, round(pixel_size * max(1.0, float(spec.line_spacing))))
            widths = [self._gdi_text_width(gdi32, hdc_ref, line, pixel_size) for line in lines]
            pad_x = max(6, int(pixel_size * 0.45))
            pad_y = max(4, int(pixel_size * 0.28))
            left = int(float(spec.x_norm) * width)
            top = int(float(spec.y_norm) * height)
            panel_width = max(widths) + pad_x * 2
            panel_height = pixel_size + line_height * (len(lines) - 1) + pad_y * 2

            if int(spec.panel_alpha) > 0:
                panel_color = self.gdi_panel_color(spec.panel_alpha)
                brush = gdi32.CreateSolidBrush(_rgb(*panel_color))
                old_brush = gdi32.SelectObject(hdc_ref, brush)
                old_pen = gdi32.SelectObject(hdc_ref, gdi32.GetStockObject(self.NULL_PEN))
                try:
                    gdi32.Rectangle(hdc_ref, left, top, left + panel_width, top + panel_height)
                finally:
                    gdi32.SelectObject(hdc_ref, old_brush)
                    gdi32.SelectObject(hdc_ref, old_pen)
                    gdi32.DeleteObject(brush)

            gdi32.SetTextColor(hdc_ref, _rgb(*tuple(spec.color)[:3]))
            gdi32.SetBkMode(hdc_ref, 1)  # TRANSPARENT
            for index, line in enumerate(lines):
                gdi32.TextOutW(hdc_ref, left + pad_x, top + pad_y + line_height * index, line, len(line))
        finally:
            gdi32.SelectObject(hdc_ref, old_font)
            gdi32.DeleteObject(font)

    @classmethod
    def gdi_panel_color(cls, panel_alpha) -> tuple[int, int, int]:
        """把 0-255 的底板 alpha 近似成 GDI 可用的不透明颜色。

        alpha 越低，颜色越接近 `PANEL_BLEND_BG`（观感更轻），alpha 为 255 时即 `PANEL_COLOR`。
        """
        try:
            alpha = float(panel_alpha)
        except (TypeError, ValueError):
            alpha = 0.0
        ratio = max(0.0, min(1.0, alpha / 255.0))
        return tuple(
            round(color * ratio + bg * (1.0 - ratio))
            for color, bg in zip(cls.PANEL_COLOR, cls.PANEL_BLEND_BG, strict=True)
        )

    @staticmethod
    def _gdi_text_width(gdi32, hdc_ref, text: str, pixel_size: int) -> int:
        import ctypes

        from ok.ui.overlay.win32_gdi import SIZE

        try:
            size = SIZE()
            if gdi32.GetTextExtentPoint32W(hdc_ref, text, len(text), ctypes.byref(size)):
                return int(size.cx)
        except Exception:
            pass
        # 取不到 extent 时按字符宽度估算：CJK 约 1em，其余约 0.6em
        wide = sum(1 for ch in text if ord(ch) > 0x2E80)
        return int(pixel_size * (wide + (len(text) - wide) * 0.6))

    def _paint_arrow_gdi(self, gdi32, hdc, spec: ArrowSpec, width: int, height: int):
        import ctypes
        import math

        from ok.ui.overlay.win32_gdi import POINT, _rgb

        sx, sy = spec.start_x_norm * width, spec.start_y_norm * height
        ex, ey = spec.end_x_norm * width, spec.end_y_norm * height
        dx, dy = ex - sx, ey - sy
        length = math.hypot(dx, dy)
        if length <= 1e-6:
            return

        color = tuple(spec.color)[:3]
        shaft_width = max(1, int(min(width, height) * spec.shaft_width_norm))
        head_len = (
            spec.head_len_norm * min(width, height)
            if spec.head_len_norm is not None
            else max(10.0, length * self._head_len_ratio)
        )
        head_len = max(8.0, min(head_len, min(width, height) * 0.18))

        ux, uy = dx / length, dy / length
        theta = math.atan2(uy, ux)
        head_angle = math.radians(self._head_angle_deg)
        wing1 = (
            ex + math.cos(theta + math.pi - head_angle) * head_len,
            ey + math.sin(theta + math.pi - head_angle) * head_len,
        )
        wing2 = (
            ex + math.cos(theta + math.pi + head_angle) * head_len,
            ey + math.sin(theta + math.pi + head_angle) * head_len,
        )
        base_center_x, base_center_y = ex - ux * head_len, ey - uy * head_len

        pen = gdi32.CreatePen(0, shaft_width, _rgb(*color))  # PS_SOLID
        brush = gdi32.CreateSolidBrush(_rgb(*color))
        # 框架未给这些 gdi32 函数设置 argtypes，64 位 HDC 必须包成 c_void_p 传入
        hdc_ref = ctypes.c_void_p(hdc)
        old_pen = gdi32.SelectObject(hdc_ref, pen)
        old_brush = gdi32.SelectObject(hdc_ref, brush)
        try:
            # 箭身画到三角基部，三角覆盖尖端
            gdi32.MoveToEx(hdc_ref, int(sx), int(sy), None)
            gdi32.LineTo(hdc_ref, int(base_center_x), int(base_center_y))
            points = (POINT * 3)(
                POINT(int(ex), int(ey)), POINT(int(wing1[0]), int(wing1[1])), POINT(int(wing2[0]), int(wing2[1]))
            )
            gdi32.Polygon(hdc_ref, points, 3)
            # 起点圆点，视觉上与 Qt 版一致
            dot_r = max(1.0, shaft_width * 0.35)
            gdi32.Ellipse(hdc_ref, int(sx - dot_r), int(sy - dot_r), int(sx + dot_r), int(sy + dot_r))
        finally:
            gdi32.SelectObject(hdc_ref, old_pen)
            gdi32.SelectObject(hdc_ref, old_brush)
            gdi32.DeleteObject(pen)
            gdi32.DeleteObject(brush)


class _GdiEmit:
    """让 GDI 控制器复用 Qt 控制器的 emit 调用面。"""

    def __init__(self, fn):
        self._fn = fn

    def emit(self, *args):
        self._fn(*args)


class GdiArrowController:
    """与 WindowArrowOverlayController 相同的调用面，但绘制走框架 GDI 浮层。"""

    def __init__(self, painter: GdiArrowPainter):
        self._painter = painter
        self.arrow_updated = _GdiEmit(painter.add_arrow)
        self.text_updated = _GdiEmit(painter.add_text)
        self.clear_requested = _GdiEmit(painter.clear_all)
        self.texts_clear_requested = _GdiEmit(painter.clear_texts)
        self.style_requested = _GdiEmit(painter.set_style)


class WindowArrowDrawingMixin:
    """为 Task 类提供窗口箭头绘制功能。"""

    def _init_window_arrow_drawing_mixin(self):
        # 默认样式（调用方可通过函数参数直接传入覆盖）
        # 颜色使用 RGB 三元组，alpha 单独配置
        self._window_arrow_color = (0, 255, 0)
        self._window_arrow_alpha = 160
        # 细一些的默认箭身宽度
        self._window_arrow_shaft_width_norm = 0.005
        self._window_arrow_head_angle_deg = 28.0
        self._window_arrow_head_len_ratio = 0.35
        # 叠层文字默认样式
        self._window_arrow_text_alpha = 235
        self._window_arrow_text_font_size_norm = 0.024
        self._window_arrow_overlay: WindowArrowOverlay | None = None
        self._window_arrow_controller: WindowArrowOverlayController | None = None
        self._window_arrow_gdi_controller: GdiArrowController | None = None
        self._window_arrow_gdi_warned = False

    def _ensure_window_arrow_controller(self):
        if self._window_arrow_controller is not None:
            return self._window_arrow_controller

        app = getattr(self, "app", None)
        if app is None:
            try:
                from PySide6.QtWidgets import QApplication

                app = QApplication.instance()
            except Exception:
                app = None

        if app is None:
            # headless（插件任务启动器）无 Qt 事件循环，回退到框架 GDI 浮层
            gdi_controller = self._ensure_gdi_arrow_controller()
            if gdi_controller is None:
                if not self._window_arrow_gdi_warned:
                    self._window_arrow_gdi_warned = True
                    logger.error(
                        "无法创建箭头叠层：headless 下未开启调试浮层（工具箱“调试浮层”开关或 OK_TOOLKIT_USE_OVERLAY=1）"
                    )
                return None
            return gdi_controller

        hwnd = self.get_game_hwnd()
        self._window_arrow_controller = WindowArrowOverlayController(hwnd)
        self._window_arrow_controller.moveToThread(app.thread())
        self._window_arrow_controller.style_requested.emit(
            self._window_arrow_color,
            self._window_arrow_head_angle_deg,
            self._window_arrow_head_len_ratio,
        )
        self._window_arrow_overlay = self._window_arrow_controller._overlay
        return self._window_arrow_controller

    def _ensure_gdi_arrow_controller(self):
        if self._window_arrow_gdi_controller is not None:
            return self._window_arrow_gdi_controller

        try:
            from ok import og

            app = getattr(og, "app", None)
            overlay = app.get_overlay_view() if app is not None else None
        except Exception as e:
            logger.error(f"获取框架浮层失败: {e}")
            return None
        if overlay is None:
            return None

        painter = GdiArrowPainter(overlay)
        painter.set_style(
            self._window_arrow_color,
            self._window_arrow_head_angle_deg,
            self._window_arrow_head_len_ratio,
        )
        try:
            overlay.draw("window_arrows", painter.paint)
        except Exception as e:
            logger.error(f"注册 GDI 箭头画笔失败: {e}")
            return None
        self._window_arrow_gdi_controller = GdiArrowController(painter)
        return self._window_arrow_gdi_controller

    def _get_window_arrow_size(self) -> tuple[int, int]:
        """获取当前游戏窗口的客户区大小。"""
        try:
            overlay = self._window_arrow_overlay
            if overlay is not None and overlay.width() > 0 and overlay.height() > 0:
                return overlay.width(), overlay.height()

            hwnd = self.get_game_hwnd()
            left, top, right, bottom = win32gui.GetClientRect(hwnd)
            return right - left, bottom - top
        except Exception as e:
            logger.error(f"获取窗口大小失败: {e}")
            return 0, 0

    def draw_window_arrow(
        self,
        start_x_norm: float,
        start_y_norm: float,
        end_x_norm: float,
        end_y_norm: float,
        shaft_width_norm: float | None = None,
        head_len_norm: float | None = None,
        color: tuple[int, int, int] | None = None,
        alpha: int | None = None,
        arrow_type: str = "default",
    ) -> bool:
        """
        在游戏窗口上绘制单个箭头。

        Args:
            start_x_norm: 起点归一化 X 坐标 [0, 1]
            start_y_norm: 起点归一化 Y 坐标 [0, 1]
            end_x_norm: 终点归一化 X 坐标 [0, 1]
            end_y_norm: 终点归一化 Y 坐标 [0, 1]
            shaft_width_norm: 箭身宽度（归一化），默认使用全局设置
            head_len_norm: 箭头头部长度（归一化），默认自动计算
            color: 箭头颜色 (RGB)，默认使用全局设置
            alpha: 透明度 (0-255)，默认使用全局设置，0=完全透明 255=完全不透明

        Returns:
            是否绘制成功
        """
        try:
            controller = self._ensure_window_arrow_controller()
            if controller is None:
                return False

            # 构造最终颜色：如果传入了 alpha，则转换为 RGBA 四元组
            final_color = color or self._window_arrow_color
            if alpha is not None and isinstance(final_color, tuple) and len(final_color) == 3:
                final_color = (final_color[0], final_color[1], final_color[2], alpha)

            controller.arrow_updated.emit(
                ArrowSpec(
                    arrow_type=arrow_type or "default",
                    start_x_norm=start_x_norm,
                    start_y_norm=start_y_norm,
                    end_x_norm=end_x_norm,
                    end_y_norm=end_y_norm,
                    color=final_color,
                    shaft_width_norm=shaft_width_norm or self._window_arrow_shaft_width_norm,
                    head_len_norm=head_len_norm,
                )
            )
            return True
        except Exception as e:
            logger.error(f"绘制窗口箭头失败: {e}")
            return False

    def draw_window_arrow_from_center(
        self,
        center_x: float,
        center_y: float,
        max_length: float,
        draw_length: float,
        angle_deg: float,
        shaft_width_norm: float | None = None,
        head_len_norm: float | None = None,
        color: tuple[int, int, int] | None = None,
        alpha: int | None = None,
        center_is_norm: bool = False,
        length_is_norm: bool = False,
        arrow_type: str = "default",
    ) -> bool:
        """
        以中心点、最大长度、绘制长度和角度直接绘制箭头。

        角度约定：
        - 0° 朝上
        - 90° 朝右
        - 180° 朝下
        - 270° 朝左
        - 顺时针增加

        Args:
            center_x: 中心点 X 坐标，默认像素坐标
            center_y: 中心点 Y 坐标，默认像素坐标
            max_length: 最大长度，默认像素长度
            draw_length: 实际绘制长度，默认像素长度
            angle_deg: 箭头朝向角度，0° 朝上
            shaft_width_norm: 箭身宽度（归一化）
            head_len_norm: 箭头头部长度（归一化），相对于窗口较小边
            color: 箭头颜色 (RGB)
            alpha: 透明度 (0-255)，默认使用全局设置
            center_is_norm: 中心坐标是否为归一化坐标
            length_is_norm: 长度是否为归一化长度

        Returns:
            是否绘制成功
        """
        width, height = self._get_window_arrow_size()
        if width <= 0 or height <= 0:
            return False

        base_size = min(width, height)
        if center_is_norm:
            center_x = center_x * width
            center_y = center_y * height
        if length_is_norm:
            max_length = max_length * base_size
            draw_length = draw_length * base_size

        length = max(0.0, min(float(draw_length), float(max_length)))
        angle_rad = math.radians(angle_deg)

        # 0° 向上，角度顺时针增加
        end_x = center_x + math.sin(angle_rad) * length
        end_y = center_y - math.cos(angle_rad) * length

        return self.draw_window_arrow(
            start_x_norm=center_x / width,
            start_y_norm=center_y / height,
            end_x_norm=end_x / width,
            end_y_norm=end_y / height,
            shaft_width_norm=shaft_width_norm or self._window_arrow_shaft_width_norm,
            head_len_norm=head_len_norm,
            color=color,
            alpha=alpha,
            arrow_type=arrow_type,
        )

    def draw_window_text(
        self,
        lines,
        text_key: str = "default",
        x_norm: float = 0.015,
        y_norm: float = 0.2,
        color: tuple[int, int, int] | None = None,
        alpha: int | None = None,
        font_size_norm: float | None = None,
        bold: bool = True,
        line_spacing: float = 1.35,
        panel_alpha: int = 150,
    ) -> bool:
        """
        在游戏窗口上绘制一个多行文字块（左上角锚点，坐标与字号按窗口尺寸归一化）。

        与 draw_window_arrow 共用同一套叠层与 2 秒超时语义：每次轮询刷新一次即可持续显示，
        停止刷新后自动消失。同一 text_key 反复调用只保留最后一份内容。

        Args:
            lines: 文本行，支持 str 或 str 列表；空行会被忽略
            text_key: 文字块标识，用于去重与独立超时
            x_norm: 左上角归一化 X 坐标 [0, 1]
            y_norm: 左上角归一化 Y 坐标 [0, 1]
            color: 文字颜色 (RGB)，默认使用全局箭头颜色
            alpha: 文字透明度 (0-255)，默认 235
            font_size_norm: 字号（相对窗口高度），默认 0.024
            bold: 是否加粗
            line_spacing: 行距倍数
            panel_alpha: 底板透明度 (0-255)，0 表示不画底板；headless GDI 路径无逐像素
                alpha，底板会退化为不透明深色

        Returns:
            是否绘制成功
        """
        try:
            controller = self._ensure_window_arrow_controller()
            if controller is None:
                return False

            if isinstance(lines, str):
                normalized_lines = [lines]
            elif lines is None:
                normalized_lines = []
            else:
                normalized_lines = [str(line) for line in lines]
            normalized_lines = [line for line in normalized_lines if line]
            if not normalized_lines:
                return False

            controller.text_updated.emit(
                TextSpec(
                    text_key=text_key or "default",
                    lines=normalized_lines,
                    x_norm=x_norm,
                    y_norm=y_norm,
                    color=color or self._window_arrow_color,
                    alpha=self._window_arrow_text_alpha if alpha is None else alpha,
                    font_size_norm=(
                        self._window_arrow_text_font_size_norm if font_size_norm is None else font_size_norm
                    ),
                    bold=bold,
                    line_spacing=line_spacing,
                    panel_alpha=panel_alpha,
                )
            )
            return True
        except Exception as e:
            logger.error(f"绘制窗口文字失败: {e}")
            return False

    def clear_window_texts(self):
        """清空窗口上的所有文字块（保留箭头）。"""
        try:
            controller = self._ensure_window_arrow_controller()
            if controller is None:
                return
            controller.texts_clear_requested.emit()
        except Exception as e:
            logger.error(f"清空窗口文字失败: {e}")

    def clear_window_arrows(self):
        """清空窗口上的所有箭头与文字块。"""
        try:
            controller = self._ensure_window_arrow_controller()
            if controller is None:
                return
            controller.clear_requested.emit()
        except Exception as e:
            logger.error(f"清空窗口箭头失败: {e}")
