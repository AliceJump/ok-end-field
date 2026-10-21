# Endfield Checkpoint Card (终末地工业统一勾选点组件)
# Checkpoint with three-state style, dynamic strategy tags & drawer trigger

from typing import Any

from ok import Logger
from PySide6.QtCore import Qt, Signal  # type: ignore
from PySide6.QtGui import QColor, QFont, QPainter  # type: ignore
from PySide6.QtWidgets import (  # type: ignore
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QWidget,
)

from src.core.BattleConfig import BATTLE_CONFIG_MODE_KEY, DEFAULT_BATTLE_CONFIG
from src.gui.components.EndfieldTacticalDrawer import is_dark, sync_bus  # type: ignore
from src.gui.EndfieldConfigContent import has_task_configuration
from src.gui.EndfieldPrimitives import EndfieldActionButton, EndfieldTag, ui_tr
from src.gui.EndfieldTheme import palette
from src.gui.EndfieldTypography import ui_font

logger = Logger.get_logger(__name__)


def extract_strategy_tags(task_name: str, task_obj: Any) -> list[str]:
    """提取子任务的关键策略摘要标签"""
    tags: list[str] = []
    if not task_obj or not hasattr(task_obj, "config") or not task_obj.config:
        return ["默认策略"]

    cfg = task_obj.config
    # 针对重点任务特化提炼
    if task_name == "帝江号整理":
        if cfg.get("⭐帝江号一键存放", True):
            tags.append("一键存放: ON")
        if cfg.get("⭐简易制作", False):
            tags.append("简易制作: ON")
    elif task_name == "帝江号收菜":
        tags.append("自动收取产出")
    elif task_name in ("刷体力", "日常刷体力"):
        stage = cfg.get("体力本")
        if stage:
            tags.append(f"副本: {stage}")
    elif task_name == "买信用商店":
        tags.append("自动兑换")
    elif task_name == "地区建设":
        tags.append("全基建巡检")
    elif task_name == "送礼":
        tags.append("好感度优先")
    elif task_name == "自动送货":
        tags.append("高效物流路线")

    # 通用提取：如果未命中特化规则，提取 True 开关
    if not tags:
        for k, v in cfg.items():
            if (
                k.startswith("_")
                or k in DEFAULT_BATTLE_CONFIG
                or k == BATTLE_CONFIG_MODE_KEY
                or not isinstance(v, bool)
                or not v
            ):
                continue
            # 保留配置项原名（含 ⭐），显示时按原名取译文再去掉星标
            tags.append(f"{k}: ON")
            if len(tags) >= 2:
                break

    return tags if tags else ["默认参数"]


def display_tag(tag: str) -> str:
    """标签按「名称: 值」分段翻译；名称多是配置项名，与配置面板用同一条译文。"""
    name, sep, value = tag.partition(": ")
    if not sep:
        return ui_tr(tag)
    return f"{ui_tr(name).lstrip('⭐ ')}{sep}{ui_tr(value)}"


class EndfieldCheckpointCard(QFrame):
    """
    终末地统一勾选点组件：
    [勾选框] [序号] [子任务名] [策略摘要 Tags] [配置按钮]
    """

    checked_changed = Signal(str, bool)  # task_name, is_checked
    config_requested = Signal(str, object)  # task_name, task_obj

    def __init__(
        self,
        index_str: str,
        task_name: str,
        task_obj: Any,
        is_checked: bool = True,
        parent: QWidget | None = None,
        participates: bool = True,
    ):
        super().__init__(parent)
        self.index_str = index_str
        self.task_name = task_name
        self.task_obj = task_obj
        self.participates = participates
        self._hovered = False
        # 面板里最后一行不画底部细线，由面板边框收尾
        self.last_row = False

        self.setFixedHeight(52)
        self.setObjectName("EndfieldCheckpointCard")

        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(4, 0, 4, 0)
        self.main_layout.setSpacing(14)

        # 1. 勾选框
        self.checkbox = QCheckBox(self)
        self.checkbox.setChecked(is_checked)
        self.checkbox.setCursor(Qt.PointingHandCursor)
        self.checkbox.stateChanged.connect(self._on_check_state_changed)
        self.main_layout.addWidget(self.checkbox)

        # 2. 序号：墨色切角编号牌
        self.idx_label = EndfieldTag(self.index_str, self, ink=True)
        self.idx_label.setObjectName("checkpointIdx")
        self.main_layout.addWidget(self.idx_label)

        # 3. 任务名（task_name 仍是存配置用的中文名）
        self.name_label = QLabel(ui_tr(self.task_name), self)
        self.name_label.setObjectName("checkpointName")
        self.name_label.setFont(ui_font(15, QFont.Bold))
        self.name_label.setFixedWidth(168)
        self.main_layout.addWidget(self.name_label)

        # 4. 策略摘要 Tag 容器
        self.tag_container = QWidget(self)
        self.tag_layout = QHBoxLayout(self.tag_container)
        self.tag_layout.setContentsMargins(0, 0, 0, 0)
        self.tag_layout.setSpacing(6)
        self.tag_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.main_layout.addWidget(self.tag_container, 1)

        # 配置按钮：切角灰块，悬停时黄色擦入
        self.config_btn = EndfieldActionButton(None, ui_tr("配置"), self)
        self.config_btn.setObjectName("checkpointConfigBtn")
        self.config_btn.setFixedSize(64, 28)
        self.config_btn.setFont(ui_font(13, QFont.DemiBold))
        self.config_btn.clicked.connect(self._on_config_clicked)
        self.config_btn.setVisible(has_task_configuration(task_obj))
        self.main_layout.addWidget(self.config_btn)

        # 渲染 Tag 与初始主题
        self.refresh_tags()
        self.apply_theme_style()

        # 订阅全局主题切换与双向同步信号
        try:
            from qfluentwidgets import qconfig  # type: ignore

            qconfig.themeChanged.connect(self._on_theme_changed)
        except Exception as exc:
            logger.debug(f"Failed to connect themeChanged in checkpoint card: {exc}")

        sync_bus.config_changed.connect(self._on_external_config_changed)

    def _on_theme_changed(self, *args: Any):
        self.apply_theme_style()
        self.refresh_tags()

    def enterEvent(self, event: Any):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: Any):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event: Any):
        """行底色只在悬停时出现；行与行之间用 1px 细线分隔。"""
        colors = palette(is_dark())
        painter = QPainter(self)
        if self._hovered:
            painter.fillRect(self.rect(), QColor(colors["row_hover"]))
        if not self.last_row:
            painter.fillRect(0, self.height() - 1, self.width(), 1, QColor(colors["divider"]))
        painter.end()

    def _on_check_state_changed(self, state: int):
        is_chk = state == Qt.Checked.value or state == 2
        self.checked_changed.emit(self.task_name, is_chk)

    def _on_config_clicked(self):
        if has_task_configuration(self.task_obj):
            self.config_requested.emit(self.task_name, self.task_obj)

    def _on_external_config_changed(self, changed_name: str):
        if changed_name == self.task_name:
            self.refresh_tags()

    def refresh_tags(self):
        """刷新策略标签显示：文本未变化时只更新配色，避免主题切换时重建控件。"""
        tags = extract_strategy_tags(self.task_name, self.task_obj)
        if not self.participates:
            tags = ["未纳入一条龙"] + [t for t in tags if t != "未纳入一条龙"]
        tags = [display_tag(t) for t in tags]

        existing: list[EndfieldTag] = []
        for i in range(self.tag_layout.count()):
            widget = self.tag_layout.itemAt(i).widget() if self.tag_layout.itemAt(i) else None
            if isinstance(widget, EndfieldTag):
                existing.append(widget)

        # 文本未变化时保留控件，标签自己跟随主题重绘
        if [tag.text() for tag in existing] == tags:
            return

        while self.tag_layout.count():
            item = self.tag_layout.takeAt(0)
            if item is not None and item.widget() is not None:
                item.widget().deleteLater()

        for t in tags:
            self.tag_layout.addWidget(EndfieldTag(t, self.tag_container))

    def apply_theme_style(self):
        """配色由各控件自绘时读取当前主题，这里只触发重绘。"""
        self.update()
