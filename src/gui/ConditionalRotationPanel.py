from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDoubleSpinBox, QFrame, QGraphicsOpacityEffect,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QListView,
    QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    Action, ComboBox, FluentIcon, IndicatorPosition,
    MessageBox, MessageBoxBase, PushButton, RoundMenu, SpinBox,
    SubtitleLabel, SwitchButton, TransparentToolButton,
)
from ok import og
from ok.gui.tasks.LabelAndWidget import LabelAndWidget

from src.core.BattleConfig import (
    BATTLE_CONFIG_DESCRIPTION,
    BATTLE_CONFIG_NAME,
    KEY_COND_ENABLED,
    KEY_COND_SEQUENCE,
    KEY_INSTANT_LINK,
    KEY_INSTANT_ULT,
)
from src.core.global_config_store import get_global_config
from src.core.rotation_ast import normalize_ast


def _tr(text: str) -> str:
    return og.app.tr(text)


# 友好名映射
_ACTION_DISPLAY = {
    "1": "战技 1", "2": "战技 2", "3": "战技 3", "4": "战技 4", "e": "连携技",
    "ult_1": "终结技 1", "ult_2": "终结技 2", "ult_3": "终结技 3", "ult_4": "终结技 4",
}
_ATOM_DISPLAY = {
    "ult1": "终结技 1 可用", "ult2": "终结技 2 可用",
    "ult3": "终结技 3 可用", "ult4": "终结技 4 可用",
    "link": "连携技可用",
}

# 动作选项 key（带 SpinBox 选择数字，模式同"等待 N 秒"）
_SKILL_ACTION_KEY = "__skill_action__"
_ULT_ACTION_KEY = "__ult_action__"
_SLEEP_KEY = "__sleep__"
_NORMAL_KEY = "__normal__"

_ACTION_OPTIONS = [  # (key, 显示名)
    (_SKILL_ACTION_KEY, "战技 N"), ("e", "连携技"),
    (_ULT_ACTION_KEY, "终结技 N"),
    (_SLEEP_KEY, "等待 N 秒"), (_NORMAL_KEY, "普通战斗 N 秒"),
]

# 条件原子选项 key（带 SpinBox 选择数字）
_ULT_KEY = "__ult__"
_SKILL_KEY = "__skill__"

_ATOM_OPTIONS = [  # (key, 显示名)
    (_ULT_KEY, "终结技 N 可用"), ("link", "连携技可用"), (_SKILL_KEY, "技力 ≥ N"),
]

# 模板映射：key → 下拉列表中的模板文字
_ACTION_TEMPLATES = dict(_ACTION_OPTIONS)
_ATOM_TEMPLATES = dict(_ATOM_OPTIONS)

_CARD_STYLE = """
QFrame#condCard {
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 6px;
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
    background: transparent;
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


def _fmt_float(v: float) -> str:
    """格式化浮点数：去掉尾部多余的零（1.0 → "1", 1.5 → "1.5"）。"""
    s = f"{v:.5g}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def _fmt_actions(actions) -> str:
    if not isinstance(actions, list) or not actions:
        return _tr("无")
    return ";".join(_fmt_action(a) for a in actions if isinstance(a, str))


# 动作选项的显示名模板
_ACTION_LABELS = {
    _SKILL_ACTION_KEY: "战技",
    _ULT_ACTION_KEY: "终结技",
    _SLEEP_KEY: "等待",
    _NORMAL_KEY: "普通战斗",
}

# 条件原子的显示名模板
_ATOM_LABELS = {
    _ULT_KEY: "终结技",
    _SKILL_KEY: "技力≥",
}


def _combo_display(key, spin) -> str:
    """根据 key + spin 值生成 ComboBox 当前显示文本。"""
    if key in (_SLEEP_KEY, _NORMAL_KEY):
        return _tr(_ACTION_LABELS[key]) + " " + _fmt_float(spin.value()) + " " + _tr("秒")
    if key in (_SKILL_ACTION_KEY, _ULT_ACTION_KEY):
        return _tr(_ACTION_LABELS[key]) + " " + str(int(spin.value()))
    if key == _ULT_KEY:
        return _tr("终结技") + " " + str(spin.value()) + " " + _tr("可用")
    if key == _SKILL_KEY:
        return _tr("技力≥") + str(spin.value())
    if key == "e":
        return _tr("连携技")
    if key == "link":
        return _tr("连携技可用")
    return key


# 弹窗内: 单行动作编辑
class _ActionRow(QWidget):
    """弹窗内一行动作编辑: 拖拽手柄 + ComboBox + SpinBox + 删除按钮。"""

    changed = Signal()
    remove_requested = Signal(object)

    def __init__(self, token: str, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        # 拖拽手柄图标（两条横线）
        grip = QLabel(" ≡")
        grip.setStyleSheet("color: rgba(255,255,255,0.25); font-size: 14px; padding: 0 2px;")
        grip.setCursor(Qt.OpenHandCursor)
        row.addWidget(grip)

        self.combo = ComboBox()
        for key, disp in _ACTION_OPTIONS:
            self.combo.addItem(_tr(disp), userData=key)
        self.combo.currentIndexChanged.connect(self._on_combo_changed)
        row.addWidget(self.combo, 1)

        self.spin = QDoubleSpinBox()
        self.spin.setMinimum(0.1)
        self.spin.setMaximum(999.0)
        self.spin.setDecimals(1)
        self.spin.setSingleStep(0.5)
        self.spin.setValue(1.0)
        self.spin.setVisible(False)
        self.spin.setFixedWidth(80)
        self.spin.valueChanged.connect(self._on_spin_changed)
        row.addWidget(self.spin)

        del_btn = TransparentToolButton(FluentIcon.DELETE)
        del_btn.setFixedSize(30, 30)
        del_btn.setToolTip(_tr("删除"))
        del_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        row.addWidget(del_btn)

        self._apply_token(token)

    def _on_combo_changed(self, *_):
        # 恢复旧选项为模板文字
        prev_idx = getattr(self, "_prev_combo_idx", -1)
        if prev_idx >= 0 and prev_idx != self.combo.currentIndex():
            prev_key = self.combo.itemData(prev_idx)
            if prev_key:
                self.combo.setItemText(prev_idx, _tr(_ACTION_TEMPLATES.get(prev_key, prev_key)))
        self._prev_combo_idx = self.combo.currentIndex()
        self._update_spin_visibility()
        self._update_combo_display()
        self.changed.emit()

    def _on_spin_changed(self, *_):
        self._update_combo_display()
        self.changed.emit()

    def _update_combo_display(self):
        key = self.combo.currentData()
        idx = self.combo.currentIndex()
        if idx >= 0:
            self.combo.setItemText(idx, _combo_display(key, self.spin))

    def _update_spin_visibility(self):
        key = self.combo.currentData()
        show = key in (_SKILL_ACTION_KEY, _ULT_ACTION_KEY, _SLEEP_KEY, _NORMAL_KEY)
        self.spin.setVisible(show)
        if show:
            if key in (_SKILL_ACTION_KEY, _ULT_ACTION_KEY):
                self.spin.setDecimals(0)
                self.spin.setMinimum(1.0)
                self.spin.setMaximum(4.0)
                self.spin.setSingleStep(1.0)
                if self.spin.value() < 1.0:
                    self.spin.setValue(1.0)
            else:
                self.spin.setDecimals(1)
                self.spin.setMinimum(0.1)
                self.spin.setMaximum(999.0)
                self.spin.setSingleStep(0.5)

    def _apply_token(self, token: str):
        if not isinstance(token, str):
            token = "1"
        if token.startswith("sleep_"):
            self.combo.setCurrentIndex(self.combo.findData(_SLEEP_KEY))
            self._update_spin_visibility()
            try:
                self.spin.setValue(max(0.1, float(token[6:])))
            except (ValueError, TypeError):
                pass
        elif token.startswith("normal_"):
            self.combo.setCurrentIndex(self.combo.findData(_NORMAL_KEY))
            self._update_spin_visibility()
            try:
                self.spin.setValue(max(0.1, float(token[7:])))
            except (ValueError, TypeError):
                pass
        elif token.startswith("ult_"):
            self.combo.setCurrentIndex(self.combo.findData(_ULT_ACTION_KEY))
            self._update_spin_visibility()
            try:
                self.spin.setValue(max(1, int(token[4:])))
            except (ValueError, TypeError):
                pass
        elif token.isdigit():
            self.combo.setCurrentIndex(self.combo.findData(_SKILL_ACTION_KEY))
            self._update_spin_visibility()
            try:
                self.spin.setValue(max(1, int(token)))
            except (ValueError, TypeError):
                pass
        else:
            idx = self.combo.findData(token)
            self.combo.setCurrentIndex(idx if idx >= 0 else 0)
            self.spin.setVisible(False)
        self._update_combo_display()

    def to_token(self) -> str:
        key = self.combo.currentData()
        if key == _SLEEP_KEY:
            return f"sleep_{self.spin.value():.3g}"
        if key == _NORMAL_KEY:
            return f"normal_{self.spin.value():.3g}"
        if key == _ULT_ACTION_KEY:
            return f"ult_{int(self.spin.value())}"
        if key == _SKILL_ACTION_KEY:
            return str(int(self.spin.value()))
        return key if isinstance(key, str) else "1"


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
        self._list.setMovement(QListView.Free)
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


# 弹窗内：条件编辑器
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
        for key, disp in _ATOM_OPTIONS:
            combo.addItem(_tr(disp), userData=key)

        spin = SpinBox()
        spin.setRange(1, 4)
        spin.setSingleStep(1)
        spin.setValue(1)
        spin.setVisible(False)

        def _update_combo_display():
            idx = combo.currentIndex()
            if idx >= 0:
                combo.setItemText(idx, _combo_display(combo.currentData(), spin))

        _prev_idx = -1

        def on_combo_changed(*_):
            nonlocal _prev_idx
            # 恢复旧选项为模板文字
            if _prev_idx >= 0 and _prev_idx != combo.currentIndex():
                prev_key = combo.itemData(_prev_idx)
                if prev_key:
                    combo.setItemText(_prev_idx, _tr(_ATOM_TEMPLATES.get(prev_key, prev_key)))
            _prev_idx = combo.currentIndex()

            key = combo.currentData()
            show = key in (_ULT_KEY, _SKILL_KEY)
            spin.setVisible(show)
            if show:
                if key == _ULT_KEY:
                    spin.setRange(1, 4)
                else:
                    spin.setRange(1, 3)
            _update_combo_display()
            self.changed.emit()

        combo.currentIndexChanged.connect(on_combo_changed)
        spin.valueChanged.connect(lambda *_: (_update_combo_display(), self.changed.emit()))
        self._apply_atom_value(combo, spin, value)
        _update_combo_display()

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
        if value.startswith("ult") and value[3:].isdigit():
            try:
                n = int(value[3:])
                if 1 <= n <= 4:
                    combo.setCurrentIndex(combo.findData(_ULT_KEY))
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
        if data == _ULT_KEY:
            return f"ult{spin.value()}"
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


# 编辑弹窗
class _ConditionEditDialog(MessageBoxBase):
    """条件块编辑弹窗（项目 MessageBoxBase 范式）。"""

    def __init__(self, node: dict, parent=None):
        super().__init__(parent)
        self.widget.hide()
        self.setUpdatesEnabled(False)
        self.setWindowTitle(_tr("编辑条件块"))
        node = node if isinstance(node, dict) else {}
        cond = node.get("if", "link")
        then_nodes = node.get("then", []) if isinstance(node.get("then"), list) else []

        self.titleLabel = SubtitleLabel(_tr("编辑条件块"), self)
        self.viewLayout.addWidget(self.titleLabel)

        self.cond_editor = _ConditionEditor(cond)
        self.viewLayout.addWidget(self.cond_editor)

        self.then_editor = _ActionListEditor(_tr("运行："))
        self.then_editor.load(then_nodes)
        self.viewLayout.addWidget(self.then_editor)

        self.yesButton.setText(_tr("确定"))
        self.cancelButton.setText(_tr("取消"))
        self.widget.setFixedWidth(460)
        self.widget.adjustSize()
        self.setUpdatesEnabled(True)
        self.widget.show()

    def showEvent(self, event):
        opacityEffect = QGraphicsOpacityEffect(self.widget)
        self.widget.setGraphicsEffect(opacityEffect)
        opacityEffect.setOpacity(0.0)
        QDialog.showEvent(self, event)
        self.widget.adjustSize()
        ani = QPropertyAnimation(opacityEffect, b'opacity', self)
        ani.setStartValue(0.0)
        ani.setEndValue(1.0)
        ani.setDuration(150)
        ani.setEasingCurve(QEasingCurve.InSine)
        ani.finished.connect(lambda: self._onShowAniFinished())
        ani.start()

    def _onShowAniFinished(self):
        self.widget.setGraphicsEffect(None)
        self.setShadowEffect()

    def done(self, code):
        self.widget.setGraphicsEffect(None)
        opacityEffect = QGraphicsOpacityEffect(self.widget)
        self.widget.setGraphicsEffect(opacityEffect)
        ani = QPropertyAnimation(opacityEffect, b'opacity', self)
        ani.setStartValue(1.0)
        ani.setEndValue(0.0)
        ani.setDuration(100)
        ani.setEasingCurve(QEasingCurve.InSine)
        ani.finished.connect(lambda c=code: self._onCloseAniFinished(c))
        ani.start()

    def _onCloseAniFinished(self, code):
        self.widget.setGraphicsEffect(None)
        QDialog.done(self, code)

    def to_node(self) -> dict:
        return {"if": self.cond_editor.to_cond(), "then": self.then_editor.to_list()}


# 只读显示卡片
class _ConditionDisplayCard(QFrame):
    """只读显示卡片：两行（条件行 / 动作行），行间浅色线，右键菜单。"""

    selected = Signal(object)      # emit self
    right_clicked = Signal(object, QPoint)  # emit self, global_pos
    double_clicked = Signal(object)  # emit self

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

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.double_clicked.emit(self)
        super().mouseDoubleClickEvent(event)

    def set_selected(self, selected: bool):
        self.setProperty("selected", selected)
        self.style().polish(self)


# ── 条件列表编辑弹窗 ──────
class _ConditionListEditDialog(MessageBoxBase):
    """条件列表编辑弹窗: 卡片列表 + 工具栏, 固定高度滚动."""

    def __init__(self, ast: list, parent=None):
        super().__init__(parent)
        self.setUpdatesEnabled(False)
        self.setWindowTitle(_tr("编辑条件列表"))
        self._ast = [dict(n) if isinstance(n, dict) else n for n in (ast if isinstance(ast, list) else [])]
        self._selected_idx = -1
        self._cards: list[_ConditionDisplayCard] = []

        self.titleLabel = SubtitleLabel(_tr("实时条件"), self)
        self.viewLayout.addWidget(self.titleLabel)

        # 卡片列表（QListWidget viewport 跟随父级渲染管线，不会割裂）
        self._card_list = QListWidget()
        self._card_list.setSelectionMode(QAbstractItemView.NoSelection)
        self._card_list.setFrameShape(QFrame.NoFrame)
        self._card_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._card_list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._card_list.setSpacing(6)
        self._card_list.setMinimumHeight(200)
        self._card_list.setStyleSheet("""
            QListWidget {
                background-color: #2b2b2b;
                border: none;
                outline: none;
            }
            QListWidget::item {
                background: transparent;
                padding: 0px;
                margin: 0px;
            }
        """)
        self.viewLayout.addWidget(self._card_list)

        # 按钮行: 增删清 + 排序
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.add_btn = PushButton(FluentIcon.ADD, _tr("添加"))
        self.add_btn.clicked.connect(self._on_add)
        btn_row.addWidget(self.add_btn)
        self.edit_btn = PushButton(FluentIcon.EDIT, _tr("编辑"))
        self.edit_btn.clicked.connect(self._on_edit)
        btn_row.addWidget(self.edit_btn)
        self.del_btn = PushButton(FluentIcon.DELETE, _tr("删除"))
        self.del_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(self.del_btn)
        self.clear_btn = PushButton(FluentIcon.BROOM, _tr("清空"))
        self.clear_btn.clicked.connect(self._on_clear)
        btn_row.addWidget(self.clear_btn)
        self.up_btn = PushButton(FluentIcon.UP, _tr("上移"))
        self.up_btn.clicked.connect(lambda: self._move(-1))
        btn_row.addWidget(self.up_btn)
        self.down_btn = PushButton(FluentIcon.DOWN, _tr("下移"))
        self.down_btn.clicked.connect(lambda: self._move(1))
        btn_row.addWidget(self.down_btn)
        btn_row.addStretch(1)
        self.viewLayout.addLayout(btn_row)

        self._render_cards()

        self.yesButton.setText(_tr("确定"))
        self.cancelButton.setText(_tr("取消"))
        self.widget.setMinimumWidth(520)
        self.widget.setFixedHeight(520)
        self.setUpdatesEnabled(True)

    def showEvent(self, event):
        opacityEffect = QGraphicsOpacityEffect(self.widget)
        self.widget.setGraphicsEffect(opacityEffect)
        opacityEffect.setOpacity(0.0)
        QDialog.showEvent(self, event)
        self.widget.adjustSize()
        ani = QPropertyAnimation(opacityEffect, b'opacity', self)
        ani.setStartValue(0.0)
        ani.setEndValue(1.0)
        ani.setDuration(150)
        ani.setEasingCurve(QEasingCurve.InSine)
        ani.finished.connect(lambda: self._onShowAniFinished())
        ani.start()

    def _onShowAniFinished(self):
        self.widget.setGraphicsEffect(None)
        self.setShadowEffect()  # 恢复构建时被替换的阴影

    def done(self, code):
        self.widget.setGraphicsEffect(None)  # 清除阴影
        opacityEffect = QGraphicsOpacityEffect(self.widget)
        self.widget.setGraphicsEffect(opacityEffect)
        ani = QPropertyAnimation(opacityEffect, b'opacity', self)
        ani.setStartValue(1.0)
        ani.setEndValue(0.0)
        ani.setDuration(100)
        ani.setEasingCurve(QEasingCurve.InSine)
        ani.finished.connect(lambda c=code: self._onCloseAniFinished(c))
        ani.start()

    def _onCloseAniFinished(self, code):
        self.widget.setGraphicsEffect(None)
        QDialog.done(self, code)

    @staticmethod
    def _est_card_height(node: dict) -> int:
        """根据文本内容估算卡高，供 QListWidgetItem.setSizeHint 使用。"""
        node = node if isinstance(node, dict) else {}
        type_label, cond_desc = _fmt_cond(node.get("if", "link"))
        cond_text = f"{type_label}  {cond_desc}  时"
        actions = node.get("then", [])
        action_text = "运行 " + _fmt_actions(actions)

        chars_per_line = 40
        cond_lines = max(1, -(-len(cond_text) // chars_per_line))
        action_lines = max(1, -(-len(action_text) // chars_per_line))

        line_h = 20  # 14px 字体的近似行高
        h = 10 + cond_lines * line_h + 8 + 1 + action_lines * line_h + 10
        return max(74, h)

    def _render_cards(self):
        prev_idx = self._selected_idx
        self._card_list.clear()
        self._cards = []
        self._selected_idx = -1

        for i, node in enumerate(self._ast):
            card = _ConditionDisplayCard(node)
            card.selected.connect(lambda c, idx=i: self._on_card_selected(c, idx))
            card.double_clicked.connect(lambda c: self._on_edit())
            card.right_clicked.connect(self._on_card_right_clicked)
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, self._est_card_height(node)))
            self._card_list.addItem(item)
            self._card_list.setItemWidget(item, card)
            self._cards.append(card)

        # 恢复选中
        if 0 <= prev_idx < len(self._cards):
            self._selected_idx = prev_idx
            self._cards[prev_idx].set_selected(True)

    def _on_card_selected(self, card: _ConditionDisplayCard, idx: int):
        for c in self._cards:
            c.set_selected(c is card)
        self._selected_idx = idx

    def _on_card_right_clicked(self, card: _ConditionDisplayCard, global_pos: QPoint):
        for i, c in enumerate(self._cards):
            if c is card:
                self._on_card_selected(card, i)
                break
        menu = RoundMenu()
        menu.addAction(Action(FluentIcon.ADD, _tr("添加"), triggered=self._on_add))
        menu.addAction(Action(FluentIcon.EDIT, _tr("编辑"), triggered=self._on_edit))
        menu.addAction(Action(FluentIcon.DELETE, _tr("删除"), triggered=self._on_delete))
        menu.exec(global_pos)

    def _on_add(self):
        new_node = {"if": "link", "then": []}
        dlg = _ConditionEditDialog(new_node, self.window())
        if dlg.exec():
            self._ast.append(dlg.to_node())
            self._render_cards()

    def _on_edit(self):
        if self._selected_idx < 0 or self._selected_idx >= len(self._ast):
            return
        dlg = _ConditionEditDialog(self._ast[self._selected_idx], self.window())
        if dlg.exec():
            self._ast[self._selected_idx] = dlg.to_node()
            if self._selected_idx < len(self._cards):
                self._cards[self._selected_idx].update_node(self._ast[self._selected_idx])

    def _on_delete(self):
        if self._selected_idx < 0:
            return
        box = MessageBox(_tr("确认删除"), _tr("确定删除选中的条件块？"), self.window())
        if box.exec():
            self._ast.pop(self._selected_idx)
            self._render_cards()

    def _on_clear(self):
        if not self._ast:
            return
        box = MessageBox(_tr("确认清空"), _tr("确定清空所有条件块？"), self.window())
        if box.exec():
            self._ast.clear()
            self._render_cards()

    def _move(self, direction: int):
        idx = self._selected_idx
        if idx < 0 or idx >= len(self._ast):
            return
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(self._ast):
            return
        self._ast[idx], self._ast[new_idx] = self._ast[new_idx], self._ast[idx]
        self._selected_idx = new_idx
        self._render_cards()

    def to_ast(self) -> list:
        return list(self._ast)


# 主面板: 4 行配置项 (无折叠, 与其他配置行样式一致)
class ConditionalRotationPanel(QWidget):
    """实时条件面板: 四行配置项.

    启用实时条件 / 立即释放终结技 / 立即释放连携技 / 动作列表(编辑按钮).
    """

    def __init__(self, parent=None, config=None):
        super().__init__(parent)
        self.config = config or get_global_config(BATTLE_CONFIG_NAME)
        self._loading = False
        self._setup_ui()
        self._load()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Row 1: 启用实时条件
        self._row1 = LabelAndWidget(KEY_COND_ENABLED, BATTLE_CONFIG_DESCRIPTION[KEY_COND_ENABLED])
        self.enable_switch = SwitchButton(indicatorPos=IndicatorPosition.RIGHT)
        self.enable_switch.setOnText(_tr("是"))
        self.enable_switch.setOffText(_tr("否"))
        self.enable_switch.checkedChanged.connect(lambda c: self._set_config(KEY_COND_ENABLED, c))
        self._row1.add_widget(self.enable_switch, stretch=0)
        layout.addWidget(self._row1)

        # Row 2: 立即释放终结技
        self._row2 = LabelAndWidget(KEY_INSTANT_ULT, BATTLE_CONFIG_DESCRIPTION[KEY_INSTANT_ULT])
        self.ult_switch = SwitchButton(indicatorPos=IndicatorPosition.RIGHT)
        self.ult_switch.setOnText(_tr("是"))
        self.ult_switch.setOffText(_tr("否"))
        self.ult_switch.checkedChanged.connect(lambda c: self._set_config(KEY_INSTANT_ULT, c))
        self._row2.add_widget(self.ult_switch, stretch=0)
        layout.addWidget(self._row2)

        # Row 3: 立即释放连携技
        self._row3 = LabelAndWidget(KEY_INSTANT_LINK, BATTLE_CONFIG_DESCRIPTION[KEY_INSTANT_LINK])
        self.link_switch = SwitchButton(indicatorPos=IndicatorPosition.RIGHT)
        self.link_switch.setOnText(_tr("是"))
        self.link_switch.setOffText(_tr("否"))
        self.link_switch.checkedChanged.connect(lambda c: self._set_config(KEY_INSTANT_LINK, c))
        self._row3.add_widget(self.link_switch, stretch=0)
        layout.addWidget(self._row3)

        # Row 4: 动作列表 + 编辑按钮
        self._row4 = LabelAndWidget(_tr("动作列表"), _tr("当条件符合时使用技能组"))
        self._row4.contentLabel.setWordWrap(False)
        self.edit_btn = PushButton(FluentIcon.EDIT, _tr("编辑"))
        self.edit_btn.clicked.connect(self._on_edit)
        self._row4.add_widget(self.edit_btn, stretch=0)
        layout.addWidget(self._row4)

    def _load(self):
        self._loading = True
        self.enable_switch.setChecked(bool(self.config.get(KEY_COND_ENABLED, False)))
        self.ult_switch.setChecked(bool(self.config.get(KEY_INSTANT_ULT, False)))
        self.link_switch.setChecked(bool(self.config.get(KEY_INSTANT_LINK, False)))
        self._loading = False

    def update_value(self):
        """供 ConfigCard.update_config 调用."""
        self._load()

    def _set_config(self, key: str, value):
        if self._loading:
            return
        self.config[key] = value

    def _on_edit(self):
        raw_ast = self.config.get(KEY_COND_SEQUENCE, [])
        clean_ast, warnings = normalize_ast(raw_ast)
        for w in warnings:
            try:
                og.app.logger.info(f"实时条件: {w}")
            except Exception:
                pass
        dlg = _ConditionListEditDialog(clean_ast, self.window())
        if dlg.exec():
            self.config[KEY_COND_SEQUENCE] = dlg.to_ast()
