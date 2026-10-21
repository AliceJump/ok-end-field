"""Shared entry geometry for native task, settings and data panels."""

from PySide6.QtCore import QEvent, QObject, Qt, QTimer  # type: ignore
from PySide6.QtGui import QFont  # type: ignore
from PySide6.QtWidgets import (  # type: ignore
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import SettingCardGroup, Theme, qconfig  # type: ignore

from src.gui.EndfieldPrimitives import EndfieldConfigButton, EndfieldElidedLabel, EndfieldTag, attach_panel_tab, ui_tr
from src.gui.EndfieldTheme import palette
from src.gui.EndfieldTypography import field_font, ui_font

ENGLISH_TITLES = {
    "日常一条龙": "DAILY ROUTINE",
    "业务与战斗": "OPERATIONS & COMBAT",
    "自动化调度": "AUTOMATION SCHEDULING",
    "系统与环境": "SYSTEM & ENVIRONMENT",
    "开发者工具": "DEVELOPER TOOLS",
    "大世界": "OPEN WORLD",
    "自动战斗": "AUTO COMBAT",
    "副本": "DUNGEONS",
    "其他": "OTHER TASKS",
    "截图方式": "CAPTURE METHOD",
    "全局配置": "GLOBAL CONFIGURATION",
    "账号配置": "ACCOUNT CONFIGURATION",
    "通知": "NOTIFICATIONS",
    "设置": "SETTINGS",
    "关于": "ABOUT",
    "计划": "SCHEDULE",
    "计划任务": "SCHEDULED TASKS",
    "脚本": "SCRIPTS",
    "模板": "TEMPLATES",
    "调试": "DEBUG",
    "工具与调试": "TOOLS & DEBUGGING",
    "运行代码": "RUN CODE",
}


def display_title(title: str) -> str:
    """Remove decorative prefixes from labels while retaining their storage keys."""
    return title.lstrip("⭐🌟★\ufe0f ").strip()


def english_title(title: str) -> str:
    return ENGLISH_TITLES.get(title, title.upper() if title.isascii() else "")


class EndfieldSectionBanner(QFrame):
    """The same functional section heading on daily and native pages."""

    def __init__(self, number, title, code, parent=None, *, panel_tab=True):
        super().__init__(parent)
        self.setObjectName("compositeBanner")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(20)
        titles = QVBoxLayout()
        titles.setSpacing(3)
        code_text = f" // {code}" if code else ""
        eyebrow = QLabel(f"// SECTION {number}{code_text}", self)
        eyebrow.setObjectName("bannerCode")
        eyebrow.setFont(field_font(11, label=True))
        name = QLabel(title, self)
        name.setObjectName("bannerTitle")
        name.setFont(ui_font(20, QFont.Black))
        if panel_tab:
            attach_panel_tab(self)
        titles.addWidget(eyebrow)
        titles.addWidget(name)
        layout.addLayout(titles, 1)
        self.count_label = QLabel(self)
        self.count_label.setObjectName("bannerDesc")
        self.count_label.setFont(ui_font(12))
        self.count_label.hide()
        layout.addWidget(self.count_label)
        self.setFixedHeight(self.sizeHint().height())


def add_entry_index(owner, header, layout):
    label = getattr(header, "_endfield_entry_index", None)
    if label is None:
        # 与日常页勾选行同一块墨色号牌
        label = EndfieldTag("00", header, ink=True)
        label.setObjectName("checkpointIdx")
        label.hide()
        layout.insertWidget(0, label)
        header._endfield_entry_index = label
    owner._endfield_entry_index = label


def _join_entry(card, joined):
    """同一行容器里紧接上一行的条目不画顶线，由上一行的底线充当分隔线。"""
    for widget in (card, getattr(card, "card", None)):
        if isinstance(widget, QWidget) and getattr(widget, "_endfield_join_top", False) != joined:
            widget._endfield_join_top = joined
            widget.update()


class EndfieldEntryNumbering(QObject):
    """Number visible entry cards in layout order, including rebuilt cards."""

    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.refresh)
        for target in (page, getattr(page, "view", None), getattr(page, "task_cards_view", None)):
            if target is not None:
                target.installEventFilter(self)
        self.timer.start(0)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Show, QEvent.Hide, QEvent.LayoutRequest, QEvent.ChildAdded, QEvent.ChildRemoved):
            self.timer.start(0)
        return super().eventFilter(obj, event)

    def refresh(self):
        entries = []
        # 面板之间留 12px；同组条目贴在一起，靠细线分隔
        for attr, spacing in (("vBoxLayout", 12), ("taskCardLayout", 0)):
            layout = getattr(self.page, attr, None)
            if layout is not None and layout.spacing() != spacing:
                layout.setSpacing(spacing)
        row_views = {getattr(self.page, "task_cards_view", None)}

        def visit_widget(widget):
            widget.installEventFilter(self)
            if widget.isHidden():
                return
            label = getattr(widget, "_endfield_entry_index", None)
            if label is not None:
                entries.append((widget, label))
                return
            if isinstance(widget, SettingCardGroup):
                row_views.add(widget)
                if widget.cardLayout.spacing() != 0:
                    widget.cardLayout.setSpacing(0)
                    widget.adjustSize()
                # ExpandLayout does not expose its cards through QLayout.itemAt().
                cards = [
                    child
                    for child in widget.findChildren(QWidget, options=Qt.FindDirectChildrenOnly)
                    if hasattr(child, "_endfield_entry_index")
                ]
                for card in sorted(cards, key=lambda child: child.y()):
                    visit_widget(card)
            elif isinstance(widget, QScrollArea):
                content = widget.widget()
                if content is not None:
                    visit_widget(content)
            else:
                visit_layout(QWidget.layout(widget))

        def visit_layout(layout):
            if layout is None:
                return
            for index in range(layout.count()):
                item = layout.itemAt(index)
                if item.widget() is not None:
                    visit_widget(item.widget())
                elif item.layout() is not None:
                    visit_layout(item.layout())

        visit_layout(getattr(self.page, "vBoxLayout", None) or QWidget.layout(self.page))
        previous = None
        for index, (card, label) in enumerate(entries, 1):
            text = f"{index:02d}"
            if label.text() != text:
                label.set_text(text)
            label.show()
            parent = card.parentWidget()
            _join_entry(card, parent in row_views and previous is not None and previous.parentWidget() is parent)
            previous = card


def style_setting_header(header):
    """Align numbered setting titles and descriptions without decorative icons."""
    layout = header.hBoxLayout
    add_entry_index(header, header, layout)
    layout.setContentsMargins(12, 0, 12, 0)
    layout.setSpacing(10)
    if header.__class__.__name__ not in ("ProjectCard", "StartCard"):
        header.iconLabel.hide()
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item.layout() is header.vBoxLayout:
            layout.setStretch(index, 1)
        elif item.spacerItem() is not None:
            item.spacerItem().changeSize(0, 0, QSizePolicy.Fixed, QSizePolicy.Minimum)
    # 普通 SettingCard 的标题没有对象名，卡片自带的 QLabel 字体规则会把它压回常规字重
    header.titleLabel.setObjectName("titleLabel")
    header.titleLabel.setFont(ui_font(14, QFont.DemiBold))
    header.contentLabel.setFont(ui_font(12))
    if not hasattr(header, "_endfield_text_row") and header.__class__.__name__ not in ("ProjectCard", "StartCard"):
        text_row = QHBoxLayout()
        text_row.setContentsMargins(0, 0, 0, 0)
        text_row.setSpacing(8)
        for attr, stretch in (("titleLabel", 0), ("contentLabel", 1)):
            original = getattr(header, attr)
            label = EndfieldElidedLabel(original.text(), header)
            label.setObjectName(original.objectName())
            label.setFont(original.font())
            label.setToolTip(original.text())
            label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
            label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            header.vBoxLayout.removeWidget(original)
            text_row.addWidget(label, stretch)
            label.setVisible(not original.isHidden())
            original.hide()
            original.deleteLater()
            setattr(header, attr, label)
        header.vBoxLayout.addLayout(text_row)
        header._endfield_text_row = text_row


def remove_page_decorations(page):
    """Restore original page headings without deleting any native controls."""
    for label in page.findChildren(EndfieldTag, "checkpointIdx"):
        label.hide()
    for header in page.findChildren(QFrame, "EndfieldPanelHeader"):
        panel = header.parentWidget()
        header.layout().removeItem(panel.title_layout)
        panel.vBoxLayout.removeWidget(header)
        panel.vBoxLayout.insertLayout(0, panel.title_layout)
        panel.vBoxLayout.setSpacing(8)
        header.hide()
        header.deleteLater()
        attach_panel_tab(panel.card)


def style_expand_header(card):
    header = card.card
    style_setting_header(header)
    card._endfield_entry_index = header._endfield_entry_index
    if not isinstance(header.expandButton, EndfieldConfigButton):
        original = header.expandButton
        button = EndfieldConfigButton(header)
        header.hBoxLayout.replaceWidget(original, button)
        header.expandButton = button
        original.hide()
        original.deleteLater()
        button.clicked.connect(card.toggleExpand)
    header.expandButton.setExpand(card.isExpand)
    header.setFixedHeight(56)
    style_setting_header(header)
    card.setViewportMargins(0, 56, 0, 0)
    card._adjustViewSize()


def style_panel_title(panel):
    """Frame the real title; preserve the content layout and its row geometry."""
    if panel.title_layout is None:
        return
    header = QFrame(panel)
    header.setObjectName("EndfieldPanelHeader")
    header.setMinimumHeight(56)
    layout = QHBoxLayout(header)
    layout.setContentsMargins(12, 0, 12, 0)
    layout.setSpacing(10)
    add_entry_index(panel, header, layout)
    panel.vBoxLayout.removeItem(panel.title_layout)
    layout.addLayout(panel.title_layout)
    panel.vBoxLayout.insertWidget(0, header)
    panel.vBoxLayout.setSpacing(0)
    panel.titleLabel.setText(display_title(panel.titleLabel.text()))
    panel.titleLabel.setFont(ui_font(14, QFont.DemiBold))
    attach_panel_tab(header)


# 状态只用墨色与信号黄区分：等待是虚线框，运行是实线框，需要处理是黄块
STATUS_STYLES = {
    "waiting": ("muted", "transparent", "dashed", "muted"),
    "running": ("text", "transparent", "solid", "text"),
    "attention": ("on_signal", "signal", "solid", "signal"),
}


class EndfieldTaskStatus(QLabel):
    """Keep an existing task status readable when the theme changes."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("taskStatus")
        self.role = "waiting"
        qconfig.themeChanged.connect(self.apply_theme)

    def apply_theme(self, *_args):
        colors = palette(qconfig.theme == Theme.DARK)
        text, fill, line, edge = STATUS_STYLES[self.role]
        background = "transparent" if fill == "transparent" else colors[fill]
        self.setStyleSheet(
            f"color: {colors[text]}; background: {background};border: 1px {line} {colors[edge]}; padding: 2px 6px;"
        )


def update_task_status(card):
    if not hasattr(card, "_endfield_status"):
        card._endfield_status = EndfieldTaskStatus(card.card)
        card.card.addWidget(card._endfield_status)
    task = card.task
    if getattr(task, "paused", False):
        text, role = "已暂停", "attention"
    elif getattr(task, "running", False):
        text, role = "运行中", "running"
    elif card.onetime and getattr(task, "enabled", False):
        text, role = "等待执行", "waiting"
    else:
        card._endfield_status.hide()
        return
    card._endfield_status.setText(ui_tr(text))
    card._endfield_status.role = role
    card._endfield_status.apply_theme()
    card._endfield_status.show()
