"""任务卡悬停参数概要弹层（对齐 ok-script-toolkit 控制台行为）。

交互与定位规范来自设计文档
``ok-script-toolkit/.workbuddy/design/eye-popup-design.md`` 1.2 / 2.2 节：

- 触发：**悬停整张任务卡立即弹出**（无按钮），弹层**向右展开**
  （左缘贴卡右缘外 8px、顶对齐卡片）；空间不足先收窄（250 → 下限
  180px）；钳位用 ``QScreen.availableGeometry()``（弹层是独立顶级窗口，
  可画出主窗口边界）；连屏幕都放不下才允许少量压卡，**永不向左回退**。
- 窗口类型 Qt.ToolTip：不抢焦点、不抓鼠标——悬停展示期间卡片上的
  开关/下拉/展开按钮照常可点（对齐扩展里 div 浮层不拦截交互的行为）。
- 悬停保持：鼠标进弹层不消失（撤隐藏定时器），移走才收（120ms 延迟）。
- 滚动收起：宿主列表滚动 / 窗口缩放移动 / 任务刷新立即收；弹层自身
  内滚不收（弹层是独立顶级窗口，事件到不了宿主过滤器）。
- 卡片自身变形（展开配置卡）立即收起：位置过时，且说明用户开始操作。
- 切 tab 抑制：卡片隐藏（切分段）后 1.2s 内不弹。
- 快速移动防竞态：show 前先撤旧的隐藏定时器。
"""

from __future__ import annotations

import time
from collections import OrderedDict

from ok import Logger, og
from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPropertyAnimation,
    Qt,
    QTimer,
)
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
from qfluentwidgets import ScrollArea, isDarkTheme

from src.core.param_preview_model import build_param_preview

# 弹层几何（对齐扩展 .gpop：宽 250、收窄下限 180、边距 8）
POP_WIDTH = 250
POP_MIN_WIDTH = 180
POP_MARGIN = 8
POP_MAX_HEIGHT_RATIO = 0.66

# 悬停节奏：整卡悬停**立即弹出**（对齐扩展 mouseenter 即显）；
# 离开后 120ms 收起；切 tab 抑制 1.2s。SHOW_DELAY_MS=0 仍保留定时器，
# Enter+Leave 同批到达时定时器先启后停，天然防抖
SHOW_DELAY_MS = 0
HIDE_DELAY_MS = 120
SUPPRESS_MS = 1200

# 渲染缓存上限（按任务名 LRU）：悬停轮切时复用已建好的内容卡，
# 跳过 widget 重建与样式解析——这是轮切卡顿的主要来源
RENDER_CACHE_LIMIT = 8

logger = Logger.get_logger(__name__)
# TODO(diag): 临时诊断日志，弹层"位置乱跳"问题定位后整体删除（搜 diag 标记）

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
    """任务 → 弹层内容模型；无可展示内容返回 None（不装悬停弹出）。"""
    return build_param_preview(
        task.config,
        task.config_type,
        getattr(task, "default_config", None),
        getattr(task, "default_config_group", None),
        og.app.tr,
    )


def _task_fingerprint(task):
    """任务内容摘要：config/default/config_type/group 任一值或结构变化
    都会改变指纹 → 渲染缓存失效重建，保证弹层显示的不是过期值。"""
    parts = [str(getattr(og.app, "lang", "") or "")]
    for obj in (task.config, task.config_type,
                getattr(task, "default_config", None),
                getattr(task, "default_config_group", None)):
        if not obj:
            parts.append(())
            continue
        try:
            parts.append(tuple(sorted((k, repr(v)) for k, v in obj.items())))
        except Exception:
            parts.append((id(obj),))
    return tuple(parts)


class ParamPreviewPopup(QWidget):
    """frameless Qt.ToolTip 浮层：半透明圆角面板 + 限高内滚的分组概要。"""

    def __init__(self):
        # Qt.ToolTip：不激活、不抢焦点、不抓鼠标——悬停展示期间卡片控件
        # 照常可点（Qt.Popup 会抓走第一次点击，不适合悬停展示场景）
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(12, 12, 12, 14)

        self._panel = QFrame(self)
        self._panel.setObjectName("paramPreviewPanel")
        self._root_layout.addWidget(self._panel)

        shadow = QGraphicsDropShadowEffect(self)
        # blur 28 → 16：阴影重绘成本随半径平方增长，16 在视觉上几乎无差，
        # 但悬停轮切（每帧 move + opacity 动画）时明显更流畅
        shadow.setBlurRadius(16)
        shadow.setOffset(0, 4)
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

        # ── 渲染缓存：任务名 -> (指纹, 内容卡列表) ─────────────────
        # 布局上挂的卡永远属于 _active_key 任务；切走时 _stow_current_cards
        # 把它们取下存回缓存，命中时直接回挂（零 widget 创建）
        self._render_cache = OrderedDict()
        self._active_key = None
        self._active_fp = None
        self._style_key = None

        # ── 动效（对齐扩展 .gpop：160ms 淡入 + 4px 上浮，收起快速淡出）──
        self._show_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._show_anim.setDuration(160)
        self._show_anim.setStartValue(0.0)
        self._show_anim.setEndValue(1.0)
        self._show_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._slide_anim = QPropertyAnimation(self, b"pos", self)
        self._slide_anim.setDuration(160)
        self._slide_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._hide_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._hide_anim.setDuration(90)
        self._hide_anim.setStartValue(1.0)
        self._hide_anim.setEndValue(0.0)
        self._hide_anim.setEasingCurve(QEasingCurve.Type.InQuad)
        self._hide_anim.finished.connect(self._finish_hide)

    # ── 内容构建 ──────────────────────────────────────────────

    def show_for_task(self, card):
        """按任务卡定位并弹出；内容不可用时返回 False。"""
        # 先停掉所有在途动画并复位透明度：本方法可能在弹层已可见时被
        # 调用（悬停扫到另一张卡），在途的 slide 动画终点是旧卡位置，
        # 会把下面 _position 定好的新位置又拉回去（位置乱跳）
        self._cancel_animations()
        task = card.task
        key = task.name
        fp = _task_fingerprint(task)
        self._stow_current_cards()

        entry = self._render_cache.get(key)
        if entry is not None and entry[0] == fp:
            # 缓存命中：直接回挂已建好的内容卡，跳过全部 widget 重建
            self._render_cache.pop(key)
            for w in entry[1]:
                self._content_layout.addWidget(w)  # 自动 reparent 回 content
                w.show()
        else:
            preview = _build_preview(task)
            if preview is None:
                self._render_cache.pop(key, None)
                return False
            self._render_cache.pop(key, None)  # 指纹已过期，弃用旧卡
            for block in preview["blocks"]:
                group_card = self._make_group_card(block)
                # 弹层可见状态下重建的新卡片带 hidden 标志，QLayout 会把它当
                # 空项（item hint=0，content 高度量成 0 → 窗口塌成一行），
                # 必须先 show() 清标志再量高；父级隐藏时 show() 不会真显示
                group_card.show()
                self._content_layout.addWidget(group_card)
        self._active_key = key
        self._active_fp = fp

        self._apply_stylesheet_once()
        self._title_label.setText(og.app.tr(task.name))

        # ⚠️ _position 必须以任务卡定位——曾因循环变量与参数 card 撞名，
        # 把弹层内部内容卡传进来，其全局坐标由弹层自身位置决定，形成
        # 自反馈回路：弹层位置逐次向右漂移、最终被右缘钳位"停"在屏幕最右边
        self._position(card)
        return True

    def _stow_current_cards(self):
        """把布局上当前任务的内容卡取下存回渲染缓存（LRU 超限回收）。"""
        if self._active_key is None:
            return
        cards = []
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)  # 脱离弹层，隐藏待复用
                cards.append(w)
        self._render_cache[self._active_key] = (self._active_fp, cards)
        while len(self._render_cache) > RENDER_CACHE_LIMIT:
            _, (_, old_cards) = self._render_cache.popitem(last=False)
            for w in old_cards:
                w.deleteLater()
        self._active_key = None
        self._active_fp = None

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

    def _apply_stylesheet_once(self):
        """样式表只在主题切换时重设（setStyleSheet 每次都重新解析，
        悬停轮切时是纯浪费）。"""
        key = bool(isDarkTheme())
        if key == self._style_key:
            return
        self._style_key = key
        self._apply_stylesheet(_colors())

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
        center_global = card.mapToGlobal(card.rect().center())
        screen = QGuiApplication.screenAt(center_global)
        screen_fallback = screen is None
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()

        card_top_left = card.mapToGlobal(card.rect().topLeft())
        card_top_right = card.mapToGlobal(card.rect().topRight())
        preferred_left = card_top_right.x() + POP_MARGIN
        avail_width = avail.right() - preferred_left - POP_MARGIN
        if avail_width >= POP_MIN_WIDTH:
            width = max(POP_MIN_WIDTH, min(POP_WIDTH, avail_width))
            h_branch = "normal"
        else:
            # 连下限都放不下：允许少量压卡（悬停即走），仍在屏幕右缘内
            width = POP_MIN_WIDTH
            preferred_left = avail.right() - width - POP_MARGIN
            h_branch = "right-clamp"

        self.setFixedWidth(width + 24)  # 两侧阴影留白

        # 高度自适应：窗口高度 = 结构开销（margins/spacing/标题，皆为确定
        # 值）+ scroll 高度；scroll 高度 = 内容实际高度，超过屏幕预算则压
        # 回上限内滚。不依赖 sizeHint/adjustSize——QScrollArea 的 sizeHint
        # 与 widgetResizable 拉伸互相干扰，内容少时窗口被硬撑、布局把
        # 分组卡垂直分散填满
        rm = self._root_layout.contentsMargins()
        pm = self._panel_layout.contentsMargins()
        overhead = (rm.top() + rm.bottom() + pm.top() + pm.bottom()
                    + self._panel_layout.spacing()
                    + self._title_label.sizeHint().height())

        max_height = int(avail.height() * POP_MAX_HEIGHT_RATIO)
        content_h = self._content.sizeHint().height()
        scroll_h = min(max(1, content_h), max(60, max_height - overhead))
        self._scroll.setFixedHeight(scroll_h)
        self.setFixedHeight(scroll_h + overhead)

        left = preferred_left - 12  # 面板左缘 = 弹层窗口左缘 + 阴影留白
        top = card_top_right.y() - 12
        clamped_top = max(avail.top() + POP_MARGIN,
                          min(top, avail.bottom() - self.height() - POP_MARGIN))
        if clamped_top > top:
            v_branch = "top-clamp"
        elif clamped_top < top:
            v_branch = "bottom-clamp"
        else:
            v_branch = "normal"
        top = clamped_top
        self.move(left, top)

        # TODO(diag): 定位全链路日志——区分「输入坐标异常」与「计算/钳位异常」
        task = getattr(card, "task", None)
        window = card.window()
        # 注意：框架 Logger.debug(message) 只收单参数，不支持 %s 惰性格式化
        logger.debug(
            f"diag-position: task={getattr(task, 'name', '?')!r} "
            f"card_geom={card.geometry()} card_visible={card.isVisible()} "
            f"card_vis_to_win={card.isVisibleTo(window) if window is not None else None} "
            f"card_tl_global=({card_top_left.x()},{card_top_left.y()}) "
            f"card_tr_global=({card_top_right.x()},{card_top_right.y()}) "
            f"card_center_global=({center_global.x()},{center_global.y()}) "
            f"popup_geom={self.geometry()} "
            f"window_geom={window.geometry() if window is not None else None} "
            f"screen_geom={screen.geometry()} avail={avail} "
            f"screen_fallback={screen_fallback} h_branch={h_branch} "
            f"v_branch={v_branch} preferred_left={preferred_left} "
            f"avail_width={avail_width} width={width} content_h={content_h} "
            f"top_raw={card_top_right.y() - 12} final=({left},{top}) "
            f"pos_after_move=({self.x()},{self.y()})")

    # ── 悬停保持 ─────────────────────────────────────────────

    def _cancel_animations(self):
        """停掉全部在途动效并复位为全显（切换目标卡前调用）。"""
        self._show_anim.stop()
        self._slide_anim.stop()
        self._hide_anim.stop()
        self.setWindowOpacity(1.0)

    def showEvent(self, event):
        pos_in_show = self.pos()  # TODO(diag): 与 position 设置值对比
        super().showEvent(event)
        # 每次弹出：透明度 0→1 + 从下方 4px 上浮到位（扩展 cubic-bezier 回弹
        # 在 Qt 里用 OutCubic 近似，4px 幅度下观感一致）
        self._hide_anim.stop()
        self._slide_anim.stop()
        self._show_anim.stop()
        self.setWindowOpacity(0.0)
        target = self.pos()
        self._slide_anim.setStartValue(target + QPoint(0, 4))
        self._slide_anim.setEndValue(target)
        self._show_anim.start()
        self._slide_anim.start()
        # TODO(diag): pos_in_show ≠ anim_target 说明 show 时窗口位置被重置过
        logger.debug(
            f"diag-show: pos_in_showEvent=({pos_in_show.x()},{pos_in_show.y()}) "
            f"anim_target=({target.x()},{target.y()}) "
            f"geometry={self.geometry()} opacity={self.windowOpacity():.2f}")

    def hide_animated(self):
        """收起动效：90ms 淡出后真正隐藏；不可见时直接返回。"""
        if not self.isVisible():
            return
        self._show_anim.stop()
        self._slide_anim.stop()
        self._hide_anim.stop()
        self._hide_anim.start()

    def _finish_hide(self):
        self.hide()
        self.setWindowOpacity(1.0)  # 复位，下次弹出从头淡入

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
        if not popup.show_for_task(card):
            # 新卡无内容：Enter 已撤掉旧卡的收起定时器，弹层若继续挂着
            # 上一张卡的内容会一直滞留到离开新卡为止，这里立刻收走
            if popup.isVisible():
                popup.hide_animated()
                cls._owner = None
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
            # 定时器挂在控制器类属性上（_ensure_popup 里创建），不在弹层实例上
            cls._ensure_popup()
            if cls._hide_timer is not None:
                cls._hide_timer.start()

    @classmethod
    def cancel_hide(cls):
        if cls._hide_timer is not None:
            cls._hide_timer.stop()

    @classmethod
    def _hide_now(cls):
        if cls._popup is not None and cls._popup.isVisible():
            cls._popup.hide_animated()
        cls._owner = None


class _CardHoverFilter(QObject):
    """整卡悬停过滤：Enter 延迟弹出、Leave 延迟收起、切 tab 抑制、
    宿主滚动/主窗口变化/卡片变形立即收起（对齐扩展 showHoverPop 语义）。"""

    def __init__(self, card):
        super().__init__(None)
        self._card = card
        self._show_timer = QTimer(self)
        self._show_timer.setSingleShot(True)
        self._show_timer.setInterval(SHOW_DELAY_MS)
        self._show_timer.timeout.connect(self._show_popup)
        self._suppress_until = 0.0

    def _show_popup(self):
        if time.monotonic() < self._suppress_until:
            return
        ParamPreviewController.show_for(self._card)

    def note_tab_switch(self):
        """卡片被隐藏（切分段）：收弹层 + 1.2s 内不再弹。"""
        self._suppress_until = time.monotonic() + SUPPRESS_MS / 1000.0
        ParamPreviewController.hide()

    def eventFilter(self, obj, event):
        # 事件过滤器里抛异常会在 Qt 过滤链上级联放大（曾把启动打断），
        # 这里兜底吞掉：弹层是锦上添花，不能影响宿主
        try:
            etype = event.type()
            if obj is self._card:
                if etype == QEvent.Enter:
                    ParamPreviewController.cancel_hide()
                    self._show_timer.start()
                elif etype == QEvent.Leave:
                    self._show_timer.stop()
                    ParamPreviewController.schedule_hide()
                elif etype == QEvent.Hide:
                    self.note_tab_switch()
                elif etype == QEvent.DeferredDelete:
                    # Qt6/PySide6 没有 QEvent.Destroyed 枚举，别再加
                    ParamPreviewController.hide()
                elif etype == QEvent.Resize:
                    # 卡片自身变形（如展开配置卡）→ 弹层位置过时，收起
                    ParamPreviewController.hide()
            elif etype in (QEvent.Wheel, QEvent.Resize, QEvent.Move):
                # 宿主列表滚动 / 主窗口缩放移动 → 立即收；弹层自身是独立
                # 顶级窗口，事件到不了宿主过滤器 → 弹层内滚不收
                ParamPreviewController.hide()
        except Exception:
            pass
        return False


def install_param_preview_hover(card):
    """给任务卡装悬停弹出：悬停整卡立即弹出层、向右展开（无按钮）。"""
    if getattr(card, "_param_preview_hover", None) is not None:
        return
    if _build_preview(card.task) is None:
        return  # 无组、无规则、无字段 → 不装

    hover = _CardHoverFilter(card)
    card._param_preview_hover = hover
    card.installEventFilter(hover)
    # 宿主列表滚动（Wheel）/ 主窗口缩放移动 → 立即收起弹层
    parent = card.parentWidget()
    while parent is not None:
        if isinstance(parent, QAbstractScrollArea):
            parent.viewport().installEventFilter(hover)
            break
        parent = parent.parentWidget()
    window = card.window()
    if window is not None:
        window.installEventFilter(hover)
