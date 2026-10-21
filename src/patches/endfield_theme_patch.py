# pyright: reportMissingImports=false, reportConstantRedefinition=false
from __future__ import annotations

import importlib
import logging
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from ok.ui.qt.common.style_sheet import StyleSheet as OkStyleSheet  # type: ignore
from PySide6.QtCore import QEvent, Qt, QTimer  # type: ignore
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPen, QPolygon  # type: ignore
from PySide6.QtWidgets import (  # type: ignore
    QApplication,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (  # type: ignore
    FluentIcon,
    NavigationItemPosition,
    Theme,
    qconfig,
    setThemeColor,
)
from qfluentwidgets.common.style_sheet import updateStyleSheet  # type: ignore

from src.gui.EndfieldControls import install_control_paint
from src.gui.EndfieldEntryCards import (
    display_title,
    style_expand_header,
    style_panel_title,
    style_setting_header,
    update_task_status,
)
from src.gui.EndfieldNavigation import EndfieldNavBrand, install_navigation_paint, style_navigation_item
from src.gui.EndfieldPrimitives import EndfieldTriStrip
from src.gui.EndfieldTheme import DARK, LIGHT, palette
from src.gui.EndfieldWindowLayout import install_window_size_button, patch_window_layout

_PATCH_INSTALLED = False
logger = logging.getLogger("ok.theme_patch")

ENDFIELD_ACCENT_DARK = DARK["accent"]
ENDFIELD_ACCENT_LIGHT = LIGHT["accent"]

_QSS_CACHE: dict[bool, str] = {}
_CARD_CLASS_CACHE: list[str] | None = None

# 勾选点对勾图标（QSS 无法绘制文字，用 SVG 资源标记“已勾选”状态）
_CHECK_TICK_PATH = Path(__file__).resolve().parent.parent / "gui" / "assets" / "check_tick.svg"

# 卡片面板配色 Token：非 QSS 路径（paintEvent 接管、控件级样式表覆写）共用同一套颜色
CARD_PANEL_TOKENS = {
    True: {"bg": DARK["panel"], "border": DARK["border"]},
    False: {"bg": LIGHT["panel"], "border": LIGHT["border"]},
}


def _widget_scope_override_qss(dark: bool) -> str:
    """控件级样式表尾部追加的终末地覆写规则（ok 自带 tab.qss 优先级高于应用级 QSS）。"""
    tokens = CARD_PANEL_TOKENS[dark]
    colors = palette(dark)
    expand_bg = colors["panel"]
    expand_border = colors["divider"]
    text_main = colors["text"]
    text_sub = colors["muted"]
    accent, accent_ink = colors["accent"], colors["on_accent"]
    signal, on_signal = colors["signal"], colors["on_signal"]
    return f"""
Card, EmptyCard {{
    background: transparent !important;
}}

Card > #card, EmptyCard > #card {{
    background-color: {tokens["bg"]} !important;
    border: 1px solid {tokens["border"]} !important;
    border-radius: 0px !important;
}}

/* 卡片页眉与展开容器自身不画底色，让父卡片自绘的面板填充透上来 */
QScrollArea > QWidget#qt_scrollarea_viewport, QAbstractScrollArea > QWidget#qt_scrollarea_viewport {{
    background: transparent !important;
    border: none !important;
}}

HeaderSettingCard, ExpandSettingCard, ConfigCard QWidget#headerView {{
    background: transparent !important;
    border: none !important;
}}

/* 设置卡的面板由 paintEvent 自绘；QSS 边框只会让内容内缩 1px，与可展开卡的页眉错位 */
SettingCard {{ border: none !important; }}

#configContentFrame, #scrollWidget > #view {{
    background-color: {expand_bg} !important;
    border: none !important;
    border-radius: 0px !important;
}}

#drawerConfigForm {{
    background-color: {expand_bg}; border: 1px solid {tokens["border"]};
    border-radius: 0px;
}}

#configRow {{
    background-color: transparent !important;
    border-top: 1px solid {expand_border} !important;
    border-bottom: none !important;
}}
#configRow[configFirst=true] {{ border-top: none !important; }}

#sourceWidget {{
    background-color: {tokens["bg"]} !important;
    border-radius: 0px !important;
}}

QLabel#viewTitleLabel, TitleLabel {{ color: {text_main} !important; font-size: 20px; font-weight: 600; }}
QLabel#titleLabel {{ color: {text_main} !important; font-size: 14px; font-weight: 500; }}
SettingCard QLabel#titleLabel, HeaderSettingCard QLabel#titleLabel {{
    font-size: 14px; font-weight: 600;
}}
#EndfieldPanelHeader {{
    background: {tokens["bg"]}; border: 1px solid {tokens["border"]};
}}
QLabel#combatSource, QLabel#accountHint {{
    color: {text_main}; background: {expand_bg};
    border: 1px solid {text_main}; border-left: 6px solid {signal};
    padding: 10px 12px; margin: 0px;
}}
#configRow[configGroup=true] {{ background-color: transparent !important; }}
#configRow[subConfig=true] {{ border-top: none !important; border-left: none; }}
#configRow QLabel#titleLabel[configGroup=true] {{ font-size: 14px; font-weight: 500; }}
QLabel#contentLabel {{ color: {text_sub} !important; font-size: 12px; font-weight: 400; }}
QLabel#optionText {{ color: {text_main}; font-size: 14px; font-weight: 400; }}
QLabel#workspaceEyebrow, QLabel#bannerCode, QLabel#drawerEyebrow, QLabel#checkpointIdx {{
    font-family: 'Saira';
}}
QLabel#workspaceEyebrow, QLabel#bannerCode, QLabel#drawerEyebrow {{
    color: {text_sub}; font-size: 11px; font-weight: 600;
}}
PushButton, PrimaryPushButton, ToolButton, ComboBox, LineEdit, SpinBox, DoubleSpinBox {{
    border-radius: 0px;
}}
PushButton, ToolButton {{
    background-color: {colors["chip"]}; color: {text_main}; border: none;
}}
PrimaryPushButton, PrimaryToolButton {{
    background-color: {colors["go"]}; color: {colors["on_go"]}; border: none; border-radius: 0px;
}}
PushButton:hover, ToolButton:hover, PrimaryPushButton:hover, PrimaryToolButton:hover {{
    background-color: {signal}; color: {on_signal}; border: none;
}}
PushButton:pressed, ToolButton:pressed, PrimaryPushButton:pressed, PrimaryToolButton:pressed {{
    background-color: {colors["primary_pressed"]}; color: {on_signal};
}}
PrimaryPushButton:disabled, PrimaryToolButton:disabled, PushButton:disabled, ToolButton:disabled {{
    background-color: {colors["disabled_bg"]}; color: {colors["disabled_text"]}; border: none;
}}
TransparentPushButton, TransparentToolButton, HyperlinkButton {{ background-color: transparent; border: none; }}
ComboBox, LineEdit, TextEdit, PlainTextEdit, SpinBox, DoubleSpinBox {{
    background-color: {colors["input"]}; color: {text_main};
    border: 1px solid {colors["border_hover"]}; border-radius: 0px;
    selection-background-color: {accent}; selection-color: {accent_ink};
}}
ComboBox:hover, LineEdit:hover, TextEdit:hover, PlainTextEdit:hover, SpinBox:hover, DoubleSpinBox:hover {{
    border: 1px solid {text_main};
}}
ComboBox:focus, LineEdit:focus, TextEdit:focus, PlainTextEdit:focus, SpinBox:focus, DoubleSpinBox:focus {{
    border: 1px solid {accent};
}}
ComboBox:disabled, LineEdit:disabled, TextEdit:disabled, PlainTextEdit:disabled, SpinBox:disabled, DoubleSpinBox:disabled {{
    color: {colors["disabled_text"]}; background-color: {colors["disabled_bg"]}; border: 1px solid {tokens["border"]};
}}
CheckBox::indicator {{ border-radius: 0px; }}
QWidget#combatSettingsHeader {{ background: transparent; }}
"""


def _card_class_names() -> list[str]:
    """收集所有需套用终末地面板样式的卡片类名（QSS 类型选择器只匹配确切类名，不含子类）。"""
    global _CARD_CLASS_CACHE
    if _CARD_CLASS_CACHE is not None:
        return _CARD_CLASS_CACHE

    names: set[str] = {
        "Card",
        "CardWidget",
        "SettingCard",
        "HeaderSettingCard",
        "SimpleCardWidget",
        "ElevatedCardWidget",
        "HeaderCardWidget",
        "ExpandSettingCard",
        "ConfigCard",
        "TaskCard",
        "GlobalConfigCard",
    }
    try:
        from qfluentwidgets import CardWidget, SettingCard  # type: ignore

        pending = [SettingCard, CardWidget]
        while pending:
            cls = pending.pop()
            name = getattr(cls, "__name__", "")
            if name and name not in names:
                names.add(name)
            pending.extend(cls.__subclasses__())
    except Exception as exc:
        logger.debug("Failed to enumerate card classes: %s", exc)

    _CARD_CLASS_CACHE = sorted(names)
    return _CARD_CLASS_CACHE


# 终末地样式下发的目标窗口（窗口级 QSS 比应用级快约 10 倍）
_ENDFIELD_WINDOWS: list[Any] = []
# 主题切换起点时间戳（毫秒计时器），用于统计整条切换链耗时
_theme_switch_start: float = 0.0


def _mark_theme_switch_start(*_args: Any) -> None:
    """记录主题切换起点，供 themeChangedFinished 统计总耗时。"""
    global _theme_switch_start
    _theme_switch_start = time.perf_counter()


def generate_endfield_qss(dark: bool = True) -> str:
    """Build paper/ink surfaces; signal yellow only fills hovered or current controls."""
    # 深色主题勾选框是浅墨底，用黑勾；浅色主题是深墨底，用白勾
    _tick_svg = _CHECK_TICK_PATH if dark else _CHECK_TICK_PATH.with_name("check_tick_inverse.svg")
    check_tick_url = _tick_svg.as_posix()
    c = palette(dark)
    bg_canvas, bg_nav, nav_border = c["canvas"], c["navigation"], c["border"]
    card_bg, card_border, card_hover_border, card_hover_bg = c["panel"], c["border"], c["border_hover"], c["hover"]
    expand_view_bg, expand_view_border = c["panel"], c["divider"]
    text_main, text_sub = c["text"], c["muted"]
    primary_btn_bg, primary_btn_fg = c["go"], c["on_go"]
    signal, on_signal, pressed_bg = c["signal"], c["on_signal"], c["primary_pressed"]
    switch_on_bg, switch_on_fg = c["accent"], c["on_accent"]
    btn_normal_bg, btn_normal_fg = c["chip"], c["text"]
    input_bg, input_border, input_fg, input_focus_border = c["input"], c["border_hover"], c["text"], c["accent"]
    scrollbar_handle, scrollbar_handle_hover = c["border"], c["border_hover"]
    chk_card_border, chk_card_hover_border, chk_idx_color = c["border_hover"], c["text"], c["muted"]
    drawer_bg, drawer_header_bg, drawer_border = c["panel"], c["canvas"], c["border"]
    banner_bg, banner_title_fg = c["panel"], c["text"]
    section_title_fg = c["text"]

    card_classes = _card_class_names()
    card_selector = ",\n".join([*card_classes, "#card", "#configContentFrame", "EmptyCard > #card"])
    card_hover_selector = ",\n".join(f"{name}:hover" for name in card_classes) + (
        ",\n#card:hover, #configContentFrame:hover"
    )

    return f"""
/* ============================================================
   ENDFIELD TACTICAL UI - DUAL THEME QSS ({"DARK" if dark else "LIGHT"})
   ============================================================ */

/* 1. 全局基础字体与直角重置 (避免裸配 QWidget 造成全树级联卡顿) */
QLabel, QAbstractButton, QLineEdit, QComboBox, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox {{
    font-family: 'Segoe UI', 'Microsoft YaHei UI', sans-serif;
    color: {text_main};
    outline: none;
}}

/* 2. 顶级主窗口背景；工作区自绘画布与等高线，Tab 与 #view 保持透明 */
FluentWindow,
#stackedWidget,
#mainView,
ToolBar,
RoundCornerContainer {{
    background-color: {bg_canvas} !important;
    border: none !important;
    border-radius: 0px !important;
}}

/* 2.1 滚动容器透明：ExpandSettingCard / TaskCard 本身就是 QScrollArea，
   若给 QScrollArea 上色会盖住它们自绘的硬直角面板 */
QScrollArea {{
    background: transparent !important;
    border: none !important;
    border-radius: 0px !important;
}}

/* 2.2 配置行分隔线（ok 的 LabelAndWidget 行是普通 QWidget，不经过 qfluentwidgets 样式管线） */
#configRow {{
    background-color: transparent !important;
    border-bottom: 1px solid {card_border} !important;
}}

/* 3. 导航侧边栏 (NavigationPanel)；导航项由 EndfieldNavigation 自绘 */
NavigationInterface, NavigationPanel {{
    background-color: {bg_nav} !important;
    border-right: 1px solid {nav_border} !important;
}}

/* 4. 核心任务卡片、配置卡片与展开面板 (TaskCard, ConfigCard, ExpandSettingCard) */
{card_selector} {{
    background-color: {card_bg} !important;
    border: 1px solid {card_border} !important;
    border-radius: 0px !important;
}}

{card_hover_selector} {{
    border: 1px solid {card_hover_border} !important;
    background-color: {card_hover_bg} !important;
}}

/* 展开卡片的内部配置内容区域 (内凹底色 + 1px 顶分割线) */
ExpandSettingCard #view,
ExpandSettingCard QScrollArea,
#configContentFrame {{
    background-color: {expand_view_bg} !important;
    border-top: 1px solid {expand_view_border} !important;
    border-radius: 0px !important;
}}

/* 5. 主操作按钮 (PrimaryPushButton)：墨底；悬停与按下换成信号黄 */
PrimaryPushButton,
PrimaryToolButton,
#startBtn,
#primaryButton {{
    background-color: {primary_btn_bg} !important;
    color: {primary_btn_fg} !important;
    border: none !important;
    border-radius: 0px !important;
    font-weight: 700 !important;
    font-size: 13px;
    padding: 6px 22px 6px 14px;
}}

/* 6. 普通操作按钮 (PushButton)：浅灰块，无描边 */
PushButton,
ToolButton {{
    background-color: {btn_normal_bg} !important;
    color: {btn_normal_fg} !important;
    border: none !important;
    border-radius: 0px !important;
    font-weight: 600;
    font-size: 12px;
    padding: 5px 18px 5px 12px;
}}

TransparentPushButton,
HyperlinkButton {{
    background-color: transparent !important;
    border: none !important;
    border-radius: 0px !important;
}}

PushButton:hover,
ToolButton:hover,
PrimaryPushButton:hover,
PrimaryToolButton:hover,
#startBtn:hover,
#primaryButton:hover {{
    background-color: {signal} !important;
    color: {on_signal} !important;
}}

PushButton:pressed,
ToolButton:pressed,
PrimaryPushButton:pressed,
PrimaryToolButton:pressed,
#startBtn:pressed,
#primaryButton:pressed {{
    background-color: {pressed_bg} !important;
    color: {on_signal} !important;
}}

/* 7. Native Indicator painting is installed by EndfieldControls. */
SwitchButton {{
    qproperty-spacing: 10;
}}

/* 8. 文本标签、标题与说明 */
QLabel {{
    font-family: 'Segoe UI', 'Microsoft YaHei UI', sans-serif;
    color: {text_main};
}}

QLabel#titleLabel {{
    color: {text_main} !important;
    font-size: 14px;
    font-weight: 500;
}}

TitleLabel, LargeTitleLabel {{
    color: {text_main} !important;
    font-size: 20px;
    font-weight: 600;
}}

QLabel#contentLabel, CaptionLabel {{
    color: {text_sub} !important;
    font-size: 12px;
    font-weight: 400;
}}

SubtitleLabel {{ font-size: 18px; font-weight: 600; color: {text_main}; }}

/* 9. 输入框、下拉选择框 (LineEdit, ComboBox) */
LineEdit,
TextEdit,
PlainTextEdit,
ComboBox,
SpinBox,
DoubleSpinBox {{
    background-color: {input_bg} !important;
    border: 1px solid {input_border} !important;
    border-radius: 0px !important;
    color: {input_fg} !important;
    padding: 4px 8px;
    selection-background-color: {switch_on_bg};
    selection-color: {switch_on_fg};
}}

LineEdit:focus,
TextEdit:focus,
PlainTextEdit:focus,
ComboBox:focus,
SpinBox:focus {{
    border: 1px solid {input_focus_border} !important;
}}

/* 10. 滚动条 (QScrollBar) */
QScrollBar:vertical {{
    width: 6px;
    background: transparent;
    margin: 0px;
}}

QScrollBar::handle:vertical {{
    background: {scrollbar_handle};
    min-height: 24px;
    border-radius: 0px;
}}

QScrollBar::handle:vertical:hover {{
    background: {scrollbar_handle_hover};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

QScrollBar:horizontal {{
    height: 6px;
    background: transparent;
}}

QScrollBar::handle:horizontal {{
    background: {scrollbar_handle};
    min-width: 24px;
    border-radius: 0px;
}}

QScrollBar::handle:horizontal:hover {{
    background: {scrollbar_handle_hover};
}}

/* 11. 检查点卡片组件 (EndfieldCheckpointCard)
   底色与描边由控件 paintEvent 自绘（描边内缩 1px，避免被父容器裁掉右边框） */

#EndfieldCheckpointCard QLabel#checkpointIdx {{
    color: {chk_idx_color} !important;
    font-weight: 800;
}}

#EndfieldCheckpointCard QLabel#checkpointName {{
    color: {text_main} !important;
    font-weight: 700;
}}

#EndfieldCheckpointCard QLabel#checkpointTime {{
    color: {text_sub} !important;
}}

#EndfieldCheckpointCard QCheckBox {{
    spacing: 5px;
}}

#EndfieldCheckpointCard QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {chk_card_border};
    background: {input_bg};
}}

#EndfieldCheckpointCard QCheckBox::indicator:hover {{
    border: 1px solid {chk_card_hover_border};
}}

#EndfieldCheckpointCard QCheckBox::indicator:checked {{
    background-color: {switch_on_bg};
    border: 1px solid {switch_on_bg};
    image: url("{check_tick_url}");
}}

#EndfieldCheckpointCard QCheckBox::indicator:disabled {{
    background-color: {c["disabled_bg"]};
    border: 1px solid {card_border};
}}
#EndfieldCheckpointCard QCheckBox::indicator:checked:disabled {{
    background-color: {c["disabled_text"]};
    border: 1px solid {c["disabled_text"]};
}}

/* 12. 分区横幅：独立时是带墨色页签的面板，在日常面板里是压一条墨线的标题行 */
#compositeBanner {{
    background-color: {banner_bg} !important;
    border: 1px solid {card_border};
}}

#EndfieldSectionMatrix #compositeBanner {{
    background-color: transparent !important;
    border: none;
    border-bottom: 1px solid {text_main};
}}

#compositeBanner QLabel#bannerTitle {{
    color: {banner_title_fg} !important;
    font-weight: 800;
}}

#compositeBanner QLabel#bannerDesc {{
    color: {text_sub} !important;
}}

QLabel#sectionTitle {{
    color: {section_title_fg} !important;
    font-weight: 700;
    letter-spacing: 0.5px;
}}

/* 13. 战术抽屉 (EndfieldTacticalDrawer) 全局高速样式 */
#EndfieldTacticalDrawer {{
    background-color: {drawer_bg} !important;
    border-left: 1px solid {drawer_border} !important;
}}

#EndfieldTacticalDrawer #drawerHeader {{
    background-color: {drawer_header_bg} !important;
    border-bottom: 1px solid {drawer_border} !important;
}}

#EndfieldTacticalDrawer #drawerFooter {{
    background-color: {drawer_header_bg} !important;
    border-top: 1px solid {drawer_border} !important;
}}

#EndfieldTacticalDrawer QScrollArea,
#EndfieldTacticalDrawer #drawerScrollWidget {{
    background-color: {drawer_bg} !important;
    border: none !important;
}}
""" + _widget_scope_override_qss(dark)


# 顶栏页签命名与顺序（与 reorganize_tasks_architecture 写入的 group_name 严格一致）
ENDFIELD_DAILY_TAB_ORDER = ("帝江号", "战斗", "奖励", "其他")
ENDFIELD_COMBAT_TAB_ORDER = ("大世界", "自动战斗", "副本", "收尾")
ENDFIELD_AUTOMATION_TAB_KEYS = ("计划任务", "脚本", "模板")
ENDFIELD_DEVELOPER_TAB_KEYS = ("调试", "运行代码", "工具与调试")

# 原生页签与 ok-ef 自己的自动战斗页的键取简体中文页签名，与界面语言无关；TAB_CODES、ENGLISH_TITLES 和上面的分栏都按它登记。
# ok 的 GlobalConfigTab 只承载通知；src 里同名的全局配置页是 CustomTab，不查这张表。
NATIVE_TAB_KEYS = {
    "EndfieldCombatTab": "自动战斗",
    "StartTab": "截图方式",
    "TriggerTaskTab": "大世界",
    "DebugTab": "调试",
    "RunCodeTab": "运行代码",
    "EditTaskTab": "脚本",
    "TemplateTab": "模板",
    "ScheduleTaskTab": "计划任务",
    "GlobalConfigTab": "通知",
    "SettingTab": "设置",
    "AboutTab": "关于",
}


def endfield_tab_key(widget: Any, text: str) -> str:
    """按页面身份取页签键：任务分组用组名，自定义页签用未翻译的源名，原生页签按类名查表。"""
    from ok.ui.qt.widget.CustomTab import CustomTab  # type: ignore

    group_name = getattr(widget, "group_name", None)
    if group_name:
        return group_name
    if isinstance(widget, CustomTab):
        return widget.name
    return NATIVE_TAB_KEYS.get(type(widget).__name__, text)


def route_endfield_tabs(
    pending_tabs: list[dict[str, Any]], tr: Callable[[str], str]
) -> dict[str, list[tuple[str, Any, Any]]]:
    """按页签键把页面分到五个工作区并排好序；页签文字跟随界面语言，归类与排序只看键。"""
    routed: dict[str, list[tuple[str, str, Any, Any]]] = {
        "ENVIRONMENT": [],
        "DAILY": [],
        "COMBAT": [],
        "AUTOMATION": [],
        "DEVELOPER": [],
    }
    for item in pending_tabs:
        widget, label = item["widget"], item["text"]
        key = endfield_tab_key(widget, label)
        # 触发任务页签叫「大世界」；业务与战斗的「收尾」组显示为「其他」，归类仍按组名，免得与日常的「其他」合并
        shown_key = "其他" if key == "收尾" else key
        if key in ("大世界", "收尾"):
            label = tr(shown_key)
        widget.endfield_tab_key = shown_key
        if key in ENDFIELD_DEVELOPER_TAB_KEYS:
            code = "DEVELOPER"
        elif key in ENDFIELD_AUTOMATION_TAB_KEYS:
            code = "AUTOMATION"
        # 日常一条龙四大分栏（与 EndfieldSectionMatrix.SECTION_META 一致）
        elif key in ENDFIELD_DAILY_TAB_ORDER:
            code = "DAILY"
        elif key in ENDFIELD_COMBAT_TAB_ORDER:
            code = "COMBAT"
        # 其余归为系统与环境 (截图方式, 全局配置, 账号配置, 通知, 设置, 关于)
        else:
            code = "ENVIRONMENT"
        routed[code].append((key, label, item["icon"], widget))
    routed["DAILY"].sort(key=lambda entry: ENDFIELD_DAILY_TAB_ORDER.index(entry[0]))
    routed["COMBAT"].sort(key=lambda entry: ENDFIELD_COMBAT_TAB_ORDER.index(entry[0]))
    return {code: [(label, icon, widget) for _, label, icon, widget in entries] for code, entries in routed.items()}


def reorganize_tasks_architecture(executor: Any) -> None:
    """按类名改写任务的 group_name，决定任务落在哪个工作区页签。

    1. 日常一条龙：清单内的子任务分到帝江号、战斗、奖励、其他四个分栏。
    2. 业务与战斗：副本类任务归「副本」；DailyTask 与清单外的独立任务归「收尾」，页签显示为「其他」。
    """
    if not executor or not hasattr(executor, "onetime_tasks"):
        return

    TASK_GROUP_MAPPING = {
        # 1. 业务与战斗（核心副本与周本演练）
        "BattleTask": "副本",
        "YingTuoTask": "副本",
        "DemoDrawTask": "副本",
        "AutoCombatTask": "副本",
        # 启动一次游戏：原属旧「日常任务」页，随日常任务汇总页一起归入业务与战斗的「收尾」
        "TestStartGame": "收尾",
        # 2. 日常一条龙：四大分区流水线；DailyTask 自身的卡片不再展示（职能由一条龙接管）
        "DailyTask": "收尾",
        "BoatHarvestTask": "帝江号",
        "BoatOrganizeTask": "帝江号",
        "LiaisonGiftTask": "帝江号",
        "MailTask": "其他",
        "DeliverySendTask": "其他",
        "RegionalBuildTask": "其他",
        "CraftWeaponTask": "其他",
        "WarehouseTransferTask": "收尾",
        "CreditCollectTask": "其他",
        "CreditShopTask": "其他",
        # 独立「自动送货」不在一条龙清单内（清单里的自动送货由 DailyDeliveryTask 执行）
        "DeliveryTask": "收尾",
        # 接取委托 / 仓库物品转移同样不在清单内，留在「收尾」作为独立任务
        "TakeDeliveryTask": "收尾",
        "DailyBattleTask": "战斗",
        "DemoBattleTask": "战斗",
        "ActivityRewardTask": "奖励",
        "DailyRewardTask": "奖励",
        # 农牧领取按一条龙执行顺序排在自动送货之前，行序见 _endfield_refresh_ui 的 roster 排序
        "FarmRelatedTask": "其他",
        "HomePointTask": "其他",
        # 清单里的「自动送货」由日常自动送货执行，随其它物流任务同栏
        "DailyDeliveryTask": "其他",
    }

    for task in executor.onetime_tasks:
        cls_name = task.__class__.__name__
        if cls_name in TASK_GROUP_MAPPING:
            task.group_name = TASK_GROUP_MAPPING[cls_name]
            logger.debug(
                "Reassigned task %s (%s) to group: %s",
                getattr(task, "name", cls_name),
                cls_name,
                task.group_name,
            )


def register_endfield_window(window: Any) -> None:
    """登记需要套用终末地样式的主窗口。

    QApplication.setStyleSheet() 在本应用上单次耗时 13~16 s（实测 2453 个控件全树
    重新 polish，QSS 长度几乎不影响），而窗口级 setStyleSheet 仅 ~1.4 s，故样式
    只下发到主窗口及其子控件。
    """
    if window not in _ENDFIELD_WINDOWS:
        _ENDFIELD_WINDOWS.append(window)


def update_application_theme_qss(theme: Theme | None = None) -> None:
    """动态编译并注入当前深浅主题的 QSS，编译结果按主题缓存，切换时零编译。"""
    started = time.perf_counter()
    try:
        app_inst = QApplication.instance()
        if not app_inst:
            return

        is_dark = (theme if theme is not None else qconfig.theme) == Theme.DARK
        qss = _qss_for_theme(is_dark)
        targets = [w for w in _ENDFIELD_WINDOWS if w is not None]
        for target in targets:
            with suppress(RuntimeError):
                target.setStyleSheet(qss)
        if not targets:
            app_inst.setStyleSheet(qss)
        logger.info(
            "Endfield theme QSS applied to %d window(s): %s in %.1f ms",
            len(targets),
            "DARK" if is_dark else "LIGHT",
            (time.perf_counter() - started) * 1000,
        )
    except Exception as exc:
        logger.warning("Failed to apply theme QSS: %s", exc)


def _qss_for_theme(dark: bool) -> str:
    """按主题缓存编译好的 QSS，避免每次切换重复拼接上千行字符串。"""
    cached = _QSS_CACHE.get(dark)
    if cached is None:
        cached = generate_endfield_qss(dark=dark)
        _QSS_CACHE[dark] = cached
    return cached


def patch_onetime_task_tab() -> None:
    """拦截 OneTimeTaskTab，把一条龙四大分栏渲染为分区矩阵，并隐藏 DailyTask 汇总卡。"""
    try:
        from ok import og  # type: ignore
        from ok.ui.qt.tasks.OneTimeTaskTab import OneTimeTaskTab  # type: ignore
        from ok.ui.qt.tasks.TaskCard import TaskCard  # type: ignore

        from src.gui.components.EndfieldSectionMatrix import (  # type: ignore
            MATRIX_GROUPS,
            EndfieldSectionMatrix,
            daily_group_map,
        )

        def _endfield_refresh_ui(self: Any) -> None:
            # 移除旧卡片
            for w in self.card_widgets:
                self.remove_task_card(w)
                if hasattr(w, "dispose"):
                    w.dispose()
                w.deleteLater()
            self.card_widgets.clear()

            self.tasks = []
            executor: Any = getattr(og, "executor", None)
            if executor and hasattr(executor, "onetime_tasks"):
                for task in executor.onetime_tasks:
                    if not getattr(task, "visible", True):
                        continue
                    # DailyTask 汇总卡不再展示：子项勾选与参数已由「日常一条龙」分区矩阵接管
                    if task.__class__.__name__ == "DailyTask":
                        continue
                    task_group = getattr(task, "group_name", None)
                    if (self.is_standalone and not task_group) or (self.group_name and task_group == self.group_name):
                        self.tasks.append(task)

            # 日常分栏只保留 DailyTask 执行清单内的子任务：清单外的独立任务勾不动勾选点，改归「收尾」
            if self.group_name in MATRIX_GROUPS:
                roster = daily_group_map()
                dropped = [t for t in self.tasks if (getattr(t, "name", "") or "") not in roster]
                if dropped:
                    logger.info(
                        "Endfield daily tab %s hides non-roster tasks: %s",
                        self.group_name,
                        [getattr(t, "name", "") for t in dropped],
                    )
                self.tasks = [t for t in self.tasks if (getattr(t, "name", "") or "") in roster]
                # 行序按一条龙执行顺序，而非任务注册顺序（注册末尾的农牧领取要排在自动送货之前）
                order = {name: index for index, name in enumerate(roster)}
                self.tasks.sort(key=lambda task: order.get(getattr(task, "name", "") or "", len(order)))

            # 日常类顶栏统一渲染为终末地分区矩阵（统一勾选点、策略 Tag、预估耗时与配置入口）
            if self.group_name in MATRIX_GROUPS and self.tasks:
                matrix = EndfieldSectionMatrix(self.group_name, self.tasks, self)
                self.card_widgets.append(matrix)
                self.add_task_card(matrix)
                return

            for task in self.tasks:
                card = TaskCard(task, True)
                apply_native_card_panel(card)
                self.card_widgets.append(card)
                self.add_task_card(card)

        OneTimeTaskTab.refresh_ui = _endfield_refresh_ui
        logger.info("OneTimeTaskTab patched for Endfield subtask convergence.")
    except Exception as exc:
        logger.warning("Failed to patch OneTimeTaskTab: %s", exc)


def find_daily_composite_task() -> Any:
    """定位一键日常的复合总控任务实例（class DailyTask / name 日常任务）。"""
    from ok import og  # type: ignore

    executor = getattr(og, "executor", None)
    for task in getattr(executor, "onetime_tasks", None) or []:
        if task.__class__.__name__ == "DailyTask":
            return task
    return None


def start_daily_dispatch() -> None:
    """页眉「开始一条龙」按钮入口：等同于点击日常任务卡片上的「开始」。"""
    from ok import og  # type: ignore

    task = find_daily_composite_task()
    if task is None:
        logger.warning("Daily composite task not found, cannot start daily dispatch")
        return
    try:
        app = getattr(og, "app", None)
        start_controller = getattr(app, "start_controller", None)
        if start_controller is None:
            logger.warning("start_controller unavailable, cannot start daily dispatch")
            return
        start_controller.start(task)
        logger.info("Daily dispatch started from workspace header action")
    except Exception as exc:
        logger.error(f"Failed to start daily dispatch: {exc}")


def apply_native_card_panel(card: Any) -> None:
    """给原生 TaskCard/ConfigCard 套上终末地硬直角面板。

    这些卡片是 QScrollArea 子类，视口默认自填背景会盖住自绘面板，
    因此在控件级样式表里直接上色，并关闭视口自动填充。
    """
    dark = qconfig.theme == Theme.DARK
    tokens = CARD_PANEL_TOKENS[dark]
    inner_bg = tokens["bg"]
    with suppress(Exception):
        card.setStyleSheet(
            f"ExpandSettingCard {{ background-color: {tokens['bg']};"
            f" border: 1px solid {tokens['border']}; border-radius: 0px; }}"
            f" #configContentFrame {{ background-color: {inner_bg};"
            f" border: none; border-radius: 0px; }}"
        )
        # QScrollArea 的视口按调色板自填背景，会盖住卡片面板，必须同步上调色板
        palette = card.palette()
        palette.setColor(QPalette.Window, QColor(tokens["bg"]))
        palette.setColor(QPalette.Base, QColor(tokens["bg"]))
        card.setPalette(palette)
        viewport_getter = getattr(card, "viewport", None)
        if callable(viewport_getter):
            viewport: Any = viewport_getter()
            if viewport is not None:
                viewport.setAutoFillBackground(True)
                viewport_palette = viewport.palette()
                viewport_palette.setColor(QPalette.Window, QColor(tokens["bg"]))
                viewport_palette.setColor(QPalette.Base, QColor(tokens["bg"]))
                viewport.setPalette(viewport_palette)


def install_endfield_theme_patch() -> None:
    """把终末地风格界面装到 ok-ef 主窗口。

    1. 主窗口：页签按页面身份归入五个工作区，工作区顶部用切角页签切换；标题栏加「恢复默认大小」。
    2. 样式：浅色、深色两套 QSS 只下发到主窗口；切换主题时同步强调色。
    3. 任务页：日常分栏渲染为分区勾选清单，战斗字段集中到「自动战斗」页，参数在右侧面板编辑。
    """
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    # 确保 ok 的日志处理器已就绪（补丁在日志系统初始化之前执行，否则 INFO 会被丢弃）
    try:
        from ok.util import logger as ok_logger_module  # type: ignore

        ok_logger_module._ensure_default_console_logger()
    except Exception as exc:
        logging.getLogger("ok.theme_patch").debug("Logger bootstrap skipped: %s", exc)

    try:
        main_window_module = importlib.import_module("ok.ui.qt.MainWindow")
        MainWindow = main_window_module.MainWindow
    except Exception as exc:
        logger.warning("Failed to import MainWindow for Endfield theme patch: %s", exc)
        return

    try:
        setThemeColor(QColor(ENDFIELD_ACCENT_DARK))
    except Exception as exc:
        logger.warning("Failed to pre-set Endfield theme color: %s", exc)

    orig_init = MainWindow.__init__
    orig_add_sub_interface = MainWindow.addSubInterface
    orig_switch_to = MainWindow.switchTo

    def _patched_sync_system_accent_color(self: Any, refresh: bool = False) -> bool:
        dark = qconfig.theme == Theme.DARK
        # 强调色锁定为当前主题的墨色（浅色深墨、深色浅墨），信号黄不进 qfluent 的 themeColor
        target_color = QColor(ENDFIELD_ACCENT_DARK if dark else ENDFIELD_ACCENT_LIGHT)
        current = qconfig.get(qconfig.themeColor)
        if current != target_color:
            qconfig.set(qconfig.themeColor, target_color, save=False)
            if refresh:
                updateStyleSheet(lazy=True)
            return True
        return False

    def _patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        # 1. 在初始化前改写任务分组，原生页签才会按新分组生成
        from ok import og  # type: ignore

        if hasattr(og, "executor") and og.executor:
            reorganize_tasks_architecture(og.executor)
        for a in args:
            if hasattr(a, "onetime_tasks"):
                reorganize_tasks_architecture(a)
        if "executor" in kwargs:
            reorganize_tasks_architecture(kwargs["executor"])

        # 2. 拦截 addSubInterface 收集所有原生的子 Tab，避免它们直接堆砌在左侧侧边栏
        pending_tabs: list[dict[str, Any]] = []

        def _collector_add_sub_interface(
            widget: Any, icon: Any, text: str, position: Any = None, parent: Any = None
        ) -> Any:
            pending_tabs.append(
                {
                    "widget": widget,
                    "icon": icon,
                    "text": text,
                    "position": position,
                    "parent": parent,
                }
            )
            return None

        self.addSubInterface = _collector_add_sub_interface

        try:
            orig_init(self, *args, **kwargs)
        finally:
            # 恢复原生的 addSubInterface
            self.addSubInterface = lambda widget, icon, text, position=None, parent=None: orig_add_sub_interface(
                self, widget, icon, text, position, parent
            )

        try:
            # 终末地硬直角不透明战术面板与 Windows Mica 亚面景冲突：
            # Mica 在每次主题切换后需重建 DWM 合成 (700ms+)，且被不透明背景完全遮挡，
            # 因此直接关闭，彻底消除切换时的窗口重绘停顿。
            with suppress(Exception):
                if self.isMicaEffectEnabled():
                    self.setMicaEffectEnabled(False)
                    logger.info("Mica effect disabled for instant theme switching")

            # 主窗口自绘背景改为终末地画布色，保证每个页面（含未自定义的页面）底色统一
            with suppress(Exception):
                self.setCustomBackgroundColor(LIGHT["canvas"], DARK["canvas"])

            # 导入终末地工作区容器
            from src.gui.components.EndfieldSectionMatrix import open_daily_settings_drawer
            from src.gui.EndfieldCombatTab import EndfieldCombatTab
            from src.gui.EndfieldWorkspaceTab import EndfieldWorkspaceTab

            # 「自动战斗」是 ok-ef 自己的页面，和收集到的原生 Tab 一起归类到五大顶级工作区
            pending_tabs.insert(
                0, {"widget": EndfieldCombatTab(), "icon": FluentIcon.PLAY, "text": self.app.tr("自动战斗")}
            )
            routed_tabs = route_endfield_tabs(pending_tabs, self.app.tr)

            # 导航顶部放 ok-ef 图标、名称与版本；工作区紧随其后从上往下排
            install_navigation_paint()
            navigation = self.navigationInterface
            navigation.panel.setIndicatorAnimationEnabled(False)
            window_icon = kwargs.get("icon", args[3] if len(args) > 3 else None)
            if not isinstance(window_icon, QIcon) or window_icon.isNull():
                window_icon = self.windowIcon()
            self._endfield_brand = EndfieldNavBrand(window_icon, str(getattr(self, "version", "") or ""))
            navigation.addWidget("endfield_brand", self._endfield_brand, position=NavigationItemPosition.TOP)
            self._endfield_tri_strip = EndfieldTriStrip(self)
            install_window_size_button(self)

            # 实例化五大终末地顶级工作区
            workspaces: list[EndfieldWorkspaceTab] = []
            # 日常任务卡片不再显示，它的总控字段改从页眉「一条龙设置」进入
            daily_settings = ("一条龙设置", lambda: open_daily_settings_drawer(self))
            sections = (
                ("ENVIRONMENT", "系统与环境", "SYSTEM", FluentIcon.SETTING, None, None),
                (
                    "DAILY",
                    "日常一条龙",
                    "DAILY",
                    FluentIcon.PLAY,
                    ("开始一条龙", start_daily_dispatch),
                    daily_settings,
                ),
                ("COMBAT", "业务与战斗", "OPERATIONS", FluentIcon.ACCEPT, None, None),
                ("AUTOMATION", "自动化调度", "AUTOMATION", FluentIcon.CALENDAR, None, None),
                ("DEVELOPER", "开发者工具", "DEVELOPER", FluentIcon.DEVELOPER_TOOLS, None, None),
            )
            for code, title, nav_code, icon, action, setting in sections:
                tabs = routed_tabs[code]
                if not tabs:
                    continue
                workspace = EndfieldWorkspaceTab(
                    code,
                    title,
                    tabs,
                    self,
                    primary_action=action,
                    secondary_action=setting,
                    run_task=find_daily_composite_task if code == "DAILY" else None,
                )
                item = self.addSubInterface(workspace, icon, self.app.tr(title), position=NavigationItemPosition.SCROLL)
                if item is not None:
                    style_navigation_item(item, f"{len(workspaces) + 1:02d} · {nav_code}")
                workspaces.append(workspace)

            self._endfield_workspaces = workspaces

            # 注入智能路由跳转：如果外部逻辑调用 switchTo(sub_tab)，自动路由至对应工作区并切换顶栏标签
            for ws in workspaces:
                ws.tab_bar.tabChanged.connect(lambda _index: _close_drawer())

            def _close_drawer() -> None:
                from src.gui.components.EndfieldTacticalDrawer import _DRAWERS

                entry = _DRAWERS.get(id(self))
                if entry is not None:
                    entry[0].close_drawer()

            def _patched_switch_to(target: Any) -> None:
                _close_drawer()
                for ws in self._endfield_workspaces:
                    if ws.has_child(target):
                        ws.switch_to_child(target)
                        orig_switch_to(self, ws)
                        return
                orig_switch_to(self, target)

            self.switchTo = _patched_switch_to

            # 注册为终末地样式下发目标（窗口级 QSS 比应用级快约 10 倍）
            register_endfield_window(self)

            # 监听主题变化，动态更新样式表与主题强调色
            def _on_theme_changed(th: Any) -> None:
                started = time.perf_counter()
                is_d = th == Theme.DARK
                # 静默同步强调色：不走 setThemeColor，避免触发第二次全树 QSS 重算
                try:
                    qconfig.set(
                        qconfig.themeColor,
                        QColor(ENDFIELD_ACCENT_DARK if is_d else ENDFIELD_ACCENT_LIGHT),
                        save=False,
                    )
                except Exception as exc:
                    logger.debug("Failed to sync theme color: %s", exc)
                update_application_theme_qss(th)
                logger.info("Endfield theme slot finished in %.1f ms", (time.perf_counter() - started) * 1000)

            self._endfield_theme_slot = _on_theme_changed  # 强引用防止被 GC
            try:
                qconfig.themeChanged.connect(self._endfield_theme_slot)
            except Exception as exc:
                logger.debug("Failed to connect themeChanged: %s", exc)

            # 统计整条切换链（含 qfluentwidgets 自身重算）的真实耗时，便于定位卡顿
            def _on_theme_finished() -> None:
                if not _theme_switch_start:
                    return
                elapsed = (time.perf_counter() - _theme_switch_start) * 1000
                logger.info("Endfield theme switch completed in %.1f ms", elapsed)

            self._endfield_theme_finished_slot = _on_theme_finished
            with suppress(Exception):
                qconfig.themeChangedFinished.connect(self._endfield_theme_finished_slot)
                qconfig.themeChanged.connect(_mark_theme_switch_start)

            # 首次初始化强制执行一次，确保当前主题与 themeColor 完全同步
            _on_theme_changed(qconfig.theme)

            if hasattr(self, "navigationInterface"):
                self.navigationInterface.setExpandWidth(216)

            logger.info("Endfield UI architecture applied successfully.")
        except Exception as e:
            logger.warning("Error applying Endfield UI architecture in MainWindow: %s", e)

    try:
        patch_theme_acceleration()
        install_control_paint()
        patch_widget_paint_bridge()
        patch_config_row_background()
        patch_ok_style_sheet_override()
        MainWindow._sync_system_accent_color = _patched_sync_system_accent_color
        MainWindow.__init__ = _patched_init
        patch_onetime_task_tab()
        patch_combat_config_forms()
        patch_about_content_layout()
        patch_window_layout()
        _PATCH_INSTALLED = True
        logger.info("Endfield UI Theme Patch installed successfully.")
    except Exception as exc:
        logger.warning("Failed to hook MainWindow with Endfield UI patch: %s", exc)


def patch_combat_config_forms() -> None:
    """Move battle fields out of task/account forms without changing their stored schema."""
    from ok.ui.qt.tasks.ConfigCard import ConfigContentMixin

    from src.gui.EndfieldConfigContent import visible_config_types
    from src.gui.GlobalConfigTab import GlobalConfigTab

    collect_cards = GlobalConfigTab._collect_cards
    if not getattr(collect_cards, "_endfield_patched", False):

        def _collect_without_battle(self: Any) -> list:
            return [entry for entry in collect_cards(self) if entry[0] != "战斗配置"]

        _collect_without_battle._endfield_patched = True  # type: ignore
        GlobalConfigTab._collect_cards = _collect_without_battle

    original = ConfigContentMixin._init_config_content
    if getattr(original, "_endfield_patched", False):
        return

    def _init_content(
        self: Any,
        task: Any,
        config: Any,
        default_config: Any,
        config_description: Any,
        config_type: Any,
    ) -> None:
        config_type = visible_config_types(task, config, config_type)
        original(self, task, config, default_config, config_description, config_type)
        for key, row in self.config_widget_by_key.items():
            grouped = key in self.sub_configs_rules
            row.setProperty("configGroup", grouped)
            # Project editors can wrap the native label row inside their own panel.
            label_row = getattr(row, "_row", row)
            title = getattr(label_row, "title", None)
            if title is None or not isinstance(title, QLabel):
                continue
            label_row.setProperty("configGroup", grouped)
            label_row.setProperty("subConfig", bool(row.property("subConfig")))
            title.setProperty("configGroup", grouped)
            # Visibility dependencies do not imply a separate visual group.
            label_row.layout.setContentsMargins(38 if row.property("subConfig") else 14, 10, 14, 10)
            label_row.setMinimumHeight(max(64, label_row.minimumHeight()))
            label_row.title_layout.setSpacing(6)
            for styled in (label_row, title):
                styled.style().unpolish(styled)
                styled.style().polish(styled)
                styled.updateGeometry()
        self.viewLayout.setContentsMargins(0, 0, 0, 0)
        self._adjust_config_content_size()

    original_adjust = ConfigContentMixin._adjust_config_content_size

    def _adjust_content(self: Any) -> None:
        first = True
        for index in range(self.viewLayout.count()):
            widget = self.viewLayout.itemAt(index).widget()
            if widget is None:
                continue
            row = getattr(widget, "_row", widget)
            is_first = first and not widget.isHidden()
            if row.property("configFirst") != is_first:
                row.setProperty("configFirst", is_first)
                row.style().unpolish(row)
                row.style().polish(row)
                row.update()
            if not widget.isHidden():
                first = False
        original_adjust(self)

    ConfigContentMixin._adjust_config_content_size = _adjust_content
    _init_content._endfield_patched = True  # type: ignore
    ConfigContentMixin._init_config_content = _init_content


def patch_about_content_layout() -> None:
    """Give project links and update status the available text width."""
    from ok.ui.qt.about.AboutTab import AboutTab
    from ok.ui.qt.about.LinksBar import LinksBar
    from ok.ui.qt.about.ProjectCard import ProjectCard
    from ok.ui.qt.about.UpdateCard import UpdateCard

    about_resize = AboutTab.resizeEvent
    if not getattr(about_resize, "_endfield_patched", False):

        def _about_resize(self: Any, event: Any) -> None:
            about_resize(self, event)
            group = getattr(self, "group", None)
            if group is None:
                return
            cards = group.findChildren(ProjectCard)
            if not cards:
                return
            columns = 2 if self.viewport().width() >= 1000 else 1
            if getattr(self, "_endfield_project_columns", None) == columns:
                return
            self._endfield_project_columns = columns
            grid = cards[0].parentWidget().layout()
            for card in cards:
                grid.removeWidget(card)
            for index, card in enumerate(cards):
                grid.addWidget(card, index // columns, index % columns)
            grid.setColumnStretch(1, 1 if columns == 2 else 0)
            group.updateGeometry()

        _about_resize._endfield_patched = True  # type: ignore
        AboutTab.resizeEvent = _about_resize

    links_init = LinksBar.__init__
    if not getattr(links_init, "_endfield_patched", False):

        def _links_init(self: Any, *args: Any, **kwargs: Any) -> None:
            links_init(self, *args, **kwargs)
            self._endfield_links = [(b, b.text(), b.sizeHint().width()) for b in self.findChildren(QPushButton)]
            self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            self.setMinimumWidth(0)
            for button, text, _ in self._endfield_links:
                button.setToolTip(text)

        links_resize = LinksBar.resizeEvent

        def _links_resize(self: Any, event: Any) -> None:
            links_resize(self, event)
            items = self._endfield_links
            required = sum(width for _, _, width in items) + len(items) * self.layout.spacing() + 20
            compact = self.width() < required
            for button, text, width in items:
                button.setText("" if compact else text)
                button.setFixedWidth(34 if compact else width)

        _links_init._endfield_patched = True  # type: ignore
        LinksBar.__init__ = _links_init
        LinksBar.resizeEvent = _links_resize

    project_init = ProjectCard.__init__
    if not getattr(project_init, "_endfield_patched", False):

        def _project_init(self: Any, *args: Any, **kwargs: Any) -> None:
            project_init(self, *args, **kwargs)
            self.contentLabel.setText(self.url)
            self.contentLabel.setMaximumWidth(16777215)
            self.contentLabel.setWordWrap(False)
            self.contentLabel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            self.titleLabel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            self.titleLabel.setToolTip(self.titleLabel.text())
            self.vBoxLayout.setAlignment(self.titleLabel, Qt.AlignmentFlag(0))
            self.vBoxLayout.setAlignment(self.contentLabel, Qt.AlignmentFlag(0))
            self.hBoxLayout.setStretch(self.hBoxLayout.indexOf(self.vBoxLayout), 1)
            for index in range(self.hBoxLayout.count()):
                if self.hBoxLayout.itemAt(index).spacerItem() is not None:
                    self.hBoxLayout.setStretch(index, 0)
            self.setFixedHeight(72)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            for label in (self.titleLabel, self.contentLabel):
                font = label.font()
                font.setPointSize(9)
                label.setFont(font)

        _project_init._endfield_patched = True  # type: ignore
        ProjectCard.__init__ = _project_init
        project_resize = ProjectCard.resizeEvent

        def _project_resize(self: Any, event: Any) -> None:
            project_resize(self, event)
            self.hBoxLayout.activate()
            width = max(0, self.vBoxLayout.geometry().width())
            for label, text in ((self.titleLabel, self.titleLabel.toolTip()), (self.contentLabel, self.url)):
                label.setText(label.fontMetrics().elidedText(text, Qt.ElideRight, width))

        ProjectCard.resizeEvent = _project_resize

    update_init = UpdateCard.__init__
    if not getattr(update_init, "_endfield_patched", False):

        def _update_init(self: Any, *args: Any, **kwargs: Any) -> None:
            update_init(self, *args, **kwargs)
            self.status_label.setMaximumWidth(16777215)
            self.controls_layout.setStretch(self.controls_layout.indexOf(self.status_label), 1)
            for index in range(self.controls_layout.count()):
                if self.controls_layout.itemAt(index).spacerItem() is not None:
                    self.controls_layout.setStretch(index, 0)

        _update_init._endfield_patched = True  # type: ignore
        UpdateCard.__init__ = _update_init


def patch_theme_acceleration() -> None:
    """包装 qfluentwidgets.setTheme 启用 lazy=True，避免全树同步重新计算 QSS 阻塞主线程。"""
    try:
        import qfluentwidgets  # type: ignore

        orig_set_theme = getattr(qfluentwidgets, "setTheme", None)
        if orig_set_theme and not getattr(orig_set_theme, "_endfield_patched", False):

            def _fast_set_theme(theme: Any, save: bool = False, lazy: bool = True) -> None:
                orig_set_theme(theme, save=save, lazy=True)

            _fast_set_theme._endfield_patched = True  # type: ignore
            qfluentwidgets.setTheme = _fast_set_theme  # type: ignore
            logger.info("qfluentwidgets.setTheme accelerated with lazy=True")
    except Exception as exc:
        logger.warning("Failed to patch setTheme acceleration: %s", exc)

    # 设置页直接 from qfluentwidgets import setTheme，绑定了原函数，需同步替换模块属性
    try:
        setting_tab_module = importlib.import_module("ok.ui.qt.settings.SettingTab")
        orig = getattr(setting_tab_module, "setTheme", None)
        if orig is not None and not getattr(orig, "_endfield_patched", False):

            def _fast_set_theme_tab(theme: Any, save: bool = False, lazy: bool = True) -> None:
                orig(theme, save=save, lazy=True)

            _fast_set_theme_tab._endfield_patched = True  # type: ignore
            setting_tab_module.setTheme = _fast_set_theme_tab  # type: ignore
            logger.info("SettingTab.setTheme accelerated with lazy=True")
    except Exception as exc:
        logger.debug("Failed to patch SettingTab.setTheme: %s", exc)


def patch_ok_style_sheet_override() -> None:
    """把终末地覆写规则追加到 ok 自己的控件级样式表面。

    ok 的 StyleSheet 直接继承 qfluentwidgets.StyleSheetBase，不经过
    StyleSheetCompose，因此 tab.qss / card.qss 里的圆角、透明值会盖掉窗口级 QSS。
    """
    try:
        from qfluentwidgets.common.config import isDarkTheme  # type: ignore

        orig_content = OkStyleSheet.content
        if getattr(orig_content, "_endfield_patched", False):
            return

        def _content(self: Any, theme: Any = Theme.AUTO) -> str:
            base = orig_content(self, theme)
            dark = isDarkTheme() if theme == Theme.AUTO else theme == Theme.DARK
            return base + "\n/* ENDFIELD-WIDGET-SCOPE */" + _widget_scope_override_qss(dark)

        _content._endfield_patched = True  # type: ignore
        OkStyleSheet.content = _content
        logger.info("Endfield ok stylesheet override installed")
    except Exception as exc:
        logger.debug("Failed to patch ok stylesheet content: %s", exc)


def patch_config_row_background() -> None:
    """让 ok 的配置行（#configRow，普通 QWidget）真正画出 QSS 分隔线。

    QWidget 子类不会自动绘制 QSS 的 background/border，需显式打开
    WA_StyledBackground；否则设置页“基本设置”展开后每行都看不出分隔。
    """
    try:
        from ok.ui.qt.settings.GlobalConfigTab import GlobalConfigTab
        from ok.ui.qt.tasks.LabelAndMultiSelection import LabelAndMultiSelection
        from ok.ui.qt.tasks.LabelAndWidget import LabelAndWidget  # type: ignore

        orig_init = LabelAndWidget.__init__
        if getattr(orig_init, "_endfield_patched", False):
            return

        def _patched_label_init(self: Any, *args: Any, **kwargs: Any) -> None:
            orig_init(self, *args, **kwargs)
            with suppress(Exception):
                self.setAttribute(Qt.WA_StyledBackground, True)
                self.layout.setStretch(0, 1)
                self.layout.setStretch(1, 0)
                self.title.setText(display_title(self.title.text()))
                self.title.setWordWrap(True)
                self.setMinimumHeight(64)
                self.title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
                self.title_layout.setSpacing(6)
                self.layout.setContentsMargins(14, 10, 14, 10)
                content = getattr(self, "contentLabel", None)
                if content is not None:
                    content.setMinimumWidth(0)

        original_add_widget = LabelAndWidget.add_widget
        original_add_layout = LabelAndWidget.add_layout

        def _add_widget(self: Any, widget: QWidget, stretch: int = 1) -> None:
            original_add_widget(self, widget, stretch=0)

        def _add_layout(self: Any, layout: Any, stretch: int = 1) -> None:
            original_add_layout(self, layout, stretch=0)

        LabelAndWidget.add_widget = _add_widget
        LabelAndWidget.add_layout = _add_layout
        _patched_label_init._endfield_patched = True  # type: ignore
        LabelAndWidget.__init__ = _patched_label_init

        original_multi_init = LabelAndMultiSelection.__init__

        class _OptionColumn(QWidget):
            def __init__(self, parent: QWidget) -> None:
                super().__init__(parent)
                self._height_timer = QTimer(self)
                self._height_timer.setSingleShot(True)
                self._height_timer.timeout.connect(self._sync_height)
                parent.installEventFilter(self)

            def _schedule_height(self) -> None:
                timer = getattr(self, "_height_timer", None)
                if timer is not None and not timer.isActive():
                    timer.start(0)

            def _sync_height(self) -> None:
                layout = self.layout()
                row = self.parentWidget()
                if layout is None or row is None:
                    return
                required = max(layout.minimumSize().height(), layout.totalHeightForWidth(self.width()))
                if self.minimumHeight() != required:
                    self.setMinimumHeight(required)
                margins = row.layout.contentsMargins()
                height = max(64, required + margins.top() + margins.bottom())
                if row.minimumHeight() != height:
                    row.setMinimumHeight(height)
                    row.updateGeometry()

            def event(self, event: Any) -> bool:
                result = super().event(event)
                if event.type() in (QEvent.LayoutRequest, QEvent.Resize, QEvent.FontChange, QEvent.StyleChange):
                    self._schedule_height()
                return result

            def eventFilter(self, watched: Any, event: Any) -> bool:
                if event.type() in (QEvent.LayoutRequest, QEvent.FontChange, QEvent.StyleChange) or (
                    event.type() == QEvent.Resize and event.size().width() != event.oldSize().width()
                ):
                    self._schedule_height()
                return super().eventFilter(watched, event)

        def _multi_init(self: Any, *args: Any, **kwargs: Any) -> None:
            original_multi_init(self, *args, **kwargs)
            old = self.content_layout
            column = _OptionColumn(self)
            column.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Minimum)
            column.setFixedWidth(180)
            rows = QVBoxLayout(column)
            rows.setContentsMargins(0, 0, 0, 0)
            rows.setSpacing(10)
            for option in self.check_boxes:
                option.setParent(column)
                option.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
                option.setMinimumHeight(24)
                option.checkbox.setFixedSize(20, 20)
                option.label.setObjectName("optionText")
                option.label.setWordWrap(True)
                option.label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
                option.layout().setContentsMargins(0, 0, 0, 0)
                option.layout().setSpacing(8)
                option.layout().setAlignment(option.label, Qt.AlignmentFlag(0))
                rows.addWidget(option)
            self.layout.replaceWidget(old, column)
            old.hide()
            old.deleteLater()
            self.content_layout = column
            self.layout.setStretch(0, 1)
            self.layout.setStretch(2, 0)
            self.layout.setAlignment(column, Qt.AlignRight | Qt.AlignVCenter)
            required = max(rows.minimumSize().height(), rows.totalHeightForWidth(180))
            column.setMinimumHeight(required)
            margins = self.layout.contentsMargins()
            self.setMinimumHeight(max(64, required + margins.top() + margins.bottom()))

        LabelAndMultiSelection.__init__ = _multi_init

        original_global_init = GlobalConfigTab.__init__

        def _global_init(self: Any, *args: Any, **kwargs: Any) -> None:
            original_global_init(self, *args, **kwargs)
            self.viewLayout.setContentsMargins(0, 0, 0, 0)
            for index in range(self.viewLayout.count()):
                row = self.viewLayout.itemAt(index).widget()
                if row is not None and not row.property("subConfig"):
                    row.setMinimumHeight(max(64, row.minimumHeight()))

        GlobalConfigTab.__init__ = _global_init
        logger.info("Endfield config row background bridge installed")
    except Exception as exc:
        logger.debug("Failed to patch config row background: %s", exc)


def patch_widget_paint_bridge() -> None:
    """接管 QFluentWidgets 自绘卡片背景，实现全站页面统一终末地样式。

    SettingCard / CardWidget / ExpandSettingCard 在 paintEvent 里用主题色自绘圆角背景，
    全局 QSS 无法作用于这些控件；ok 自带的 tab.qss / card.qss 又以控件级样式表下发，
    优先级高于应用级 QSS。这里一手接管 paintEvent 直接画硬直角面板，
    一手把终末地覆写规则追加到控件级样式表尾部，两道夹击保证全站配色一致。
    """
    try:
        from qfluentwidgets import CardWidget, SettingCard  # type: ignore
        from qfluentwidgets.common import style_sheet as qss_module  # type: ignore
        from qfluentwidgets.components.settings.expand_setting_card import (  # type: ignore
            ExpandBorderWidget,
            ExpandSettingCard,
            HeaderSettingCard,
        )

        def _paint_card_panel(target: Any, owner: Any) -> None:
            colors = palette(qconfig.theme == Theme.DARK)
            hovered = owner.underMouse()
            # 编号条目是面板里的一行：悬停换底色；其余独立面板悬停加深边框
            row = hasattr(owner, "_endfield_entry_index")
            painter = QPainter(target)
            rect = target.rect().adjusted(0, 0, -1, -1)
            painter.fillRect(rect, QColor(colors["row_hover"] if row and hovered else colors["panel"]))
            pen = QPen(QColor(colors["border_hover"] if hovered and not row else colors["border"]))
            pen.setWidth(1)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            if getattr(owner, "_endfield_join_top", False):
                # drawRect 的右、下边落在 width-1、height-1；折线要取完整矩形的外沿才能与之对齐
                edge = target.rect()
                painter.drawPolyline(QPolygon([edge.topLeft(), edge.bottomLeft(), edge.bottomRight(), edge.topRight()]))
            else:
                painter.drawRect(rect)
            painter.end()

        def _styled_paint(self: Any, _event: Any) -> None:
            target = self
            viewport_getter = getattr(self, "viewport", None)
            if callable(viewport_getter):
                viewport = viewport_getter()
                if isinstance(viewport, QWidget):
                    target = viewport
            _paint_card_panel(target, self)

        def _empty_paint(self: Any, _event: Any) -> None:
            return

        SettingCard.paintEvent = _styled_paint
        CardWidget.paintEvent = _styled_paint
        ExpandSettingCard.paintEvent = _styled_paint
        # QScrollArea paints on its viewport; the fixed header has its own panel.
        HeaderSettingCard.paintEvent = _styled_paint
        ExpandBorderWidget.paintEvent = _empty_paint

        if not getattr(SettingCard.__init__, "_endfield_entry_sized", False):
            original_setting_init = SettingCard.__init__

            def _setting_init(self: Any, *args: Any, **kwargs: Any) -> None:
                original_setting_init(self, *args, **kwargs)
                self.titleLabel.setText(display_title(self.titleLabel.text()))
                self.setFixedHeight(56)
                style_setting_header(self)

            _setting_init._endfield_entry_sized = True  # type: ignore
            SettingCard.__init__ = _setting_init  # type: ignore

        if not getattr(ExpandSettingCard.__init__, "_endfield_patched", False):
            orig_expand_init = ExpandSettingCard.__init__

            from src.gui.components.EndfieldTacticalDrawer import (
                _DRAWERS,
                card_has_configuration,
                open_card_drawer,
            )

            def _adjust_view_size(self: Any) -> None:
                # Native fields remain owned by this card while shown in the shared panel.
                self.setFixedHeight(self.card.height())
                self.card.expandButton.setVisible(card_has_configuration(self))
                if not self.isExpand:
                    self.view.hide()
                else:
                    layout = self.viewLayout
                    height = (
                        layout.totalHeightForWidth(max(1, self.view.width()))
                        if layout.hasHeightForWidth()
                        else layout.sizeHint().height()
                    )
                    self.view.setMinimumHeight(max(layout.minimumSize().height(), height))
                    self.view.updateGeometry()

            def _settle_height(self: Any) -> None:
                self.expandAni.stop()
                self._adjustViewSize()

            def _set_expand(self: Any, expanded: bool) -> None:
                if expanded:
                    # Some native pages request an initial expansion during construction.
                    if self.isVisible():
                        open_card_drawer(self)
                else:
                    entry = _DRAWERS.get(id(self.window()))
                    if entry is not None and entry[0]._source_card is self:
                        entry[0].close_drawer()

            orig_expand_resize = ExpandSettingCard.resizeEvent
            orig_expand_hide = ExpandSettingCard.hideEvent

            def _expand_resize(self: Any, event: Any) -> None:
                orig_expand_resize(self, event)
                self._adjustViewSize()

            def _expand_hide(self: Any, event: Any) -> None:
                _set_expand(self, False)
                orig_expand_hide(self, event)

            def _expand_init(self: Any, *args: Any, **kwargs: Any) -> None:
                orig_expand_init(self, *args, **kwargs)
                self.expandAni.stop()
                self.scrollLayout.removeWidget(self.spaceWidget)
                self.spaceWidget.hide()
                self.view.hide()
                style_expand_header(self)

            original_header_filter = HeaderSettingCard.eventFilter

            def _header_filter(self: Any, obj: Any, event: Any) -> bool:
                if obj is self and event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
                    return False
                return original_header_filter(self, obj, event)

            HeaderSettingCard.eventFilter = _header_filter

            _expand_init._endfield_patched = True  # type: ignore
            ExpandSettingCard._endfield_settle_height = _settle_height  # type: ignore
            ExpandSettingCard._adjustViewSize = _adjust_view_size  # type: ignore
            ExpandSettingCard.setExpand = _set_expand  # type: ignore
            ExpandSettingCard.resizeEvent = _expand_resize  # type: ignore
            ExpandSettingCard.hideEvent = _expand_hide  # type: ignore
            ExpandSettingCard.__init__ = _expand_init  # type: ignore
        from ok.ui.qt.tasks.TaskCard import TaskCard  # type: ignore

        if not getattr(TaskCard.__init__, "_endfield_patched", False):
            orig_taskcard_init = TaskCard.__init__

            def _taskcard_init(self: Any, *args: Any, **kwargs: Any) -> None:
                from src.gui.EndfieldPrimitives import EndfieldElidedLabel

                orig_taskcard_init(self, *args, **kwargs)
                for attr in ("titleLabel", "contentLabel"):
                    old = getattr(self.card, attr)
                    label = EndfieldElidedLabel(old.text(), self.card)
                    label.setObjectName(old.objectName())
                    label.setFont(old.font())
                    label.setAlignment(old.alignment())
                    label.setToolTip(old.text())
                    label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
                    self.header_text_layout.replaceWidget(old, label)
                    label.setVisible(not old.isHidden())
                    old.hide()
                    old.deleteLater()
                    setattr(self.card, attr, label)
                style_expand_header(self)
                update_task_status(self)
                apply_native_card_panel(self)

            _taskcard_init._endfield_patched = True  # type: ignore
            TaskCard.__init__ = _taskcard_init  # type: ignore
            original_update_buttons = TaskCard.update_buttons

            def _taskcard_update(self: Any, task: Any) -> None:
                original_update_buttons(self, task)
                update_task_status(self)

            TaskCard.update_buttons = _taskcard_update  # type: ignore

        from ok.ui.qt.widget.Card import Card

        if not getattr(Card.__init__, "_endfield_patched", False):
            original_card_init = Card.__init__

            def _panel_init(self: Any, *args: Any, **kwargs: Any) -> None:
                original_card_init(self, *args, **kwargs)
                style_panel_title(self)

            _panel_init._endfield_patched = True  # type: ignore
            Card.__init__ = _panel_init  # type: ignore

        logger.info("Endfield card paint bridge installed")

        # Every FluentStyleSheet entry passes here both at construction and on theme refresh.
        # Compose alone misses the initial direct setStyleSheet(source) call.
        orig_fluent_content = qss_module.FluentStyleSheet.content
        if not getattr(orig_fluent_content, "_endfield_patched", False):
            from qfluentwidgets.common.config import isDarkTheme  # type: ignore

            def _fluent_content(self: Any, theme: Any = Theme.AUTO) -> str:
                base = orig_fluent_content(self, theme)
                dark = isDarkTheme() if theme == Theme.AUTO else theme == Theme.DARK
                return base + "\n/* ENDFIELD-WIDGET-SCOPE */" + _widget_scope_override_qss(dark)

            _fluent_content._endfield_patched = True  # type: ignore
            qss_module.FluentStyleSheet.content = _fluent_content  # type: ignore
            logger.info("Endfield widget-scope QSS override installed")
    except Exception as exc:
        logger.warning("Failed to install card paint bridge: %s", exc)
