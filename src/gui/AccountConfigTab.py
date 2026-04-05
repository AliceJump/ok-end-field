from __future__ import annotations

from typing import Any, Dict

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    FluentIcon,
    NavigationItemPosition,
    PrimaryPushButton,
    PushButton,
    SwitchButton,
    TextEdit,
)

from ok.gui.tasks.ConfigCard import ConfigCard
from ok.gui.tasks.LabelAndWidget import LabelAndWidget
from ok.gui.widget.CustomTab import CustomTab
from src.tasks.DailyTask import DailyTask
from src.tasks.account.account_scope_store import load_overrides, save_overrides


class InMemoryConfig(dict):
    """A lightweight config object used by ConfigCard for account overrides."""

    def __init__(self, initial: Dict[str, Any], defaults: Dict[str, Any]):
        super().__init__(initial)
        self.default = defaults

    def get_default(self, key):
        return self.default.get(key)

    def has_user_config(self):
        return any(not str(key).startswith("_") for key in self.keys())


class AccountConfigTab(CustomTab):
    def __init__(self):
        super().__init__()
        self.daily_task = None
        self._loaded_once = False
        self._building = False

        self.overrides_data: Dict[str, Any] = {"accounts": {}}
        self.task_map: Dict[str, Any] = {}
        self.current_virtual_config: InMemoryConfig | None = None
        self.current_task = None
        self.current_account = ""
        self.current_editable_keys: list[str] = []
        self.current_base_values: Dict[str, Any] = {}

        self._build_ui()

    @property
    def name(self):
        return "账号配置"

    @property
    def position(self):
        return NavigationItemPosition.TOP

    @property
    def add_after_default_tabs(self):
        return False

    @property
    def icon(self):
        return FluentIcon.SETTING

    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded_once and self.executor is not None:
            self._loaded_once = True
            self.refresh_from_source()

    def _build_ui(self):
        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)
        tip = BodyLabel(
            "按账号和任务配置独立参数。先选账号，再选任务，下面会自动出现该任务的属性控件。"
        )
        tip.setWordWrap(True)
        header_layout.addWidget(tip)
        self.add_card("账号配置中心", header)

        base_widget = QWidget()
        base_layout = QVBoxLayout(base_widget)
        base_layout.setContentsMargins(0, 0, 0, 0)
        base_layout.setSpacing(8)

        account_mode_row = LabelAndWidget("多账户模式", "总开关")
        self.multi_account_switch = SwitchButton()
        self.multi_account_switch.setOnText(self.tr("启用"))
        self.multi_account_switch.setOffText(self.tr("禁用"))
        account_mode_row.add_widget(self.multi_account_switch, stretch=0)
        base_layout.addWidget(account_mode_row)

        scoped_mode_row = LabelAndWidget("多账户独立配置", "开启后按账号读取覆盖参数")
        self.scoped_config_switch = SwitchButton()
        self.scoped_config_switch.setOnText(self.tr("启用"))
        self.scoped_config_switch.setOffText(self.tr("禁用"))
        scoped_mode_row.add_widget(self.scoped_config_switch, stretch=0)
        base_layout.addWidget(scoped_mode_row)

        account_list_row = LabelAndWidget("账号列表", "每行格式：账号,密码")
        self.account_list_edit = TextEdit()
        self.account_list_edit.setMinimumHeight(120)
        self.account_list_edit.setPlaceholderText("账号A,密码A\n账号B,密码B")
        account_list_row.add_widget(self.account_list_edit, stretch=1)
        base_layout.addWidget(account_list_row)

        base_action_row = LabelAndWidget("基础配置操作")
        base_action_layout = QHBoxLayout()
        self.save_base_button = PrimaryPushButton("保存基础配置")
        self.refresh_button = PushButton("刷新")
        base_action_layout.addWidget(self.save_base_button)
        base_action_layout.addWidget(self.refresh_button)
        base_action_layout.addStretch(1)
        base_action_row.add_layout(base_action_layout, stretch=1)
        base_layout.addWidget(base_action_row)

        self.add_card("多账户基础设置", base_widget)

        selector_widget = QWidget()
        selector_layout = QVBoxLayout(selector_widget)
        selector_layout.setContentsMargins(0, 0, 0, 0)
        selector_layout.setSpacing(8)

        account_selector_row = LabelAndWidget("账号", "从账号列表或已有覆盖中选择")
        account_selector_layout = QHBoxLayout()
        self.account_selector = ComboBox()
        self.account_selector.setMinimumWidth(220)
        self.refresh_account_selector_button = PushButton("刷新账号下拉")
        self.clear_account_override_button = PushButton("清空当前账号全部覆盖")
        account_selector_layout.addWidget(self.account_selector)
        account_selector_layout.addWidget(self.refresh_account_selector_button)
        account_selector_layout.addWidget(self.clear_account_override_button)
        account_selector_layout.addStretch(1)
        account_selector_row.add_layout(account_selector_layout, stretch=1)
        selector_layout.addWidget(account_selector_row)

        task_selector_row = LabelAndWidget("任务", "选择任务后自动渲染属性控件")
        task_selector_layout = QHBoxLayout()
        self.task_selector = ComboBox()
        self.task_selector.setMinimumWidth(280)
        self.refresh_task_selector_button = PushButton("刷新任务下拉")
        task_selector_layout.addWidget(self.task_selector)
        task_selector_layout.addWidget(self.refresh_task_selector_button)
        task_selector_layout.addStretch(1)
        task_selector_row.add_layout(task_selector_layout, stretch=1)
        selector_layout.addWidget(task_selector_row)

        action_row = LabelAndWidget("账号任务覆盖操作")
        action_layout = QHBoxLayout()
        self.save_task_override_button = PrimaryPushButton("保存当前账号任务覆盖")
        self.clear_task_override_button = PushButton("清空当前任务覆盖")
        action_layout.addWidget(self.save_task_override_button)
        action_layout.addWidget(self.clear_task_override_button)
        action_layout.addStretch(1)
        action_row.add_layout(action_layout, stretch=1)
        selector_layout.addWidget(action_row)

        self.add_card("账号任务选择", selector_widget)

        editor_widget = QWidget()
        self.editor_layout = QVBoxLayout(editor_widget)
        self.editor_layout.setContentsMargins(0, 0, 0, 0)
        self.editor_layout.setSpacing(8)
        self.editor_layout.addWidget(BodyLabel("请先选择账号与任务"))
        self.add_card("任务属性配置", editor_widget)

        status_widget = QWidget()
        status_layout = QVBoxLayout(status_widget)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(4)
        self.status_label = BodyLabel("就绪")
        self.status_label.setWordWrap(True)
        status_layout.addWidget(self.status_label)
        self.add_card("状态", status_widget)

        self.save_base_button.clicked.connect(self.save_base_settings)
        self.refresh_button.clicked.connect(self.refresh_from_source)
        self.refresh_account_selector_button.clicked.connect(self.rebuild_account_selector)
        self.refresh_task_selector_button.clicked.connect(self.rebuild_task_selector)
        self.account_selector.currentTextChanged.connect(self.on_account_changed)
        self.task_selector.currentTextChanged.connect(self.on_task_changed)
        self.save_task_override_button.clicked.connect(self.save_current_task_override)
        self.clear_task_override_button.clicked.connect(self.clear_current_task_override)
        self.clear_account_override_button.clicked.connect(self.clear_current_account_overrides)

    def _set_status(self, text: str):
        self.status_label.setText(text)

    def _ensure_daily_task(self):
        if self.executor is None:
            self._set_status("界面初始化中，请稍候")
            return False

        if self.daily_task is None:
            try:
                self.daily_task = self.get_task(DailyTask)
            except Exception:
                self.daily_task = None

        if self.daily_task is None:
            self._set_status("未找到 DailyTask，无法读取多账户基础配置")
            return False

        return True

    @staticmethod
    def _parse_accounts(account_list_text: str) -> list[str]:
        accounts = []
        seen = set()
        for raw in account_list_text.splitlines():
            line = raw.strip()
            if not line:
                continue
            username = line.split(",", 1)[0].strip()
            if username and username not in seen:
                seen.add(username)
                accounts.append(username)
        return accounts

    @staticmethod
    def _is_supported_value(value: Any) -> bool:
        return isinstance(value, (bool, int, float, str, list))

    @staticmethod
    def _coerce_like(base_value: Any, value: Any) -> Any:
        if base_value is None or value is None:
            return value

        if isinstance(base_value, bool):
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                text = value.strip().lower()
                if text in {"true", "1", "yes", "on", "是", "开启"}:
                    return True
                if text in {"false", "0", "no", "off", "否", "关闭"}:
                    return False
            return base_value

        if isinstance(base_value, int) and not isinstance(base_value, bool):
            if isinstance(value, int):
                return value
            if isinstance(value, str):
                try:
                    return int(value.strip())
                except ValueError:
                    return base_value
            return base_value

        if isinstance(base_value, float):
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str):
                try:
                    return float(value.strip())
                except ValueError:
                    return base_value
            return base_value

        if isinstance(base_value, list):
            return value if isinstance(value, list) else base_value

        if isinstance(base_value, str):
            return str(value)

        return value if isinstance(value, type(base_value)) else base_value

    def _collect_tasks(self):
        if self.executor is None:
            return []

        tasks = []
        seen = set()
        for task in list(getattr(self.executor, "onetime_tasks", [])) + list(getattr(self.executor, "trigger_tasks", [])):
            class_name = task.__class__.__name__
            if class_name in seen:
                continue
            seen.add(class_name)
            tasks.append(task)
        return tasks

    def _clear_layout(self, layout: QVBoxLayout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def refresh_from_source(self):
        if not self._ensure_daily_task():
            return

        self._building = True
        try:
            cfg = self.daily_task.config
            self.multi_account_switch.setChecked(bool(cfg.get("多账户模式", False)))
            self.scoped_config_switch.setChecked(bool(cfg.get("多账户独立配置", False)))
            self.account_list_edit.setPlainText(str(cfg.get("账号列表", "") or ""))
            self.overrides_data = load_overrides(force=True)

            self.rebuild_account_selector(keep_selection=False)
            self.rebuild_task_selector(keep_selection=False)
            self.render_task_editor()
            self._set_status("已刷新账号与任务配置")
        finally:
            self._building = False

    def save_base_settings(self):
        if not self._ensure_daily_task():
            return

        cfg = self.daily_task.config
        cfg["多账户模式"] = bool(self.multi_account_switch.isChecked())
        cfg["多账户独立配置"] = bool(self.scoped_config_switch.isChecked())
        cfg["账号列表"] = self.account_list_edit.toPlainText().strip()

        self.rebuild_account_selector()
        self._set_status("基础配置已保存")

    def _current_account(self) -> str:
        return self.account_selector.currentText().strip()

    def _current_task(self):
        display = self.task_selector.currentText().strip()
        return self.task_map.get(display)

    def rebuild_account_selector(self, keep_selection: bool = True):
        current = self._current_account() if keep_selection else ""

        accounts = self._parse_accounts(self.account_list_edit.toPlainText())
        stored_accounts = list((self.overrides_data.get("accounts") or {}).keys())
        for account in stored_accounts:
            if account not in accounts:
                accounts.append(account)

        self.account_selector.blockSignals(True)
        self.account_selector.clear()
        for account in accounts:
            self.account_selector.addItem(account)
        self.account_selector.blockSignals(False)

        if current and current in accounts:
            self.account_selector.setCurrentText(current)
        elif accounts:
            self.account_selector.setCurrentIndex(0)

    def rebuild_task_selector(self, keep_selection: bool = True):
        current_task = self._current_task()
        current_class_name = current_task.__class__.__name__ if keep_selection and current_task else ""

        self.task_map = {}
        displays = []
        for task in self._collect_tasks():
            display = f"{task.name} ({task.__class__.__name__})"
            self.task_map[display] = task
            displays.append(display)

        self.task_selector.blockSignals(True)
        self.task_selector.clear()
        for display in displays:
            self.task_selector.addItem(display)
        self.task_selector.blockSignals(False)

        if current_class_name:
            for display, task in self.task_map.items():
                if task.__class__.__name__ == current_class_name:
                    self.task_selector.setCurrentText(display)
                    return

        if displays:
            self.task_selector.setCurrentIndex(0)

    def on_account_changed(self, _):
        if self._building:
            return
        self.render_task_editor()

    def on_task_changed(self, _):
        if self._building:
            return
        self.render_task_editor()

    def _build_virtual_config(self, task, account_name: str):
        task_class = task.__class__.__name__
        account_map = (self.overrides_data.get("accounts") or {}).get(account_name, {})
        task_override = account_map.get(task_class, {}) if isinstance(account_map, dict) else {}

        defaults = {}
        initial = {}
        base_values = {}
        editable_keys = []

        for key, default_value in task.default_config.items():
            if str(key).startswith("_"):
                continue
            if key in {"多账户模式", "多账户独立配置", "账号列表"}:
                continue

            type_meta = task.config_type.get(key) if task.config_type else None
            if type_meta and type_meta.get("type") == "global":
                continue

            if not self._is_supported_value(default_value):
                continue

            base_value = dict.get(task.config, key, default_value)
            override_value = task_override.get(key, base_value)
            value = self._coerce_like(base_value, override_value)

            defaults[key] = default_value
            initial[key] = value
            base_values[key] = base_value
            editable_keys.append(key)

        return InMemoryConfig(initial, defaults), editable_keys, base_values

    def render_task_editor(self):
        self._clear_layout(self.editor_layout)
        self.current_virtual_config = None
        self.current_task = None
        self.current_account = ""
        self.current_editable_keys = []
        self.current_base_values = {}

        account_name = self._current_account()
        if not account_name:
            self.editor_layout.addWidget(BodyLabel("请先选择账号"))
            return

        task = self._current_task()
        if task is None:
            self.editor_layout.addWidget(BodyLabel("请先选择任务"))
            return

        virtual_config, editable_keys, base_values = self._build_virtual_config(task, account_name)
        if not editable_keys:
            self.editor_layout.addWidget(BodyLabel("该任务暂无可编辑配置项"))
            return

        card = ConfigCard(
            None,
            f"{task.name} - {account_name}",
            virtual_config,
            "按当前账号覆盖该任务配置。未覆盖的项将使用任务原配置。",
            {},
            task.config_description,
            task.config_type,
            task.icon,
        )
        self.editor_layout.addWidget(card)

        self.current_virtual_config = virtual_config
        self.current_task = task
        self.current_account = account_name
        self.current_editable_keys = editable_keys
        self.current_base_values = base_values

    def save_current_task_override(self):
        if not self.current_virtual_config or self.current_task is None or not self.current_account:
            self._set_status("请先选择账号与任务")
            return

        diff = {}
        for key in self.current_editable_keys:
            current_value = self.current_virtual_config.get(key)
            base_value = self.current_base_values.get(key)
            if current_value != base_value:
                diff[key] = current_value

        accounts = self.overrides_data.setdefault("accounts", {})
        account_map = accounts.setdefault(self.current_account, {})

        task_class = self.current_task.__class__.__name__
        if diff:
            account_map[task_class] = diff
            self._set_status(f"已保存：{self.current_account} / {self.current_task.name}（覆盖 {len(diff)} 项）")
        else:
            account_map.pop(task_class, None)
            self._set_status(f"无差异，已清除：{self.current_account} / {self.current_task.name} 覆盖")

        if not account_map:
            accounts.pop(self.current_account, None)

        self.overrides_data = save_overrides(self.overrides_data)
        self.rebuild_account_selector()

    def clear_current_task_override(self):
        account = self._current_account()
        task = self._current_task()
        if not account or task is None:
            self._set_status("请先选择账号与任务")
            return

        accounts = self.overrides_data.get("accounts", {})
        account_map = accounts.get(account, {})
        task_class = task.__class__.__name__
        account_map.pop(task_class, None)
        if not account_map:
            accounts.pop(account, None)

        self.overrides_data = save_overrides(self.overrides_data)
        self.render_task_editor()
        self.rebuild_account_selector()
        self._set_status(f"已清空：{account} / {task.name} 覆盖")

    def clear_current_account_overrides(self):
        account = self._current_account()
        if not account:
            self._set_status("请先选择账号")
            return

        accounts = self.overrides_data.get("accounts", {})
        if account in accounts:
            accounts.pop(account, None)
            self.overrides_data = save_overrides(self.overrides_data)

        self.rebuild_account_selector()
        self.render_task_editor()
        self._set_status(f"已清空账号全部覆盖：{account}")
