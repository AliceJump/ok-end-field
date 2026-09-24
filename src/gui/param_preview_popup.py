"""任务卡「小眼睛」参数概要弹层（Qt.Popup 浮层）。

交互与定位规范来自设计文档
``ok-script-toolkit/.workbuddy/design/eye-popup-design.md`` 1.2 / 2.2 节：

- 展开方向：眼睛在卡头最右侧，弹层**向右展开**（左缘贴卡右缘外 8px、
  顶对齐卡片）；空间不足先收窄（250 → 下限 180px）；钳位用
  ``QScreen.availableGeometry()``（弹层是独立顶级窗口，可画出主窗口
  边界）；连屏幕都放不下才允许少量压卡，**永不向左回退**。
- 悬停保持：鼠标进弹层不消失（撤隐藏定时器），移走才收（120ms 延迟）。
- 滚动收起：宿主列表滚动 / 窗口缩放移动 / 任务刷新立即收；弹层自身
  内滚不收（弹层是独立顶级窗口，事件到不了宿主过滤器）。
- 切 tab 抑制：卡片隐藏（切分段）后 1.2s 内不弹。
- 快速移动防竞态：show 前先撤旧的隐藏定时器。
"""

from __future__ import annotations

import time

from ok import og
from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import FluentIcon, ScrollArea, ToolButton, isDarkTheme

from src.core.param_preview_model import build_param_preview

# 弹层几何（对齐扩展 .gpop：宽 250、收窄下限 180、边距 8）
POP_WIDTH = 250
POP_MIN_WIDTH = 180
POP_MARGIN = 8
POP_MAX_HEIGHT_RATIO = 0.66

# 悬停节奏：卡头悬停 500ms 弹出；离开后 120ms 收起；切 tab 抑制 1.2s
SHOW_DELAY_MS = 500
HIDE_DELAY_MS = 120
SUPPRESS_MS = 1200

# 点击眼睛后的重开保护窗口（Qt.Popup 在点击外部时会先关闭弹层，
# 同一次按压会继续派发到眼睛，靠时间戳避免「点一下又立刻弹回」）
RECLICK_GUARD_S = 0.25

_LIGHT = {
    "panel_bg": "rgba(250, 250, 250, 244)",
    "panel_border": "#e2e2e2",
    "text": "#1f1f1f",
    "secondary": "#8a8a8a",
    "group_border": "#e6e6e6",
    "cond_border": "#bcd7f7",
    "cond_title": "#1668dc",
    "badge_bg": "#e8f1ff",
    "badge_text": "#1668dc",
}
_DARK = {
    "panel_bg": "rgba(44, 44, 44, 244)",
    "panel_border": "#3d3d3d",
    "text": "#ececec",
    "secondary": "#9a9a9a",
    "group_border": "#424242",
    "cond_border": "#1e4e80",
    "cond_title": "#5ca7ff",
    "badge_bg": "rgba(30, 78, 128, 0.45)",
    "badge_text": "#8ec4ff",
}


def _colors():
    return _DARK if isDarkTheme() else _LIGHT


def _build_preview(task):
    """任务 → 弹层内容模型；无可展示内容返回 None（不显示眼睛）。"""
    return build_param_preview(
        task.config,
        task.config_type,
        getattr(task, "default_config", None),
        getattr(task, "default_config_group", None),
        og.app.tr,
    )


class ParamPreviewPopup(QWidget):
    """frameless Qt.Popup 浮层：半透明圆角面板 + 限高内滚的分组概要。"""

    def __init__(self):
        super().__init__(None, Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(12, 12, 12, 14)

        self._panel = QFrame(self)
        self._panel.setObjectName("paramPreviewPanel")
        self._root_layout.addWidget(self._panel)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 5)
        from PySide6.QtGui import QColor

        shadow.setColor(QColor(0, 0, 0, 90))
        self._panel.setGraphicsEffect(shadow)

        self._panel_layout = QVBoxLayout(self._panel)
        self._panel_layout.setContentsMargins(12, 10, 12, 10)
        self._panel_layout.setSpacing(8)

        self._title_label = QLabel(self._panel)
        self._title_label.setObjectName("paramPreviewTitle")
        self._title_label.setWordWrap(False)
        self._panel_layout.addWidget(self._title_label)

        self._scroll = ScrollArea(self._panel)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._content = QWidget(self._scroll)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(8)
        self._scroll.setWidget(self._content)
        self._panel_layout.addWidget(self._scroll)

    # ── 内容构建 ──────────────────────────────────────────────

    def show_for_task(self, card):
        """按任务卡定位并弹出；内容不可用时返回 False。"""
        task = card.task
        preview = _build_preview(task)
        if preview is None:
            return False

        colors = _colors()
        self._apply_stylesheet(colors)
        self._title_label.setText(og.app.tr(task.name))

        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for block in preview["blocks"]:
            self._content_layout.addWidget(self._make_group_card(block))

        self._position(card)
        return True

    def _make_group_card(self, block):
        """一个组块 = 一张圆角边框卡（静态组 / 条件组 / 其他参数）。"""
        is_cond = block["type"] == "cond"
        card = QFrame(self._content)
        card.setObjectName("paramPreviewGroupCardCond" if is_cond
                           else "paramPreviewGroupCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setSpacing(6)
        name = QLabel(block["name"], card)
        name.setObjectName("paramPreviewGroupName")
        header.addWidget(name)
        if is_cond:
            tag = QLabel(block.get("tag", ""), card)
            tag.setObjectName("paramPreviewBadge")
            header.addWidget(tag)
        else:
            count = QLabel(og.app.tr("{n} 项").format(n=block["count"]), card)
            count.setObjectName("paramPreviewCount")
            header.addWidget(count)
        header.addStretch(1)
        layout.addLayout(header)

        if is_cond:
            for rule in block["rules"]:
                layout.addLayout(self._make_rule_row(rule, card))
                layout.addLayout(self._make_nodes(rule["nodes"], indent=12))
        else:
            layout.addLayout(self._make_nodes(block["nodes"], indent=0))
        return card

    def _make_rule_row(self, rule, parent):
        """规则行：值 → {n} 项。"""
        row = QHBoxLayout()
        row.setSpacing(6)
        value = QLabel(str(rule["label"]), parent)
        value.setObjectName("paramPreviewRuleValue")
        row.addWidget(value)
        count = QLabel(og.app.tr("{n} 项").format(n=rule["count"]), parent)
        count.setObjectName("paramPreviewCount")
        row.addWidget(count)
        row.addStretch(1)
        return row

    def _make_nodes(self, nodes, indent):
        """字段行列表：普通行 + 拍平徽标行（徽标可换行）。"""
        container = QVBoxLayout()
        container.setContentsMargins(indent, 0, 0, 0)
        container.setSpacing(2)
        for node in nodes:
            if node["type"] == "item":
                row = QHBoxLayout()
                row.setSpacing(6)
                label = QLabel(node["label"], self._content)
                label.setObjectName("paramPreviewItem")
                row.addWidget(label)
                if node.get("badge"):
                    badge = QLabel(node["badge"], self._content)
                    badge.setObjectName("paramPreviewBadge")
                    badge.setWordWrap(True)
                    row.addWidget(badge, 1)
                else:
                    row.addStretch(1)
                container.addLayout(row)
            elif node["type"] in ("cond", "static"):
                # 嵌套子组（depth < cap）：同款组卡
                container.addWidget(self._make_group_card(node))
        return container

    def _apply_stylesheet(self, c):
        self._panel.setStyleSheet(f"""
            QFrame#paramPreviewPanel {{
                background-color: {c['panel_bg']};
                border: 1px solid {c['panel_border']};
                border-radius: 12px;
            }}
            QFrame#paramPreviewGroupCard {{
                background-color: transparent;
                border: 1px solid {c['group_border']};
                border-radius: 8px;
            }}
            QFrame#paramPreviewGroupCardCond {{
                background-color: transparent;
                border: 1px solid {c['cond_border']};
                border-radius: 8px;
            }}
            QLabel#paramPreviewTitle {{
                color: {c['text']};
                font-weight: 600;
                font-size: 13px;
            }}
            QLabel#paramPreviewGroupName {{
                color: {c['text']};
                font-weight: 600;
                font-size: 12px;
            }}
            QLabel#paramPreviewItem {{
                color: {c['text']};
                font-size: 12px;
            }}
            QLabel#paramPreviewRuleValue {{
                color: {c['cond_title']};
                font-size: 12px;
            }}
            QLabel#paramPreviewCount {{
                color: {c['secondary']};
                font-size: 11px;
            }}
            QLabel#paramPreviewBadge {{
                color: {c['badge_text']};
                background-color: {c['badge_bg']};
                border-radius: 6px;
                padding: 1px 6px;
                font-size: 11px;
            }}
            QScrollArea {{
                background: transparent;
                border: none;
            }}
            QScrollArea > QWidget > QWidget {{
                background: transparent;
            }}
        """)

    # ── 定位 ─────────────────────────────────────────────────

    def _position(self, card):
        """左缘贴卡右缘外 8px、顶对齐卡片；屏幕几何钳位，永不向左回退。"""
        screen = QGuiApplication.screenAt(card.mapToGlobal(card.rect().center()))
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()

        card_top_right = card.mapToGlobal(card.rect().topRight())
        preferred_left = card_top_right.x() + POP_MARGIN
        avail_width = avail.right() - preferred_left - POP_MARGIN
        if avail_width >= POP_MIN_WIDTH:
            width = max(POP_MIN_WIDTH, min(POP_WIDTH, avail_width))
        else:
            # 连下限都放不下：允许少量压卡（悬停即走），仍在屏幕右缘内
            width = POP_MIN_WIDTH
            preferred_left = avail.right() - width - POP_MARGIN

        self.setFixedWidth(width + 24)  # 两侧阴影留白
        self.adjustSize()

        max_height = int(avail.height() * POP_MAX_HEIGHT_RATIO)
        if self.height() > max_height:
            overhead = self.height() - self._scroll.height()
            self._scroll.setFixedHeight(max(60, max_height - overhead))
            self.adjustSize()

        left = preferred_left - 12  # 面板左缘 = 弹层窗口左缘 + 阴影留白
        top = card_top_right.y() - 12
        top = max(avail.top() + POP_MARGIN,
                  min(top, avail.bottom() - self.height() - POP_MARGIN))
        self.move(left, top)

    # ── 悬停保持 ─────────────────────────────────────────────

    def enterEvent(self, event):
        ParamPreviewController.cancel_hide()
        super().enterEvent(event)

    def leaveEvent(self, event):
        ParamPreviewController.schedule_hide()
        super().leaveEvent(event)


class ParamPreviewController:
    """弹层单例管理：全局同时只弹一个，收起节奏统一调度。"""

    _popup: ParamPreviewPopup | None = None
    _owner = None
    _hide_timer: QTimer | None = None
    _last_closed_at = 0.0
    _last_owner = None

    @classmethod
    def _ensure_popup(cls):
        if cls._popup is None:
            cls._popup = ParamPreviewPopup()
            cls._hide_timer = QTimer()
            cls._hide_timer.setSingleShot(True)
            cls._hide_timer.setInterval(HIDE_DELAY_MS)
            cls._hide_timer.timeout.connect(cls._hide_now)
        return cls._popup

    @classmethod
    def show_for(cls, card):
        """显示/切换弹层（快速移动防竞态：先撤旧隐藏定时器）。"""
        cls.cancel_hide()
        popup = cls._ensure_popup()
        if cls._owner is card and popup.isVisible():
            cls._hide_now()
            return
        if not popup.show_for_task(card):
            return
        cls._owner = card
        popup.show()

    @classmethod
    def hide(cls):
        """立即收起（滚动/缩放/任务刷新/切 tab 用）。"""
        cls.cancel_hide()
        cls._hide_now()

    @classmethod
    def schedule_hide(cls):
        if cls._popup is not None and cls._popup.isVisible():
            cls._ensure_popup()._hide_timer.start()

    @classmethod
    def cancel_hide(cls):
        if cls._hide_timer is not None:
            cls._hide_timer.stop()

    @classmethod
    def recently_closed_for(cls, card):
        """弹层刚因点击而关闭（Qt.Popup 点外自动关）→ 忽略同一次点击。"""
        return (cls._last_owner is card
                and time.monotonic() - cls._last_closed_at < RECLICK_GUARD_S)

    @classmethod
    def _hide_now(cls):
        if cls._popup is not None and cls._popup.isVisible():
            cls._last_closed_at = time.monotonic()
            cls._last_owner = cls._owner
            cls._popup.hide()
        cls._owner = None


class _EyeButton(ToolButton):
    """卡头「小眼睛」：悬停 500ms 或点击 → 弹出参数概要，向右展开。"""

    def __init__(self, card, parent=None):
        # 不能用 super().__init__(FluentIcon.VIEW, parent)：ToolButton.__init__
        # 是 singledispatchmethod，FluentIconBase 分支会回调 self.__init__(parent)，
        # 而实例上的 self.__init__ 是本类的 __init__，会无限递归（启动即 RecursionError）
        super().__init__(parent)
        self.setIcon(FluentIcon.VIEW)
        self._card = card
        self.setToolTip(og.app.tr("参数预览"))
        self._show_timer = QTimer(self)
        self._show_timer.setSingleShot(True)
        self._show_timer.setInterval(SHOW_DELAY_MS)
        self._show_timer.timeout.connect(self._show_popup)
        self._suppress_until = 0.0
        self._event_filter = _CardEventFilter(self, card)
        card.installEventFilter(self._event_filter)
        self._install_host_filters(card)

    # ── 弹出控制 ─────────────────────────────────────────────

    def _show_popup(self):
        if time.monotonic() < self._suppress_until:
            return
        ParamPreviewController.show_for(self._card)

    def note_tab_switch(self):
        """卡片被隐藏（切分段）：收弹层 + 1.2s 内不再弹。"""
        self._suppress_until = time.monotonic() + SUPPRESS_MS / 1000.0
        ParamPreviewController.hide()

    def _install_host_filters(self, card):
        """宿主列表滚动（Wheel）/ 主窗口缩放移动 → 立即收起弹层。"""
        parent = card.parentWidget()
        while parent is not None:
            if isinstance(parent, QAbstractScrollArea):
                parent.viewport().installEventFilter(self._event_filter)
                break
            parent = parent.parentWidget()
        window = card.window()
        if window is not None:
            window.installEventFilter(self._event_filter)

    # ── 眼睛自身交互 ─────────────────────────────────────────

    def enterEvent(self, event):
        ParamPreviewController.cancel_hide()
        self._show_timer.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._show_timer.stop()
        ParamPreviewController.schedule_hide()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self._show_timer.stop()
        if not ParamPreviewController.recently_closed_for(self._card):
            ParamPreviewController.show_for(self._card)
        super().mousePressEvent(event)


class _CardEventFilter(QObject):
    """卡片与宿主的事件过滤：切 tab 抑制；宿主滚动/窗口变化立即收起。"""

    def __init__(self, eye, card):
        super().__init__(eye)
        self._eye = eye
        self._card = card

    def eventFilter(self, obj, event):
        # 事件过滤器里抛异常会在 Qt 过滤链上级联放大（曾把启动打断），
        # 这里兜底吞掉：弹层是锦上添花，不能影响宿主
        try:
            etype = event.type()
            if obj is self._card:
                if etype == QEvent.Hide:
                    self._eye.note_tab_switch()
                elif etype == QEvent.DeferredDelete:
                    # Qt6/PySide6 没有 QEvent.Destroyed 枚举，别再加
                    ParamPreviewController.hide()
            elif etype in (QEvent.Wheel, QEvent.Resize, QEvent.Move):
                # 弹层自身是独立顶级窗口，事件不会到达宿主过滤器 → 内滚不收
                ParamPreviewController.hide()
        except Exception:
            pass
        return False


def inject_param_preview_eye(card):
    """给 TaskCard 卡头最右侧（expandButton 前）注入小眼睛。"""
    if getattr(card, "_param_preview_eye", None) is not None:
        return
    if _build_preview(card.task) is None:
        return  # 无组、无规则、无字段 → 不显示眼睛

    eye = _EyeButton(card, card)
    card._param_preview_eye = eye
    layout = card.card.hBoxLayout
    index = layout.indexOf(card.card.expandButton)
    if index < 0:
        layout.addWidget(eye, 0, Qt.AlignRight)
    else:
        layout.insertWidget(index, eye, 0, Qt.AlignRight)
