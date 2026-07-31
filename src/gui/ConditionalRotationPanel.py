from __future__ import annotations

from PySide6.QtCore import Qt, QPoint, QSize, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QListView, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    Action, ComboBox, FluentIcon, IndicatorPosition, MessageBox, MessageBoxBase,
    PushButton, RoundMenu, SpinBox, SubtitleLabel, SwitchButton,
    TransparentToolButton,
)
from ok import og
from ok.gui.common.design_system import DesignToken
from ok.gui.tasks.LabelAndWidget import LabelAndWidget

from src.core.BattleConfig import BATTLE_CONFIG_NAME
from src.core.global_config_store import get_global_config
from src.core.rotation_ast import normalize_ast


def _tr(text: str) -> str:
    return og.app.tr(text)


# ── 友好名映射 ───────────────────────────────────────────
_ACTION_DISPLAY = {
    "1": "战技 1", "2": "战技 2", "3": "战技 3", "4": "战技 4", "e": "连携技",
    "ult_1": "终结技 1", "ult_2": "终结技 2", "ult_3": "终结技 3", "ult_4": "终结技 4",
}
_ATOM_DISPLAY = {
    "ult1": "终结技 1 可用", "ult2": "终结技 2 可用",
    "ult3": "终结技 3 可用", "ult4": "终结技 4 可用",
    "link": "连携技可用",
}

_ACTION_OPTIONS = [  # (token, 显示名)
    ("1", "战技 1"), ("2", "战技 2"), ("3", "战技 3"), ("4", "战技 4"), ("e", "连携技"),
    ("ult_1", "终结技 1"), ("ult_2", "终结技 2"), ("ult_3", "终结技 3"), ("ult_4", "终结技 4"),
]
_SLEEP_KEY = "__sleep__"
_NORMAL_KEY = "__normal__"

_ATOM_OPTIONS = [  # (原子值, 显示名)
    ("ult1", "终结技 1 可用"), ("ult2", "终结技 2 可用"),
    ("ult3", "终结技 3 可用"), ("ult4", "终结技 4 可用"), ("link", "连携技可用"),
]
_SKILL_KEY = "__skill__"

_CARD_STYLE = """
QFrame#condCard {
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 6px;
    background-color: rgba(255, 255, 255, 0.03);
}
QFrame#condCard[selected="true"] {
    border: 1px solid rgba(0, 120, 215, 0.85);
    background-color: rgba(0, 120, 215, 0.08);
}
QFrame#divider {
    color: rgba(255, 255, 255, 0.08);
    background-color: rgba(255, 255, 255, 0.08);
}
QLabel#condLine, QLabel#actionLine {
    color: rgba(255, 255, 255, 0.90);
    font-size: 14px;
}
"""

_LIST_STYLE = """
QListWidget {
    background: transparent;
    border: none;
    outline: none;
}
QListWidget::item {
    background: transparent;
    padding: 0px;
    margin: 0px;
}
QListWidget::item:selected {
    background: rgba(0, 120, 215, 0.12);
    border-radius: 4px;
}
"""


def _fmt_action(token: str) -> str:
    if token in _ACTION_DISPLAY:
        return _tr(_ACTION_DISPLAY[token])
    if token.startswith("sleep_"):
        return _tr("等待") + token[6:] + _tr("秒")
    if token.startswith("normal_"):
        return _tr("普通") + token[7:] + _tr("秒")
    return token


def _fmt_atom(atom: str) -> str:
    if atom in _ATOM_DISPLAY:
        return _tr(_ATOM_DISPLAY[atom])
    if atom.startswith("skill>="):
        return _tr("技力≥") + atom[7:]
    return atom


def _fmt_cond(cond) -> tuple[str, str]:
    """返回 (类型标签, 条件描述)。"""
    if isinstance(cond, str):
        return _tr("满足条件"), _fmt_atom(cond)
    if isinstance(cond, dict):
        if "all" in cond:
            subs = [c for c in cond.get("all", []) if isinstance(c, str)]
            return _tr("全部满足"), ";".join(_fmt_atom(c) for c in subs) if subs else _tr("无")
        if "any" in cond:
            subs = [c for c in cond.get("any", []) if isinstance(c, str)]
            return _tr("任一满足"), ";".join(_fmt_atom(c) for c in subs) if subs else _tr("无")
    return _tr("满足条件"), _tr("未知")


def _fmt_actions(actions) -> str:
    if not isinstance(actions, list) or not actions:
        return _tr("无")
    return ";".join(_fmt_action(a) for a in actions if isinstance(a, str))


# ── 弹窗内：单行动作编辑 ─────────────────────────────────
class _ActionRow(QWidget):
    """弹窗内一行动作编辑：ComboBox + SpinBox(sleep/normal) + 删除按钮。"""

    changed = Signal()
    remove_requested = Signal(object)

    def __init__(self, token: str, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        self.combo = ComboBox()
        for t, disp in _ACTION_OPTIONS:
            self.combo.addItem(_tr(disp), userData=t)
        self.combo.addItem(_tr("等待 N 秒"), userData=_SLEEP_KEY)
        self.combo.addItem(_tr("普通战斗 N 秒"), userData=_NORMAL_KEY)
        self.combo.currentIndexChanged.connect(lambda *_: (self._update_spin_visibility(), self.changed.emit()))
        row.addWidget(self.combo, 1)

        self.spin = SpinBox()
        self.spin.setRange(1, 999)
        self.spin.setSingleStep(1)
        self.spin.setValue(1)
        self.spin.setVisible(False)
        self.spin.valueChanged.connect(lambda *_: self.changed.emit())
        row.addWidget(self.spin)

        del_btn = TransparentToolButton(FluentIcon.DELETE)
        del_btn.setFixedSize(30, 30)
        del_btn.setToolTip(_tr("删除"))
        del_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        row.addWidget(del_btn)

        self._apply_token(token)

    def _update_spin_visibility(self):
        self.spin.setVisible(self.combo.currentData() in (_SLEEP_KEY, _NORMAL_KEY))

    def _apply_token(self, token: str):
        if not isinstance(token, str):
            token = "1"
        if token.startswith("sleep_"):
            self.combo.setCurrentIndex(self.combo.findData(_SLEEP_KEY))
            self.spin.setVisible(True)
            try:
                self.spin.setValue(max(1, int(float(token[6:]))))
            except (ValueError, TypeError):
                pass
        elif token.startswith("normal_"):
            self.combo.setCurrentIndex(self.combo.findData(_NORMAL_KEY))
            self.spin.setVisible(True)
            try:
                self.spin.setValue(max(1, int(float(token[7:]))))
            except (ValueError, TypeError):
                pass
        else:
            idx = self.combo.findData(token)
            self.combo.setCurrentIndex(idx if idx >= 0 else 0)
            self.spin.setVisible(False)

    def to_token(self) -> str:
        data = self.combo.currentData()
        if data == _SLEEP_KEY:
            return f"sleep_{self.spin.value()}"
        if data == _NORMAL_KEY:
            return f"normal_{self.spin.value()}"
        return data if isinstance(data, str) else "1"


# ── 弹窗内：动作列表编辑（QListWidget 支持拖拽排序） ──────
class _ActionListEditor(QWidget):
    """弹窗内动作列表编辑：标签 + 可拖拽列表 + 添加按钮。"""

    changed = Signal()

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)

        self.label = QLabel(label)
        self.label.setStyleSheet("color: rgba(255,255,255,0.6); font-size: 12px;")
        self._layout.addWidget(self.label)

        self._list = QListWidget()
        self._list.setDragDropMode(QAbstractItemView.InternalMove)
        self._list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._list.setDefaultDropAction(Qt.MoveAction)
        self._list.setMovement(QListView.Static)
        self._list.setFrameShape(QFrame.NoFrame)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._list.setSpacing(2)
        self._list.setStyleSheet(_LIST_STYLE)
        self._list.model().rowsMoved.connect(lambda *_: self.changed.emit())
        self._layout.addWidget(self._list)

        self.add_btn = PushButton(FluentIcon.ADD, _tr("添加动作"))
        self.add_btn.clicked.connect(lambda: self._add_row("1"))
        self._layout.addWidget(self.add_btn)

    def load(self, tokens: list):
        self._list.clear()
        for t in (tokens if isinstance(tokens, list) else []):
            self._add_row(t, emit=False)

    def _add_row(self, token: str, emit: bool = True):
        row = _ActionRow(token)
        row.changed.connect(self.changed)
        row.remove_requested.connect(self._remove_row_widget)
        item = QListWidgetItem()
        # 固定行高，确保内容完整可见
        item.setSizeHint(QSize(0, 36))
        self._list.addItem(item)
        self._list.setItemWidget(item, row)
        if emit:
            self.changed.emit()

    def _remove_row_widget(self, row_widget: _ActionRow):
        for i in range(self._list.count()):
            item = self._list.item(i)
            if self._list.itemWidget(item) is row_widget:
                self._list.takeItem(i)
                row_widget.deleteLater()
                self.changed.emit()
                return

    def to_list(self) -> list:
        result = []
        for i in range(self._list.count()):
            row = self._list.itemWidget(self._list.item(i))
            if row is not None:
                result.append(row.to_token())
        return result


# ── 弹窗内：条件编辑器 ───────────────────────────────────
class _ConditionEditor(QWidget):
    """条件编辑器：原子 / 且(all) / 或(any)，且/或支持多个原子。"""

    changed = Signal()

    def __init__(self, cond, parent=None):
        super().__init__(parent)
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(4)

        self.type_combo = ComboBox()
        self.type_combo.addItem(_tr("满足条件"), userData="atom")
        self.type_combo.addItem(_tr("全部满足 (且)"), userData="all")
        self.type_combo.addItem(_tr("任一满足 (或)"), userData="any")
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        self._root.addWidget(self.type_combo)

        self._atom_box = QWidget()
        self._atom_layout = QVBoxLayout(self._atom_box)
        self._atom_layout.setContentsMargins(0, 0, 0, 0)
        self._atom_layout.setSpacing(4)
        self._root.addWidget(self._atom_box)

        self.add_atom_btn = PushButton(FluentIcon.ADD, _tr("添加条件"))
        self.add_atom_btn.clicked.connect(lambda: self._add_atom_row("link", emit=True))
        self._root.addWidget(self.add_atom_btn)

        self._atom_rows = []
        self._load(cond)

    def _load(self, cond):
        if isinstance(cond, str):
            self.type_combo.setCurrentIndex(0)
            self._set_atoms([cond])
            self.add_atom_btn.setVisible(False)
        elif isinstance(cond, dict):
            if "all" in cond and isinstance(cond.get("all"), list):
                self.type_combo.setCurrentIndex(1)
                atoms = [c for c in cond["all"] if isinstance(c, str)]
                self._set_atoms(atoms or ["link"])
                self.add_atom_btn.setVisible(True)
            elif "any" in cond and isinstance(cond.get("any"), list):
                self.type_combo.setCurrentIndex(2)
                atoms = [c for c in cond["any"] if isinstance(c, str)]
                self._set_atoms(atoms or ["link"])
                self.add_atom_btn.setVisible(True)
            else:
                self.type_combo.setCurrentIndex(0)
                self._set_atoms(["link"])
                self.add_atom_btn.setVisible(False)
        else:
            self.type_combo.setCurrentIndex(0)
            self._set_atoms(["link"])
            self.add_atom_btn.setVisible(False)

    def _set_atoms(self, atoms: list):
        for row in self._atom_rows:
            self._atom_layout.removeWidget(row["widget"])
            row["widget"].deleteLater()
        self._atom_rows = []
        for a in atoms:
            self._add_atom_row(a, emit=False)

    def _add_atom_row(self, value: str, emit: bool = True):
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        combo = ComboBox()
        for v, disp in _ATOM_OPTIONS:
            combo.addItem(_tr(disp), userData=v)
        combo.addItem(_tr("技力 ≥ N"), userData=_SKILL_KEY)

        spin = SpinBox()
        spin.setRange(1, 3)
        spin.setSingleStep(1)
        spin.setValue(2)
        spin.setVisible(False)

        def on_combo_changed(*_):
            spin.setVisible(combo.currentData() == _SKILL_KEY)
            self.changed.emit()

        combo.currentIndexChanged.connect(on_combo_changed)
        spin.valueChanged.connect(lambda *_: self.changed.emit())
        self._apply_atom_value(combo, spin, value)

        del_btn = TransparentToolButton(FluentIcon.DELETE)
        del_btn.setFixedSize(30, 30)
        del_btn.setToolTip(_tr("删除条件"))
        del_btn.clicked.connect(lambda: self._remove_atom(widget))

        row.addWidget(combo, 1)
        row.addWidget(spin)
        row.addWidget(del_btn)
        self._atom_layout.addWidget(widget)
        self._atom_rows.append({"combo": combo, "spin": spin, "widget": widget})
        if emit:
            self.changed.emit()

    def _remove_atom(self, widget):
        if len(self._atom_rows) <= 1:
            return
        for i, r in enumerate(self._atom_rows):
            if r["widget"] is widget:
                self._atom_layout.removeWidget(widget)
                widget.deleteLater()
                self._atom_rows.pop(i)
                break
        self.changed.emit()

    @staticmethod
    def _apply_atom_value(combo, spin, value: str):
        if not isinstance(value, str):
            value = "link"
        if value.startswith("skill>="):
            try:
                n = int(value[7:])
                if 1 <= n <= 3:
                    combo.setCurrentIndex(combo.findData(_SKILL_KEY))
                    spin.setValue(n)
                    spin.setVisible(True)
                    return
            except (ValueError, TypeError):
                pass
        idx = combo.findData(value)
        combo.setCurrentIndex(idx if idx >= 0 else combo.findData("link"))
        spin.setVisible(False)

    def _on_type_changed(self):
        typ = self.type_combo.currentData()
        if typ == "atom" and len(self._atom_rows) > 1:
            first = self._atom_rows[0]
            self._set_atoms([self._atom_to_value(first)])
        elif typ in ("all", "any") and not self._atom_rows:
            self._add_atom_row("link", emit=False)
        self.add_atom_btn.setVisible(typ in ("all", "any"))
        self.changed.emit()

    @staticmethod
    def _atom_to_value(r) -> str:
        combo, spin = r["combo"], r["spin"]
        data = combo.currentData()
        if data == _SKILL_KEY:
            return f"skill>={spin.value()}"
        return data if isinstance(data, str) else "link"

    def to_cond(self):
        typ = self.type_combo.currentData()
        if typ == "all":
            atoms = [self._atom_to_value(r) for r in self._atom_rows]
            return {"all": atoms}
        if typ == "any":
            atoms = [self._atom_to_value(r) for r in self._atom_rows]
            return {"any": atoms}
        # atom mode: use only the first row
        if self._atom_rows:
            return self._atom_to_value(self._atom_rows[0])
        return "link"


# ── 编辑弹窗 ─────────────────────────────────────────────
class _ConditionEditDialog(MessageBoxBase):
    """条件块编辑弹窗（项目 MessageBoxBase 范式）。"""

    def __init__(self, node: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_tr("编辑条件块"))
        node = node if isinstance(node, dict) else {}
        cond = node.get("if", "link")
        then_nodes = node.get("then", []) if isinstance(node.get("then"), list) else []
        else_nodes = node.get("else", []) if isinstance(node.get("else"), list) else []

        self.titleLabel = SubtitleLabel(_tr("编辑条件块"), self)
        self.viewLayout.addWidget(self.titleLabel)

        self.cond_editor = _ConditionEditor(cond)
        self.viewLayout.addWidget(self.cond_editor)

        self.then_editor = _ActionListEditor(_tr("运行："))
        self.then_editor.load(then_nodes)
        self.viewLayout.addWidget(self.then_editor)

        self.else_editor = _ActionListEditor(_tr("否则："))
        self.else_editor.load(else_nodes)
        self.viewLayout.addWidget(self.else_editor)

        self.yesButton.setText(_tr("确定"))
        self.cancelButton.setText(_tr("取消"))
        # 固定 centerWidget 宽度 + 预计算尺寸，避免 exec() 时尺寸跳变闪屏
        self.widget.setFixedWidth(460)
        self.widget.adjustSize()

    def to_node(self) -> dict:
        result = {"if": self.cond_editor.to_cond(), "then": self.then_editor.to_list()}
        else_list = self.else_editor.to_list()
        if else_list:
            result["else"] = else_list
        return result


# ── 只读显示卡片 ─────────────────────────────────────────
class _ConditionDisplayCard(QFrame):
    """只读显示卡片：两行（条件行 / 动作行），行间浅色线，右键菜单。"""

    selected = Signal(object)      # emit self
    right_clicked = Signal(object, QPoint)  # emit self, global_pos

    def __init__(self, node: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("condCard")
        self.setProperty("selected", False)
        self.setStyleSheet(_CARD_STYLE)
        self._node = node
        self._build()
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self.right_clicked.emit(self, self.mapToGlobal(pos))
        )

    def _build(self):
        layout = self.layout()
        if layout is None:
            layout = QVBoxLayout(self)
            layout.setContentsMargins(14, 10, 14, 10)
            layout.setSpacing(8)

        node = self._node if isinstance(self._node, dict) else {}
        cond = node.get("if", "link")
        then_nodes = node.get("then", [])

        type_label, cond_desc = _fmt_cond(cond)
        self.cond_label = QLabel(f"{type_label}  {cond_desc}  {_tr('时')}")
        self.cond_label.setObjectName("condLine")
        self.cond_label.setWordWrap(True)
        layout.addWidget(self.cond_label)

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFrameShape(QFrame.HLine)
        divider.setFixedHeight(1)
        layout.addWidget(divider)

        action_text = _tr("运行") + " " + _fmt_actions(then_nodes)
        self.action_label = QLabel(action_text)
        self.action_label.setObjectName("actionLine")
        self.action_label.setWordWrap(True)
        layout.addWidget(self.action_label)

    def update_node(self, node: dict):
        """更新节点数据并刷新显示。"""
        self._node = node
        layout = self.layout()
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._build()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.selected.emit(self)
        super().mousePressEvent(event)

    def set_selected(self, selected: bool):
        self.setProperty("selected", selected)
        self.style().polish(self)


# ── 主面板 ───────────────────────────────────────────────
class ConditionalRotationPanel(QWidget):
    """「实时条件」(条件排轴) 可视化面板。

    - 标题行：LabelAndWidget + SwitchButton（文字「是/否」跟随语言）。
    - 立即释放终结技 / 立即释放连携技 两个开关行。
    - 工具栏：添加 / 编辑 / 删除 / 清空（删除与清空二次确认）。
    - 卡片流：只读显示卡片（两行：条件行 + 动作行），右键弹操作菜单。
    - 编辑经弹窗（_ConditionEditDialog）完成。
    - 即时写盘；开关仅控制配置值，关闭时仍可编辑。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config = get_global_config(BATTLE_CONFIG_NAME)
        self._cards: list[_ConditionDisplayCard] = []
        self._selected_card: _ConditionDisplayCard | None = None
        self._loading = False
        self._setup_ui()
        self._load()

    # ------------------------------------------------------------------ UI
    def _setup_ui(self):
        self.setObjectName("ConditionalRotationPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 标题行：实时条件
        header = LabelAndWidget(
            "实时条件",
            "根据实时情况释放技能\n启用时自动忽略排轴配置",
        )
        self.switch = SwitchButton(indicatorPos=IndicatorPosition.RIGHT)
        self.switch.setOnText(_tr("是"))
        self.switch.setOffText(_tr("否"))
        self.switch.checkedChanged.connect(lambda c: self._set_config("启用条件排轴", c))
        header.add_widget(self.switch, stretch=0)
        layout.addWidget(header)

        # 立即释放终结技
        ult_row = LabelAndWidget(
            "立即释放终结技",
            "在没有运行任何条件动作时生效\n当终结技可释放时立刻释放终结技",
        )
        self.ult_switch = SwitchButton(indicatorPos=IndicatorPosition.RIGHT)
        self.ult_switch.setOnText(_tr("是"))
        self.ult_switch.setOffText(_tr("否"))
        self.ult_switch.checkedChanged.connect(lambda c: self._set_config("立即释放终结技", c))
        ult_row.add_widget(self.ult_switch, stretch=0)
        layout.addWidget(ult_row)

        # 立即释放连携技
        link_row = LabelAndWidget(
            "立即释放连携技",
            "在没有运行任何条件动作时生效\n当连携技可释放时立刻释放连携技",
        )
        self.link_switch = SwitchButton(indicatorPos=IndicatorPosition.RIGHT)
        self.link_switch.setOnText(_tr("是"))
        self.link_switch.setOffText(_tr("否"))
        self.link_switch.checkedChanged.connect(lambda c: self._set_config("立即释放连携技", c))
        link_row.add_widget(self.link_switch, stretch=0)
        layout.addWidget(link_row)

        # 工具栏 + 卡片流
        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(
            DesignToken.ROW_HORIZONTAL_PADDING, 8,
            DesignToken.ROW_HORIZONTAL_PADDING, 8,
        )
        body_layout.setSpacing(12)

        # 左侧工具栏
        toolbar = QVBoxLayout()
        toolbar.setSpacing(8)

        self.add_btn = PushButton(FluentIcon.ADD, _tr("添加"))
        self.add_btn.clicked.connect(self._on_add)
        toolbar.addWidget(self.add_btn)

        self.edit_btn = PushButton(FluentIcon.EDIT, _tr("编辑"))
        self.edit_btn.clicked.connect(self._on_edit)
        toolbar.addWidget(self.edit_btn)

        self.del_btn = PushButton(FluentIcon.DELETE, _tr("删除"))
        self.del_btn.clicked.connect(self._on_delete)
        toolbar.addWidget(self.del_btn)

        self.clear_btn = PushButton(FluentIcon.BROOM, _tr("清空"))
        self.clear_btn.clicked.connect(self._on_clear)
        toolbar.addWidget(self.clear_btn)

        toolbar.addStretch(1)
        body_layout.addLayout(toolbar)

        # 右侧卡片流容器
        self._cards_box = QVBoxLayout()
        self._cards_box.setContentsMargins(0, 0, 0, 0)
        self._cards_box.setSpacing(6)
        self._cards_box.addStretch(1)
        body_layout.addLayout(self._cards_box, 1)

        layout.addWidget(body)

    # -------------------------------------------------------------- 数据绑定
    def _load(self):
        self._loading = True
        self.switch.setChecked(bool(self.config.get("启用条件排轴", False)))
        self.ult_switch.setChecked(bool(self.config.get("立即释放终结技", False)))
        self.link_switch.setChecked(bool(self.config.get("立即释放连携技", False)))
        raw_ast = self.config.get("条件排轴序列", [])
        clean_ast, warnings = normalize_ast(raw_ast)
        for w in warnings:
            self._log(w)
        self._render(clean_ast)
        self._loading = False
        self._notify_resize()

    def _render(self, ast: list):
        # 清空旧卡片
        for card in self._cards:
            self._cards_box.removeWidget(card)
            card.deleteLater()
        self._cards = []
        self._selected_card = None
        for node in (ast if isinstance(ast, list) else []):
            self._add_card_widget(node, notify=False)
        self._notify_resize()

    def _add_card_widget(self, node, notify: bool = True) -> _ConditionDisplayCard:
        card = _ConditionDisplayCard(node)
        card.selected.connect(self._on_card_selected)
        card.right_clicked.connect(self._on_card_right_clicked)
        # 插在 stretch 之前
        self._cards_box.insertWidget(self._cards_box.count() - 1, card)
        self._cards.append(card)
        if notify:
            self._notify_resize()
        return card

    def _remove_card(self, card: _ConditionDisplayCard, notify: bool = True):
        if card in self._cards:
            self._cards.remove(card)
            self._cards_box.removeWidget(card)
            card.deleteLater()
            if self._selected_card is card:
                self._selected_card = None
            if notify:
                self._notify_resize()

    def _on_card_selected(self, card: _ConditionDisplayCard):
        for c in self._cards:
            c.set_selected(c is card)
        self._selected_card = card

    def _on_card_right_clicked(self, card: _ConditionDisplayCard, global_pos: QPoint):
        self._on_card_selected(card)
        menu = RoundMenu()
        menu.addAction(Action(FluentIcon.ADD, _tr("添加"), triggered=self._on_add))
        menu.addAction(Action(FluentIcon.EDIT, _tr("编辑"), triggered=self._on_edit))
        menu.addAction(Action(FluentIcon.DELETE, _tr("删除"), triggered=self._on_delete))
        menu.exec(global_pos)

    # -------------------------------------------------------------- 工具栏
    def _on_add(self):
        # 直接添加条件块，弹窗编辑
        new_node = {"if": "link", "then": []}
        dlg = _ConditionEditDialog(new_node, self.window())
        if dlg.exec():
            node = dlg.to_node()
            self._add_card_widget(node)
            self._save()

    def _on_edit(self):
        if self._selected_card is None:
            return
        dlg = _ConditionEditDialog(self._selected_card._node, self.window())
        if dlg.exec():
            node = dlg.to_node()
            self._selected_card.update_node(node)
            self._save()

    def _on_delete(self):
        if self._selected_card is None:
            return
        card = self._selected_card
        box = MessageBox(_tr("确认删除"), _tr("确定删除选中的条件块？"), self.window())
        if box.exec():
            self._remove_card(card)
            self._save()

    def _on_clear(self):
        if not self._cards:
            return
        box = MessageBox(_tr("确认清空"), _tr("确定清空所有条件块？"), self.window())
        if box.exec():
            for card in list(self._cards):
                self._remove_card(card, notify=False)
            self._notify_resize()
            self._save()

    def _set_config(self, key: str, value):
        if self._loading:
            return
        self.config[key] = value

    # -------------------------------------------------------------- 写盘 / 布局
    def _save(self):
        if self._loading:
            return
        ast = []
        for card in self._cards:
            node = card._node
            ast.append(node)
        self.config["条件排轴序列"] = ast

    def _notify_resize(self):
        """内容增删后通知祖先 ConfigCard 重新计算展开高度。"""
        self.updateGeometry()
        p = self.parent()
        while p is not None:
            if hasattr(p, "_adjust_config_content_size"):
                try:
                    p._adjust_config_content_size()
                except Exception:
                    pass
                break
            p = p.parent()

    def _log(self, msg: str):
        try:
            og.app.logger.info(f"实时条件面板: {msg}")
        except Exception:
            pass
