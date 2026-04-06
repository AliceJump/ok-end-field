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
from src.tasks.account.account_scope_store import (
    load_overrides,
    parse_account_list_text,
    save_overrides,
    sync_account_list_text,
)


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
        self._loaded_once = False
        self._building = False

        self.overrides_data: Dict[str, Any] = {"accounts": {}}
        self.task_map: Dict[str, Any] = {}
        self.current_virtual_config: InMemoryConfig | None = None
        self.current_task = None
        self.current_account_key = ""
        self.current_account_name = ""
        self.current_editable_keys: list[str] = []
        self.current_base_values: Dict[str, Any] = {}
        self.account_display_to_key: Dict[str, str] = {}
        self.account_display_to_name: Dict[str, str] = {}

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
            "账号页只需要填写账号名（手机号），密码请在任务配置里的账号列表中填写。"
        )
        tip.setWordWrap(True)
        header_layout.addWidget(tip)
        self.add_card("账号配置中心", header)

        base_widget = QWidget()
        base_layout = QVBoxLayout(base_widget)
        base_layout.setContentsMargins(0, 0, 0, 0)
        base_layout.setSpacing(8)

        account_list_row = LabelAndWidget("账号列表", "每行一个账号名（手机号），无需密码")
        self.account_list_edit = TextEdit()
        self.account_list_edit.setMinimumHeight(120)
        self.account_list_edit.setPlaceholderText("手机号A\n手机号B")
        account_list_row.add_widget(self.account_list_edit, stretch=1)
        base_layout.addWidget(account_list_row)

        base_action_row = LabelAndWidget("账号列表操作")
        base_action_layout = QHBoxLayout()
        self.save_base_button = PrimaryPushButton("保存账号列表")
        self.refresh_button = PushButton("刷新")
        base_action_layout.addWidget(self.save_base_button)
        base_action_layout.addWidget(self.refresh_button)
        base_action_layout.addStretch(1)
        base_action_row.add_layout(base_action_layout, stretch=1)
        base_layout.addWidget(base_action_row)

        self.add_card("账号基础设置", base_widget)

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

        view_row = LabelAndWidget("视图", "开启后仅显示与原配置不同的项")
        self.only_diff_switch = SwitchButton()
        self.only_diff_switch.setOnText(self.tr("仅差异"))
        self.only_diff_switch.setOffText(self.tr("全部"))
        view_row.add_widget(self.only_diff_switch, stretch=0)
        selector_layout.addWidget(view_row)

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
        self.only_diff_switch.checkedChanged.connect(self.on_view_mode_changed)
        self.save_task_override_button.clicked.connect(self.save_current_task_override)
        self.clear_task_override_button.clicked.connect(self.clear_current_task_override)
        self.clear_account_override_button.clicked.connect(self.clear_current_account_overrides)

    def _set_status(self, text: str):
        self.status_label.setText(text)

    def _ensure_executor(self):
        if self.executor is None:
            self._set_status("界面初始化中，请稍候")
            return False
        return True

    @staticmethod
    def _parse_accounts(account_list_text: str) -> list[Dict[str, str]]:
        accounts: list[Dict[str, str]] = []
        seen = set()
        for entry in parse_account_list_text(account_list_text):
            username = str(entry.get("username", "")).strip()
            if username and username not in seen:
                seen.add(username)
                accounts.append({"username": username, "password": str(entry.get("password", ""))})
        return accounts

    def _resolve_account_key_by_username(self, username: str) -> str:
        username = username.strip()
        if not username:
            return ""

        registry = self.overrides_data.get("account_registry") or {}
        for account_id, meta in registry.items():
            if not isinstance(account_id, str) or not isinstance(meta, dict):
                continue

            current_name = str(meta.get("username", "") or "").strip()
            if username == current_name:
                return account_id

        return ""

    def _get_account_name_by_key(self, account_key: str) -> str:
        if not account_key:
            return ""

        registry = self.overrides_data.get("account_registry") or {}
        meta = registry.get(account_key)
        if isinstance(meta, dict):
            username = str(meta.get("username", "") or "").strip()
            if username:
                return username
        return account_key

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
            if not getattr(task, "support_multi_account", False):
                continue
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
        if not self._ensure_executor():
            return

        self._building = True
        try:
            self.overrides_data = load_overrides(force=True)
            self.account_list_edit.setPlainText(str(self.overrides_data.get("account_list_text", "") or ""))

            tasks = self._collect_tasks()

            self.rebuild_account_selector(keep_selection=False)
            self.rebuild_task_selector(keep_selection=False)
            self.render_task_editor()

            if not tasks:
                self._set_status("未找到 support_multi_account=True 的任务")
            else:
                self._set_status("已刷新账号与任务配置（账号页账号列表与任务账号列表独立）")
        finally:
            self._building = False

    def save_base_settings(self):
        if not self._ensure_executor():
            return

        account_list = self.account_list_edit.toPlainText().strip()

        summary = sync_account_list_text(account_list)
        self.overrides_data = load_overrides(force=True)

        self.rebuild_account_selector()
        status = (
            "账号列表已保存"
            f"（复用ID {summary.get('reused_count', 0)}，"
            f"新建ID {summary.get('created_count', 0)}）"
        )
        status += "；账号名（手机号）是唯一ID，密码变化不影响ID，账号名变化会新建ID"
        status += "；账号页无需密码，密码请在任务配置账号列表中填写"

        invalid_count = int(summary.get("invalid_count", 0) or 0)
        if invalid_count > 0:
            status += f"；忽略无效行 {invalid_count} 条"

        self._set_status(status)

    def _current_account_key(self) -> str:
        display = self.account_selector.currentText().strip()
        return self.account_display_to_key.get(display, "")

    def _current_account_name(self) -> str:
        display = self.account_selector.currentText().strip()
        return self.account_display_to_name.get(display, "")

    def _current_task(self):
        display = self.task_selector.currentText().strip()
        return self.task_map.get(display)

    def rebuild_account_selector(self, keep_selection: bool = True):
        current_key = self._current_account_key() if keep_selection else ""

        raw_items: list[tuple[str, str]] = []
        for account_entry in self._parse_accounts(self.account_list_edit.toPlainText()):
            username = str(account_entry.get("username", "")).strip()
            if not username:
                continue
            account_key = self._resolve_account_key_by_username(username) or username
            raw_items.append((account_key, username))

        for account_key in (self.overrides_data.get("accounts") or {}).keys():
            display_name = self._get_account_name_by_key(account_key)
            raw_items.append((str(account_key), display_name))

        dedup_items: list[tuple[str, str]] = []
        seen_keys = set()
        for account_key, account_name in raw_items:
            if not account_key or account_key in seen_keys:
                continue
            seen_keys.add(account_key)
            dedup_items.append((account_key, account_name))

        self.account_display_to_key = {}
        self.account_display_to_name = {}

        self.account_selector.blockSignals(True)
        self.account_selector.clear()

        used_display = set()
        for account_key, account_name in dedup_items:
            display = account_name or account_key
            if display in used_display:
                display = f"{display} ({account_key[-6:]})"
            used_display.add(display)

            self.account_selector.addItem(display)
            self.account_display_to_key[display] = account_key
            self.account_display_to_name[display] = account_name or account_key

        self.account_selector.blockSignals(False)

        if current_key:
            for display, key in self.account_display_to_key.items():
                if key == current_key:
                    self.account_selector.setCurrentText(display)
                    break

        if self.account_selector.count() > 0 and self.account_selector.currentIndex() < 0:
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

    def on_view_mode_changed(self, _):
        if self._building:
            return
        self.render_task_editor()

    def _build_virtual_config(self, task, account_key: str, account_name: str, only_diff: bool = False):
        task_class = task.__class__.__name__
        accounts = self.overrides_data.get("accounts") or {}
        account_map = accounts.get(account_key, {})
        if account_name and (
            not isinstance(account_map, dict) or (not account_map and account_name in accounts)
        ):
            legacy_account_map = accounts.get(account_name, {})
            if isinstance(legacy_account_map, dict):
                account_map = legacy_account_map
        task_override = account_map.get(task_class, {}) if isinstance(account_map, dict) else {}

        defaults = {}
        initial = {}
        base_values = {}
        editable_keys = []
        total_supported_keys = 0

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

            total_supported_keys += 1

            base_value = dict.get(task.config, key, default_value)
            override_value = task_override.get(key, base_value)
            value = self._coerce_like(base_value, override_value)

            if only_diff and value == base_value:
                continue

            defaults[key] = default_value
            initial[key] = value
            base_values[key] = base_value
            editable_keys.append(key)

        return InMemoryConfig(initial, defaults), editable_keys, base_values, total_supported_keys

    def render_task_editor(self):
        self._clear_layout(self.editor_layout)
        self.current_virtual_config = None
        self.current_task = None
        self.current_account_key = ""
        self.current_account_name = ""
        self.current_editable_keys = []
        self.current_base_values = {}

        account_key = self._current_account_key()
        account_name = self._current_account_name()
        if not account_key:
            self.editor_layout.addWidget(BodyLabel("请先选择账号"))
            return

        task = self._current_task()
        if task is None:
            self.editor_layout.addWidget(BodyLabel("请先选择任务"))
            return

        only_diff = bool(self.only_diff_switch.isChecked())
        virtual_config, editable_keys, base_values, total_supported_keys = self._build_virtual_config(
            task,
            account_key,
            account_name,
            only_diff=only_diff,
        )
        if not editable_keys:
            if only_diff:
                self.editor_layout.addWidget(BodyLabel("当前账号在该任务下没有差异项"))
            else:
                self.editor_layout.addWidget(BodyLabel("该任务暂无可编辑配置项"))
            return

        summary_text = (
            f"当前视图：{'仅差异项' if only_diff else '全部配置'} | "
            f"展示 {len(editable_keys)} / {total_supported_keys} 项"
        )
        self.editor_layout.addWidget(BodyLabel(summary_text))

        card = ConfigCard(
            None,
            f"{task.name} - {account_name or account_key}",
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
        self.current_account_key = account_key
        self.current_account_name = account_name
        self.current_editable_keys = editable_keys
        self.current_base_values = base_values

    def save_current_task_override(self):
        if not self.current_virtual_config or self.current_task is None or not self.current_account_key:
            self._set_status("请先选择账号与任务")
            return

        diff = {}
        for key in self.current_editable_keys:
            current_value = self.current_virtual_config.get(key)
            base_value = self.current_base_values.get(key)
            if current_value != base_value:
                diff[key] = current_value

        accounts = self.overrides_data.setdefault("accounts", {})
        account_map = accounts.setdefault(self.current_account_key, {})

        task_class = self.current_task.__class__.__name__
        if diff:
            account_map[task_class] = diff
            self._set_status(
                f"已保存：{self.current_account_name or self.current_account_key} / {self.current_task.name}（覆盖 {len(diff)} 项）"
            )
        else:
            account_map.pop(task_class, None)
            self._set_status(
                f"无差异，已清除：{self.current_account_name or self.current_account_key} / {self.current_task.name} 覆盖"
            )

        if not account_map:
            accounts.pop(self.current_account_key, None)

        self.overrides_data = save_overrides(self.overrides_data)
        self.rebuild_account_selector()

    def clear_current_task_override(self):
        account_key = self._current_account_key()
        account_name = self._current_account_name()
        task = self._current_task()
        if not account_key or task is None:
            self._set_status("请先选择账号与任务")
            return

        accounts = self.overrides_data.get("accounts", {})
        account_map = accounts.get(account_key, {})
        if account_name and (
            not isinstance(account_map, dict) or (not account_map and account_name in accounts)
        ):
            legacy_account_map = accounts.get(account_name, {})
            if isinstance(legacy_account_map, dict):
                account_map = legacy_account_map
                account_key = account_name
        task_class = task.__class__.__name__
        account_map.pop(task_class, None)
        if not account_map:
            accounts.pop(account_key, None)

        self.overrides_data = save_overrides(self.overrides_data)
        self.render_task_editor()
        self.rebuild_account_selector()
        self._set_status(f"已清空：{account_name or account_key} / {task.name} 覆盖")

    def clear_current_account_overrides(self):
        account_key = self._current_account_key()
        account_name = self._current_account_name()
        if not account_key:
            self._set_status("请先选择账号")
            return

        accounts = self.overrides_data.get("accounts", {})
        if account_key in accounts:
            accounts.pop(account_key, None)
            self.overrides_data = save_overrides(self.overrides_data)
        elif account_name in accounts:
            accounts.pop(account_name, None)
            self.overrides_data = save_overrides(self.overrides_data)

        self.rebuild_account_selector()
        self.render_task_editor()
        self._set_status(f"已清空账号全部覆盖：{account_name or account_key}")
