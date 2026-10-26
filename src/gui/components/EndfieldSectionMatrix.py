# Endfield Section Matrix (终末地分区矩阵)
# 每个顶栏页签 = 一个 SECTION：眉标横幅 + 统一切角勾选点行，配置走战术抽屉

from typing import Any

from ok import Logger, og
from PySide6.QtCore import QEvent, Qt, QTimer  # type: ignore
from PySide6.QtGui import QColor, QPainter  # type: ignore
from PySide6.QtWidgets import (  # type: ignore
    QFrame,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import qconfig  # type: ignore

from src.gui.components.EndfieldCheckpointCard import EndfieldCheckpointCard  # type: ignore
from src.gui.components.EndfieldTacticalDrawer import (  # type: ignore
    EndfieldTacticalDrawer,
    is_dark,
    show_drawer,
    sync_bus,
)
from src.gui.EndfieldConfigContent import has_task_configuration
from src.gui.EndfieldEntryCards import EndfieldSectionBanner
from src.gui.EndfieldPrimitives import EndfieldTag, ThemeRepaint, attach_panel_tab, ui_tr
from src.gui.EndfieldTheme import palette

logger = Logger.get_logger(__name__)

# DailyTask 的七组配置键：任务名 → 分组键以 DailyTask 实际清单为准，
# 不在任何一组里的任务不参与一条龙（行内标“未纳入一条龙”，勾选点置灰）
DAILY_GROUP_KEY_ATTRS = (
    "CFG_BOAT_TASKS",
    "CFG_OTHER_TASKS",
    "CFG_BATTLE_TASKS",
    "CFG_REWARD_TASKS",
    "CFG_FARM_TASKS",
    "CFG_DELIVERY_TASKS",
    "CFG_TAIL_TASKS",
)
DAILY_ROSTER_ATTRS = (
    "BOAT_TASKS",
    "OTHER_TASKS",
    "BATTLE_TASKS",
    "REWARD_TASKS",
    "FARM_TASKS",
    "DELIVERY_TASKS",
    "TAIL_TASKS",
)

# 兜底映射：DailyTask 尚未就绪时沿用固定清单
FALLBACK_GROUP_MAP = {
    "送礼": "帝江号任务",
    "帝江号整理": "帝江号任务",
    "帝江号收菜": "帝江号任务",
    "收邮件": "其他任务",
    "清理基质": "其他任务",
    "转交运送委托": "其他任务",
    "地区建设": "其他任务",
    "造装备": "其他任务",
    "收信用": "其他任务",
    "买信用商店": "其他任务",
    "刷体力": "战斗任务",
    "演算": "战斗任务",
    "影拓丰碑": "战斗任务",
    "活动奖励": "奖励任务",
    "日常奖励": "奖励任务",
    "农牧相关": "农牧任务",
    "自动送货": "自动送货任务",
    "传送到帝江号右侧传送点": "收尾任务",
}

# 顶栏分组名 → (Section 编号, 中文标题, 英文眉标)
SECTION_META = {
    "帝江号": ("01", "帝江号", "DIJIANG"),
    "战斗": ("02", "战斗", "COMBAT"),
    "奖励": ("03", "奖励", "REWARDS"),
    "其他": ("04", "其他", "OTHER TASKS"),
}

# 统一渲染为分区矩阵的顶栏（「收尾」页保留原生卡片）
MATRIX_GROUPS = set(SECTION_META)


def find_daily_task() -> Any:
    """定位一键日常总控任务实例（DailyTask），勾选状态即写入它的配置分组列表。"""
    executor: Any = getattr(og, "executor", None)
    for task in getattr(executor, "onetime_tasks", None) or []:
        if task.__class__.__name__ == "DailyTask":
            return task
    return None


def _daily_feature_classes(daily: Any) -> set[type]:
    """DailyTask 实际包裹的子任务类；用于在同名任务中挑出真正的一条龙子任务。"""
    classes: set[type] = set()
    # 只扫实例字典：触发 class 上的属性（如 running / current_user）会有副作用
    for value in vars(daily).values():
        task_class = getattr(value, "task_class", None)
        if isinstance(task_class, type):
            classes.add(task_class)
    return classes


def _resolve_task_name(roster: str, tasks: list[Any], feature_classes: set[type]) -> str | None:
    """把 DailyTask 清单名对到实际任务实例名（如「刷体力」→「日常刷体力」）。

    同名任务可能不止一个，优先取 DailyTask 真正包裹的子任务类实例。
    """
    candidates = []
    for task in tasks:
        name = getattr(task, "name", "") or ""
        if name and (name == roster or name.endswith(roster) or roster.endswith(name)):
            candidates.append(task)
    if not candidates:
        return None
    for task in candidates:
        if task.__class__ in feature_classes:
            return getattr(task, "name", "") or None
    return getattr(candidates[0], "name", "") or None


def daily_group_map() -> dict[str, tuple[str, str]]:
    """任务名 → (DailyTask 分组键, 清单里登记的名字)；未列入任何一组的任务不参与一条龙。"""
    daily = find_daily_task()
    if daily is None:
        return {name: (group, name) for name, group in FALLBACK_GROUP_MAP.items()}

    executor: Any = getattr(og, "executor", None)
    tasks = [task for task in (getattr(executor, "onetime_tasks", None) or []) if getattr(task, "name", "")]
    feature_classes = _daily_feature_classes(daily)

    roster_items: list[tuple[str, str]] = []
    for attr in DAILY_GROUP_KEY_ATTRS:
        group_key = getattr(daily, attr, None)
        if not group_key:
            continue
        for source in (getattr(daily, "default_config", None), getattr(daily, "config", None)):
            if source is None or not hasattr(source, "get"):
                continue
            value = source.get(group_key, [])
            if isinstance(value, (list, tuple)):
                roster_items.extend((name, group_key) for name in value)
    for attr in DAILY_ROSTER_ATTRS:
        group_key = getattr(daily, "CFG_" + attr, None)
        if not group_key:
            continue
        roster_items.extend((name, group_key) for name in (getattr(daily, attr, []) or []))

    mapping: dict[str, tuple[str, str]] = {}
    for roster, group_key in roster_items:
        task_name = _resolve_task_name(roster, tasks, feature_classes)
        if task_name:
            mapping.setdefault(task_name, (group_key, roster))
    return mapping or {name: (group, name) for name, group in FALLBACK_GROUP_MAP.items()}


def subtask_enabled(task_name: str) -> bool:
    entry = daily_group_map().get(task_name)
    if not entry:
        return False
    group_key, roster = entry
    daily = find_daily_task()
    cfg: Any = getattr(daily, "config", None) if daily is not None else None
    if cfg is None or not hasattr(cfg, "get"):
        return False
    return roster in (cfg.get(group_key, []) or [])


def set_subtask_enabled(task_name: str, checked: bool) -> None:
    """把勾选写入 DailyTask 对应分组列表并落盘。"""
    entry = daily_group_map().get(task_name)
    if not entry:
        return
    group_key, roster = entry
    daily = find_daily_task()
    cfg: Any = getattr(daily, "config", None) if daily is not None else None
    if cfg is None or not hasattr(cfg, "get"):
        return

    current = cfg.get(group_key, [])
    current_list = list(current) if isinstance(current, (list, tuple)) else []
    if checked and roster not in current_list:
        current_list.append(roster)
    elif not checked and roster in current_list:
        current_list.remove(roster)
    else:
        return

    cfg[group_key] = current_list
    try:
        if hasattr(cfg, "save"):
            cfg.save()  # type: ignore
        elif hasattr(daily, "save_config"):
            daily.save_config()  # type: ignore
        logger.info(f"DailyTask config updated: {group_key} -> {current_list}")
    except Exception as exc:
        logger.error(f"Failed to save DailyTask config: {exc}")


def open_task_drawer(host: QWidget, task_name: str, task_obj: Any) -> EndfieldTacticalDrawer | None:
    if not has_task_configuration(task_obj):
        return None
    return show_drawer(host, lambda drawer: drawer.open_subtask(task_name, task_obj))


def open_daily_settings_drawer(host: QWidget) -> EndfieldTacticalDrawer | None:
    """一条龙页眉「一条龙设置」：DailyTask 卡片隐藏后，多账户、退出与汇总等总控字段从这里编辑。"""
    daily = find_daily_task()
    if daily is None:
        return None
    # 子任务清单已由勾选行维护，侧栏只放总控字段；启动用页眉的“开始一条龙”，侧栏不再放单独运行
    roster_keys = [key for key in (getattr(daily, attr, None) for attr in DAILY_GROUP_KEY_ATTRS) if key]
    return show_drawer(host, lambda drawer: drawer.open_subtask(daily.name, daily, roster_keys, run_once=False))


class EndfieldSectionMatrix(ThemeRepaint, QWidget):
    """单个顶栏页签的分区面板：标题行压一条墨线，下面是细线分隔的勾选行。"""

    def __init__(self, group_name: str, tasks: list[Any], parent: QWidget | None = None):
        super().__init__(parent)
        self.group_name = group_name
        self.tasks = tasks
        self.checkpoint_cards: list[EndfieldCheckpointCard] = []

        self.setObjectName("EndfieldSectionMatrix")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 6)
        outer.setSpacing(0)

        outer.addWidget(self._build_banner())

        row_box = QVBoxLayout()
        row_box.setContentsMargins(0, 0, 0, 0)
        row_box.setSpacing(0)
        group_map = daily_group_map()
        for i, task in enumerate(tasks, start=1):
            name = getattr(task, "name", "") or task.__class__.__name__
            participates = name in group_map
            card = EndfieldCheckpointCard(
                index_str=f"{i:02d}",
                task_name=name,
                task_obj=task,
                is_checked=subtask_enabled(name) if participates else False,
                parent=self,
                participates=participates,
            )
            if not participates:
                card.checkbox.setEnabled(False)
                card.checkbox.setToolTip(ui_tr("该任务不在一条龙执行清单中，可用右侧「配置」抽屉单独运行"))
            card.checked_changed.connect(self._on_checked)
            card.config_requested.connect(self._on_config_requested)
            self.checkpoint_cards.append(card)
            row_box.addWidget(card)
        if self.checkpoint_cards:
            self.checkpoint_cards[-1].last_row = True

        outer.addLayout(row_box)
        attach_panel_tab(self)
        qconfig.themeChanged.connect(self._repaint)
        sync_bus.config_changed.connect(self._on_config_changed)
        self._height_update_pending = False
        self._height_timer = QTimer(self)
        self._height_timer.setSingleShot(True)
        self._height_timer.timeout.connect(self._lock_height)
        self._refresh_count()
        self._lock_height()

    def event(self, event: QEvent) -> bool:
        result = super().event(event)
        if event.type() in (QEvent.LayoutRequest, QEvent.StyleChange, QEvent.FontChange) and not getattr(
            self, "_height_update_pending", True
        ):
            self._height_update_pending = True
            self._height_timer.start(0)
        return result

    def _lock_height(self) -> None:
        """样式改变横幅高度后重新测量，避免 ExpandCardLayout 裁掉末行。"""
        self._height_update_pending = False
        layout = self.layout()
        layout.invalidate()
        layout.activate()
        target = max(layout.sizeHint().height(), layout.minimumSize().height(), 120)
        if self.height() != target or self.minimumHeight() != target:
            self.setFixedHeight(target)

    def paintEvent(self, event: Any) -> None:
        colors = palette(is_dark())
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(colors["panel"]))
        painter.setPen(QColor(colors["border"]))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.end()

    def _build_banner(self) -> QFrame:
        sec_id, title, sub_title = SECTION_META.get(self.group_name, ("--", self.group_name, "PIPELINE SECTION"))
        banner = EndfieldSectionBanner(sec_id, ui_tr(title), sub_title, self, panel_tab=False)
        banner.layout().setContentsMargins(0, 4, 0, 12)
        banner.layout().setSpacing(10)
        self.count_label = banner.count_label
        self.count_label.setText(ui_tr("勾选后纳入一条龙"))
        self.count_label.show()
        self.count_tag = EndfieldTag(f"{ui_tr('已选')}: 0 / 0", banner)
        banner.layout().addWidget(self.count_tag, 0, Qt.AlignVCenter)
        banner.setFixedHeight(banner.sizeHint().height())
        return banner

    def _refresh_count(self) -> None:
        selected = sum(card.checkbox.isChecked() for card in self.checkpoint_cards)
        self.count_tag.set_text(f"{ui_tr('已选')}: {selected} / {len(self.checkpoint_cards)}")

    def _on_checked(self, task_name: str, checked: bool) -> None:
        set_subtask_enabled(task_name, checked)
        self._refresh_count()

    def _on_config_changed(self, changed_name: str) -> None:
        # 「一条龙设置」重置配置会改子任务清单；只回显勾选，不再写回配置
        daily = find_daily_task()
        if daily is None or changed_name != getattr(daily, "name", None):
            return
        for card in self.checkpoint_cards:
            if card.participates:
                card.checkbox.blockSignals(True)
                card.checkbox.setChecked(subtask_enabled(card.task_name))
                card.checkbox.blockSignals(False)
        self._refresh_count()

    def _on_config_requested(self, task_name: str, task_obj: Any) -> None:
        open_task_drawer(self, task_name, task_obj)
