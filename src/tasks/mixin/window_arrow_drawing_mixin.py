from dataclasses import dataclass
import math
from typing import Dict, Tuple, Optional, List

import win32gui
import win32con
from PySide6.QtCore import QPoint, QPointF, QTimer, Qt, QObject, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF, QBrush
from PySide6.QtWidgets import QApplication, QWidget

from ok.util.logger import Logger

logger = Logger.get_logger(__name__)


@dataclass
class ArrowSpec:
    arrow_type: str
    start_x_norm: float
    start_y_norm: float
    end_x_norm: float
    end_y_norm: float
    color: Tuple[int, int, int]
    shaft_width_norm: float
    head_len_norm: Optional[float]


class WindowArrowOverlay(QWidget):
    """透明叠层窗口，依附到游戏窗口之上绘制箭头。"""

    def __init__(self, hwnd: int, parent=None):
        super().__init__(parent)
        self._hwnd = hwnd
        self._arrows: List[ArrowSpec] = []
        self._arrow_head_angle_deg = 28.0
        self._arrow_head_len_ratio = 0.35
        # 默认使用半透明绿色（alpha=160）以减少视觉遮挡
        self._base_color = QColor(0, 255, 0, 160)
        self._sync_timer = QTimer(self)
        self._sync_timer.timeout.connect(self._sync_geometry)
        self._sync_timer.start(50)

        # 自动清除计时器：每次 set_arrows 后会启动，超时后清空箭头（最大停留时长）
        self._auto_clear_timer = QTimer(self)
        self._auto_clear_timer.setSingleShot(True)
        self._auto_clear_timer.timeout.connect(self.clear_arrows)

        self.setWindowFlags(
            Qt.Tool |
            Qt.FramelessWindowHint |
            Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._bind_to_game_window_layer()
        self._sync_geometry()

    def _bind_to_game_window_layer(self):
        """将叠层绑定为游戏窗口的 owned window，保持在游戏窗口之上但不全局置顶。"""
        try:
            # 确保原生窗口句柄已创建
            overlay_hwnd = int(self.winId())
            if overlay_hwnd and win32gui.IsWindow(self._hwnd):
                win32gui.SetWindowLong(overlay_hwnd, win32con.GWL_HWNDPARENT, self._hwnd)
        except Exception as e:
            logger.error(f"绑定箭头叠层到游戏窗口失败: {e}")

    def _should_show_overlay(self) -> bool:
        """仅在游戏窗口处于前台时显示箭头叠层。"""
        try:
            return win32gui.GetForegroundWindow() == self._hwnd
        except Exception:
            return False

    def set_style(self, color: Tuple[int, int, int], head_angle_deg: float, head_len_ratio: float):
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

    def set_arrows(self, arrows: List[ArrowSpec]):
        unique_arrows: Dict[str, ArrowSpec] = {}
        ordered_types: List[str] = []
        for spec in arrows:
            arrow_type = getattr(spec, 'arrow_type', None) or 'default'
            if arrow_type not in unique_arrows:
                ordered_types.append(arrow_type)
            unique_arrows[arrow_type] = spec

        self._arrows = [unique_arrows[arrow_type] for arrow_type in ordered_types]
        self._sync_geometry()
        if self._should_show_overlay():
            self.show()
            self.raise_()
        else:
            self.hide()
        self.update()
        app = QApplication.instance()
        if app is not None:
            app.processEvents()
        # 每次绘制后启动自动清除计时器，2s 后尝试清空
        try:
            self._auto_clear_timer.stop()
            self._auto_clear_timer.start(2000)
        except Exception:
            pass

    def clear_arrows(self):
        self._arrows = []
        self.update()
        app = QApplication.instance()
        if app is not None:
            app.processEvents()
        # 停止自动清除计时器（如果存在），避免计时器后续再触发清空
        try:
            if hasattr(self, '_auto_clear_timer') and self._auto_clear_timer is not None:
                self._auto_clear_timer.stop()
        except Exception:
            pass

    def _sync_geometry(self):
        try:
            if not win32gui.IsWindow(self._hwnd):
                return
            left, top = win32gui.ClientToScreen(self._hwnd, (0, 0))
            right, bottom = win32gui.ClientToScreen(self._hwnd, win32gui.GetClientRect(self._hwnd)[2:])
            width = max(1, right - left)
            height = max(1, bottom - top)

            screen = QGuiApplication.screenAt(QPoint(left, top)) or QGuiApplication.primaryScreen()
            ratio = float(screen.devicePixelRatio()) if screen is not None else 1.0
            ratio = ratio if ratio > 0 else 1.0

            self.setGeometry(
                int(round(left / ratio)),
                int(round(top / ratio)),
                max(1, int(round(width / ratio))),
                max(1, int(round(height / ratio))),
            )
            if self._arrows and self._should_show_overlay():
                self.show()
                self.raise_()
            else:
                self.hide()
        except Exception as e:
            logger.error(f"同步箭头叠层几何失败: {e}")

    def _color_for(self, color: Tuple[int, int, int]) -> QColor:
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
        if not self._arrows:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        width = max(1, self.width())
        height = max(1, self.height())

        for spec in self._arrows:
            self._paint_arrow(painter, spec, width, height)

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
        head_len = spec.head_len_norm * min(width, height) if spec.head_len_norm is not None else max(10.0, length * self._arrow_head_len_ratio)
        head_len = max(8.0, min(head_len, min(width, height) * 0.18))

        ux = dx / length
        uy = dy / length
        theta = math.atan2(uy, ux)
        head_angle = math.radians(self._arrow_head_angle_deg)

        wing1 = QPointF(end_x + math.cos(theta + math.pi - head_angle) * head_len,
                        end_y + math.sin(theta + math.pi - head_angle) * head_len)
        wing2 = QPointF(end_x + math.cos(theta + math.pi + head_angle) * head_len,
                        end_y + math.sin(theta + math.pi + head_angle) * head_len)
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

            inner_band = QPolygonF([
                shrink_point(tip, inner_band_k),
                shrink_point(wing1, inner_band_k),
                shrink_point(wing2, inner_band_k),
            ])

            painter.setBrush(QBrush(QColor(255, 255, 255)))
            painter.setPen(Qt.NoPen)
            painter.drawPolygon(inner_band)

            # 再绘制更小的彩色中心三角，避免白色覆盖尖端颜色
            inner_center = QPolygonF([
                shrink_point(tip, inner_center_k),
                shrink_point(wing1, inner_center_k),
                shrink_point(wing2, inner_center_k),
            ])
            painter.setBrush(QBrush(color))
            painter.drawPolygon(inner_center)
        except Exception:
            # 若计算出错，不影响主要绘制
            pass

        # 用一个小圆点稳住起点，视觉上更像正常箭头起点
        painter.drawEllipse(QPointF(start_x, start_y), max(1.0, shaft_width * 0.35), max(1.0, shaft_width * 0.35))


class WindowArrowOverlayController(QObject):
    """把箭头更新切回 GUI 线程执行。"""

    arrows_replaced = Signal(object)
    arrow_updated = Signal(object)
    clear_requested = Signal()
    style_requested = Signal(tuple, float, float)

    def __init__(self, hwnd: int):
        super().__init__()
        self._hwnd = hwnd
        self._overlay: Optional[WindowArrowOverlay] = None
        self._arrow_map: Dict[str, ArrowSpec] = {}
        self.arrows_replaced.connect(self._on_arrows_replaced)
        self.arrow_updated.connect(self._on_arrow_updated)
        self.clear_requested.connect(self._on_clear_requested)
        self.style_requested.connect(self._on_style_requested)

    def _ensure_overlay(self) -> WindowArrowOverlay:
        if self._overlay is None:
            self._overlay = WindowArrowOverlay(self._hwnd)
        return self._overlay

    def _apply_arrow_state(self):
        overlay = self._ensure_overlay()
        overlay.set_arrows(list(self._arrow_map.values()))

    @Slot(object)
    def _on_arrows_replaced(self, arrows: List[ArrowSpec]):
        self._arrow_map = {}
        for spec in arrows:
            arrow_type = getattr(spec, 'arrow_type', None) or 'default'
            self._arrow_map[arrow_type] = spec
        self._apply_arrow_state()

    @Slot(object)
    def _on_arrow_updated(self, arrow: ArrowSpec):
        arrow_type = getattr(arrow, 'arrow_type', None) or 'default'
        self._arrow_map[arrow_type] = arrow
        self._apply_arrow_state()

    def _on_arrows_requested(self, arrows: List[ArrowSpec]):
        overlay = self._ensure_overlay()
        self._arrow_map = {}
        for spec in arrows:
            arrow_type = getattr(spec, 'arrow_type', None) or 'default'
            self._arrow_map[arrow_type] = spec
        overlay.set_arrows(list(self._arrow_map.values()))

    @Slot()
    def _on_clear_requested(self):
        if self._overlay is not None:
            self._overlay.clear_arrows()

    @Slot(tuple, float, float)
    def _on_style_requested(self, color: Tuple[int, int, int], head_angle_deg: float, head_len_ratio: float):
        overlay = self._ensure_overlay()
        overlay.set_style(color, head_angle_deg, head_len_ratio)


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
        self._window_arrow_overlay: Optional[WindowArrowOverlay] = None
        self._window_arrow_controller: Optional[WindowArrowOverlayController] = None

    def _get_game_hwnd(self) -> int:
        """返回游戏主窗口句柄，优先使用 self.hwnd.hwnd。"""
        hwnd = self.hwnd.hwnd if hasattr(self.hwnd, 'hwnd') else self.hwnd
        return int(hwnd)

    def _ensure_window_arrow_controller(self):
        if self._window_arrow_controller is not None:
            return self._window_arrow_controller

        app = getattr(self, 'app', None)
        if app is None:
            try:
                from PySide6.QtWidgets import QApplication
                app = QApplication.instance()
            except Exception:
                app = None

        if app is None:
            logger.error("无法创建箭头叠层：未找到 QApplication 实例")
            return None

        hwnd = self._get_game_hwnd()
        self._window_arrow_controller = WindowArrowOverlayController(hwnd)
        self._window_arrow_controller.moveToThread(app.thread())
        self._window_arrow_controller.style_requested.emit(
            self._window_arrow_color,
            self._window_arrow_head_angle_deg,
            self._window_arrow_head_len_ratio,
        )
        self._window_arrow_overlay = self._window_arrow_controller._overlay
        return self._window_arrow_controller

    def _get_window_arrow_size(self) -> Tuple[int, int]:
        """获取当前游戏窗口的客户区大小。"""
        try:
            overlay = self._window_arrow_overlay
            if overlay is not None and overlay.width() > 0 and overlay.height() > 0:
                return overlay.width(), overlay.height()

            hwnd = self.hwnd.hwnd if hasattr(self.hwnd, 'hwnd') else self.hwnd
            left, top, right, bottom = win32gui.GetClientRect(hwnd)
            return right - left, bottom - top
        except Exception as e:
            logger.error(f"获取窗口大小失败: {e}")
            return 0, 0

    def set_window_arrow_style(
        self,
        arrow_color: Optional[Tuple[int, int, int]] = None,
        shaft_width_norm: Optional[float] = None,
        head_angle_deg: Optional[float] = None,
        head_len_ratio: Optional[float] = None,
    ):
        """
        设置窗口箭头的全局样式。
        
        Args:
            arrow_color: 箭头颜色 (RGB)，例如 (0, 255, 0) 为绿色
            shaft_width_norm: 箭身宽度（归一化），默认 0.01
            head_angle_deg: 箭头头部张角（度）
            head_len_ratio: 箭头头部长度比例
        """
        if arrow_color is not None:
            self._window_arrow_color = arrow_color
        if shaft_width_norm is not None:
            self._window_arrow_shaft_width_norm = shaft_width_norm
        if head_angle_deg is not None:
            self._window_arrow_head_angle_deg = head_angle_deg
        if head_len_ratio is not None:
            self._window_arrow_head_len_ratio = head_len_ratio
        
        controller = self._ensure_window_arrow_controller()
        if controller is not None:
            controller.style_requested.emit(
                self._window_arrow_color,
                self._window_arrow_head_angle_deg,
                self._window_arrow_head_len_ratio,
            )

    def draw_window_arrow(
        self,
        start_x_norm: float,
        start_y_norm: float,
        end_x_norm: float,
        end_y_norm: float,
        shaft_width_norm: Optional[float] = None,
        head_len_norm: Optional[float] = None,
        color: Optional[Tuple[int, int, int]] = None,
        alpha: Optional[int] = None,
        arrow_type: str = 'default',
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
                    arrow_type=arrow_type or 'default',
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
        shaft_width_norm: Optional[float] = None,
        head_len_norm: Optional[float] = None,
        color: Optional[Tuple[int, int, int]] = None,
        alpha: Optional[int] = None,
        center_is_norm: bool = False,
        length_is_norm: bool = False,
        arrow_type: str = 'default',
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

    def draw_window_arrows(
        self,
        arrows: List[Dict],
        default_shaft_width_norm: Optional[float] = None,
    ) -> int:
        """
        在游戏窗口上绘制多个箭头。
        
        Args:
            arrows: 箭头列表，每个元素是字典：
                {
                    'start_x_norm': float,      # 必需
                    'start_y_norm': float,      # 必需
                    'end_x_norm': float,        # 必需
                    'end_y_norm': float,        # 必需
                    'shaft_width_norm': float,  # 可选
                    'color': tuple,             # 可选 (RGB)
                    'head_len_norm': float,     # 可选
                }
            default_shaft_width_norm: 默认的箭身宽度
            
        Returns:
            成功绘制的箭头数量
        """
        try:
            controller = self._ensure_window_arrow_controller()
            if controller is None:
                return 0

            arrow_specs = []
            for arrow in arrows:
                arrow_specs.append(
                    ArrowSpec(
                        arrow_type=arrow.get('arrow_type', 'default'),
                        start_x_norm=arrow.get('start_x_norm', 0.0),
                        start_y_norm=arrow.get('start_y_norm', 0.0),
                        end_x_norm=arrow.get('end_x_norm', 1.0),
                        end_y_norm=arrow.get('end_y_norm', 1.0),
                        color=arrow.get('color') or self._window_arrow_color,
                        shaft_width_norm=arrow.get('shaft_width_norm', default_shaft_width_norm or self._window_arrow_shaft_width_norm),
                        head_len_norm=arrow.get('head_len_norm'),
                    )
                )

            controller.arrows_replaced.emit(arrow_specs)
            return len(arrow_specs)
        except Exception as e:
            logger.error(f"绘制多个窗口箭头失败: {e}")
            return 0

    def clear_window_arrows(self):
        """清空窗口上的所有箭头。"""
        try:
            controller = self._ensure_window_arrow_controller()
            if controller is None:
                return
            controller.clear_requested.emit()
        except Exception as e:
            logger.error(f"清空窗口箭头失败: {e}")

    def get_window_arrow_size(self) -> Tuple[int, int]:
        """获取游戏窗口大小。"""
        overlay = self._window_arrow_overlay
        if overlay is not None:
            return overlay.width(), overlay.height()
        return self._get_window_arrow_size()

    def get_window_arrow_dpi_scale(self) -> float:
        """获取 DPI 缩放因子。"""
        width, height = self._get_window_arrow_size()
        min_dim = min(width, height)
        if min_dim > 1500:
            return 1.5
        if min_dim > 1000:
            return 1.2
        return 1.0
