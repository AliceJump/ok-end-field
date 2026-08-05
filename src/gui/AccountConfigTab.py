from __future__ import annotations

import copy
from collections import OrderedDict
from typing import Any, Dict

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    FluentIcon,
    LineEdit,
    NavigationItemPosition,
    PrimaryPushButton,
    PushButton,
    SwitchButton,
    TextEdit,
)

from ok.gui.tasks.ConfigCard import ConfigCard, og
from ok.gui.tasks.LabelAndWidget import LabelAndWidget
from ok.gui.widget.CustomTab import CustomTab
from ok.gui.Communicate import communicate
from src.tasks.account.account_scope_store import (
    get_account_map_content,
    load_overrides,
    parse_account_list_text,
    sync_account_list_text,
    update_overrides,
)
from src.core.global_config_store import (
    ZIP_LINE_CONFIG_NAME,
    ZIP_LINE_CONFIG_TYPE,
    ZIP_LINE_CONFIG_DESCRIPTION,
    ZIP_LINE_DEFAULT_CONFIG,
    get_global_config,
)


class GlobalZipLineConfigProxy:
    """Expose the global zipline schema in the per-account editor."""

    name = "滑索配置"
    icon = FluentIcon.SETTING
    account_override_name = ZIP_LINE_CONFIG_NAME
    support_multi_account = True
    running = False
    default_config = ZIP_LINE_DEFAULT_CONFIG
    config = get_global_config(ZIP_LINE_CONFIG_NAME)
    config_description = ZIP_LINE_CONFIG_DESCRIPTION
    config_type = ZIP_LINE_CONFIG_TYPE
    account_config_blacklist = set()
    account_config_whitelist = set()
    account_config_defaults = {}
    account_config_description = {}
    account_config_type = {}


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
    ALWAYS_HIDDEN_CONFIG_KEYS = {"多账户模式", "多账户独立配置", "账号列表"}

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
        self.current_original_values: Dict[str, Any] = {}
        self.current_editor_card = None
        self.current_account_list_value = ""
        self.current_map_account_key = ""
        self.current_map_value = ""
        self._editor_cards: OrderedDict[tuple[Any, ...], ConfigCard] = OrderedDict()
        self._task_expand_state: Dict[str, bool] = {}
        self.account_display_to_key: Dict[str, str] = {}
        self.account_display_to_name: Dict[str, str] = {}

        self._build_ui()
        communicate.task.connect(self._on_task_state_changed)

    @property
    def name(self):
        # MainWindow 会对 tab 的 name 统一调用 self.app.tr(name)，
        # 这里必须返回源 key（"账号配置"）而非已翻译文本，
        # 否则会对翻译结果二次 tr()，把繁体 key 当作待翻译字符串收集进 ok.po。
        return "账号配置"

    @property
    def position(self):
        return NavigationItemPosition.TOP

    @property
    def add_after_default_tabs(self):
        return False

    @property
    def icon(self):
        return FluentIcon.PEOPLE

    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded_once and self.executor is not None:
            self._loaded_once = True
            self.refresh_from_source()
        elif self.executor is not None:
            self.sync_from_source()

    def sync_from_source(self):
        self._save_pending_changes()
        self._building = True
        try:
            self.overrides_data = load_overrides()
            account_list = str(self.overrides_data.get("account_list_text", "") or "")
            if self.account_list_edit.toPlainText() == self.current_account_list_value:
                self.account_list_edit.setPlainText(account_list)
            self.current_account_list_value = account_list
            self.rebuild_account_selector()
            self.rebuild_task_selector()
            self.load_current_map_content()
            self.render_task_editor()
        finally:
            self._building = False

    def _build_ui(self):
        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)
        tip = BodyLabel(og.app.tr(
            "按账号和任务配置独立参数。先选账号，再选任务，下面会自动出现该任务的属性控件。"
            "账号页只需要填写账号名（手机号），无需填写密码。系统兼容旧格式 `账号,密码` 但不会保存密码。"
            "登录时也可只使用手机号后四位进行匹配（若唯一）。"
        ))
        tip.setWordWrap(True)
        header_layout.addWidget(tip)
        self.add_card(og.app.tr("账号配置中心"), header)

        base_widget = QWidget()
        base_layout = QVBoxLayout(base_widget)
        base_layout.setContentsMargins(0, 0, 0, 0)
        base_layout.setSpacing(8)

        account_list_row = LabelAndWidget("账号列表", "每行一个账号名（手机号），无需密码")
        self.account_list_edit = TextEdit()
        self.account_list_edit.setFixedWidth(420)
        self.account_list_edit.setMinimumHeight(120)
        self.account_list_edit.setPlaceholderText(og.app.tr("手机号A\n手机号B"))
        account_list_row.add_widget(self.account_list_edit, stretch=0)
        base_layout.addWidget(account_list_row)

        base_action_row = LabelAndWidget("账号列表操作")
        base_action_layout = QHBoxLayout()
        base_action_layout.addStretch(1)
        self.save_base_button = PrimaryPushButton(og.app.tr("保存账号列表"))
        base_action_layout.addWidget(self.save_base_button)
        base_action_row.add_layout(base_action_layout, stretch=1)
        base_layout.addWidget(base_action_row)

        self.add_card(og.app.tr("账号基础设置"), base_widget)

        selector_widget = QWidget()
        selector_layout = QVBoxLayout(selector_widget)
        selector_layout.setContentsMargins(0, 0, 0, 0)
        selector_layout.setSpacing(8)

        account_selector_row = LabelAndWidget("账号", "从账号列表或已有覆盖中选择")
        self.account_selector = ComboBox()
        self.account_selector.setMinimumWidth(220)
        account_selector_row.add_widget(self.account_selector, stretch=0)
        selector_layout.addWidget(account_selector_row)

        map_content_row = LabelAndWidget(
            "地图同步 content",
            "当前账号的 hg/check data.content 值，用于官方地图位置同步",
        )
        self.map_content_edit = LineEdit()
        self.map_content_edit.setFixedWidth(420)
        self.map_content_edit.setPlaceholderText(og.app.tr("只填写 data.content 的字符串值"))
        map_content_row.add_widget(self.map_content_edit, stretch=0)
        selector_layout.addWidget(map_content_row)

        task_selector_row = LabelAndWidget("任务", "选择任务后自动渲染属性控件")
        self.task_selector = ComboBox()
        self.task_selector.setMinimumWidth(280)
        task_selector_row.add_widget(self.task_selector, stretch=0)
        selector_layout.addWidget(task_selector_row)

        view_row = LabelAndWidget("视图", "开启后仅显示与原配置不同的项")
        self.only_diff_switch = SwitchButton()
        self.only_diff_switch.setOnText(og.app.tr("仅差异"))
        self.only_diff_switch.setOffText(og.app.tr("全部"))
        view_row.add_widget(self.only_diff_switch, stretch=0)
        selector_layout.addWidget(view_row)

        action_row = LabelAndWidget("账号任务覆盖操作")
        action_layout = QHBoxLayout()
        action_layout.addStretch(1)
        self.save_current_config_button = PrimaryPushButton(og.app.tr("保存当前账号配置"))
        self.clear_task_override_button = PushButton(og.app.tr("清空当前任务覆盖"))
        self.clear_account_override_button = PushButton(og.app.tr("清空当前账号全部覆盖"))
        action_layout.addWidget(self.save_current_config_button)
        action_layout.addWidget(self.clear_task_override_button)
        action_layout.addWidget(self.clear_account_override_button)
        action_row.add_layout(action_layout, stretch=1)
        selector_layout.addWidget(action_row)

        self.add_card(og.app.tr("账号任务选择"), selector_widget)

        editor_widget = QWidget()
        self.editor_layout = QVBoxLayout(editor_widget)
        self.editor_layout.setContentsMargins(0, 0, 0, 0)
        self.editor_layout.setSpacing(8)
        self.editor_summary_label = BodyLabel()
        self.editor_empty_label = BodyLabel(og.app.tr("请先选择账号与任务"))
        self.editor_layout.addWidget(self.editor_summary_label)
        self.editor_layout.addWidget(self.editor_empty_label)
        self.editor_summary_label.hide()
        self.add_card(og.app.tr("任务属性配置"), editor_widget)

        status_widget = QWidget()
        status_layout = QVBoxLayout(status_widget)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(4)
        self.status_label = BodyLabel(og.app.tr("就绪"))
        self.status_label.setWordWrap(True)
        status_layout.addWidget(self.status_label)
        self.add_card(og.app.tr("状态"), status_widget)

        self.save_base_button.clicked.connect(self.save_base_settings)
        self.account_selector.currentTextChanged.connect(self.on_account_changed)
        self.task_selector.currentTextChanged.connect(self.on_task_changed)
        self.only_diff_switch.checkedChanged.connect(self.on_view_mode_changed)
        self.save_current_config_button.clicked.connect(self.save_current_account_config)
        self.clear_task_override_button.clicked.connect(self.clear_current_task_override)
        self.clear_account_override_button.clicked.connect(self.clear_current_account_overrides)

    def _set_status(self, text: str):
        self.status_label.setText(text)

    def _ensure_executor(self):
        if self.executor is None:
            self._set_status(og.app.tr("界面初始化中，请稍候"))
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
    def _config_key_set(task, attribute: str) -> set[str]:
        value = getattr(task, attribute, None)
        if isinstance(value, str):
            return {value}
        if isinstance(value, (list, tuple, set)):
            return {str(key) for key in value}
        return set()

    @staticmethod
    def _task_storage_name(task) -> str:
        return str(getattr(task, "account_override_name", task.__class__.__name__))

    def _account_config_schema(self, task, task_override: Dict[str, Any]) -> Dict[str, Any]:
        schema = dict(task.default_config)
        extra_defaults = getattr(task, "account_config_defaults", None)
        if isinstance(extra_defaults, dict):
            schema.update(extra_defaults)

        whitelist = self._config_key_set(task, "account_config_whitelist")
        for key in whitelist:
            if key in schema:
                continue
            if key in task_override:
                schema[key] = task_override[key]
            elif key in task.config:
                schema[key] = dict.get(task.config, key)
        return schema

    def _account_config_rules(self, task) -> tuple[set[str], set[str]]:
        blacklist = self.ALWAYS_HIDDEN_CONFIG_KEYS | self._config_key_set(task, "account_config_blacklist")
        whitelist = self._config_key_set(task, "account_config_whitelist")

        config_types = dict(task.config_type or {})
        config_types.update(getattr(task, "account_config_type", {}) or {})
        for key, type_meta in config_types.items():
            if not isinstance(type_meta, dict):
                continue
            if type_meta.get("type") == "button" or (
                "type" not in type_meta and ("buttons" in type_meta or "callback" in type_meta)
            ):
                blacklist.add(key)
            sub_configs = type_meta.get("sub_configs")
            if isinstance(sub_configs, dict):
                other_keys = sub_configs.get("其他配置", [])
                if isinstance(other_keys, str):
                    blacklist.add(other_keys)
                elif isinstance(other_keys, (list, tuple, set)):
                    blacklist.update(str(item) for item in other_keys)

        if "配置选择" in task.default_config or "配置选择" in config_types:
            whitelist.add("配置选择")

        whitelist -= blacklist
        return blacklist, whitelist

    @staticmethod
    def _account_config_base_value(task, key: str, default_value: Any) -> Any:
        if key in task.config:
            return dict.get(task.config, key, default_value)
        provider = getattr(task, "get_account_config_base_value", None)
        if callable(provider):
            return provider(key, default_value)
        return default_value

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
            class_name = AccountConfigTab._task_storage_name(task)
            if class_name in seen:
                continue
            seen.add(class_name)
            tasks.append(task)
        tasks.append(GlobalZipLineConfigProxy)
        return tasks

    def refresh_from_source(self):
        if not self._ensure_executor():
            return

        self._building = True
        try:
            self.overrides_data = load_overrides(force=True)
            self.current_account_list_value = str(self.overrides_data.get("account_list_text", "") or "")
            self.account_list_edit.setPlainText(self.current_account_list_value)

            tasks = self._collect_tasks()

            self.rebuild_account_selector(keep_selection=False)
            self.rebuild_task_selector(keep_selection=False)
            self.load_current_map_content()
            self.render_task_editor()

            if not tasks:
                self._set_status(og.app.tr("未找到 support_multi_account=True 的任务"))
            else:
                self._set_status(og.app.tr("已刷新账号与任务配置（账号页账号列表与任务账号列表独立）"))
        finally:
            self._building = False

    def save_base_settings(self):
        if not self._ensure_executor():
            return

        self._save_pending_changes()
        account_list = self.account_list_edit.toPlainText().strip()

        summary = sync_account_list_text(account_list)
        self.overrides_data = load_overrides(force=True)
        self.current_account_list_value = str(self.overrides_data.get("account_list_text", "") or "")
        self.account_list_edit.setPlainText(self.current_account_list_value)

        self.rebuild_account_selector()
        self.load_current_map_content()
        self.render_task_editor()
        status = (
            og.app.tr("账号列表已保存")
            + og.app.tr("（复用ID {reused}，新建ID {created}）").format(
                reused=summary.get('reused_count', 0),
                created=summary.get('created_count', 0),
            )
        )
        status += og.app.tr("；账号名（手机号）是唯一ID，密码变化不影响ID，账号名变化会新建ID")
        status += og.app.tr("；账号页无需填写密码，保存时会移除任何密码信息（仅保留用户名）")

        invalid_count = int(summary.get("invalid_count", 0) or 0)
        if invalid_count > 0:
            status += og.app.tr("；忽略无效行 {count} 条").format(count=invalid_count)

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

        for account_key in (self.overrides_data.get("map_contents") or {}).keys():
            display_name = self._get_account_name_by_key(account_key)
            raw_items.append((str(account_key), display_name))

        dedup_items: list[tuple[str, str]] = []
        seen_keys = set()
        for account_key, account_name in raw_items:
            if not account_key or account_key in seen_keys:
                continue
            seen_keys.add(account_key)
            dedup_items.append((account_key, account_name))

        display_to_key = {}
        display_to_name = {}
        displays = []
        used_display = set()
        for account_key, account_name in dedup_items:
            display = account_name or account_key
            if display in used_display:
                display = f"{display} ({account_key[-6:]})"
            used_display.add(display)
            displays.append(display)
            display_to_key[display] = account_key
            display_to_name[display] = account_name or account_key

        selected_display = next(
            (display for display, key in display_to_key.items() if key == current_key),
            displays[0] if displays else "",
        )
        current_displays = [self.account_selector.itemText(i) for i in range(self.account_selector.count())]

        self.account_selector.blockSignals(True)
        try:
            if current_displays != displays:
                self.account_selector.clear()
                for display in displays:
                    self.account_selector.addItem(display)
            self.account_display_to_key = display_to_key
            self.account_display_to_name = display_to_name
            if selected_display and self.account_selector.currentText() != selected_display:
                self.account_selector.setCurrentText(selected_display)
        finally:
            self.account_selector.blockSignals(False)

    def rebuild_task_selector(self, keep_selection: bool = True):
        current_task = self._current_task()
        current_class_name = AccountConfigTab._task_storage_name(current_task) if keep_selection and current_task else ""

        task_map = {}
        displays = []
        for task in self._collect_tasks():
            display = f"{og.app.tr(task.name)} ({AccountConfigTab._task_storage_name(task)})"
            task_map[display] = task
            displays.append(display)

        selected_display = next(
            (
                display for display, task in task_map.items()
                if AccountConfigTab._task_storage_name(task) == current_class_name
            ),
            displays[0] if displays else "",
        )
        current_displays = [self.task_selector.itemText(i) for i in range(self.task_selector.count())]

        self.task_selector.blockSignals(True)
        try:
            if current_displays != displays:
                self.task_selector.clear()
                for display in displays:
                    self.task_selector.addItem(display)
            self.task_map = task_map
            if selected_display and self.task_selector.currentText() != selected_display:
                self.task_selector.setCurrentText(selected_display)
        finally:
            self.task_selector.blockSignals(False)

    def on_account_changed(self, _):
        if self._building:
            return
        self._save_pending_changes()
        self.load_current_map_content()
        self.render_task_editor()

    def load_current_map_content(self):
        account_key = self._current_account_key()
        account_name = self._current_account_name()
        self.current_map_account_key = account_key
        if not account_key:
            self.current_map_value = ""
            self.map_content_edit.setText("")
            return
        self.current_map_value = get_account_map_content(account_key, account_name=account_name)
        self.map_content_edit.setText(self.current_map_value)

    def on_task_changed(self, _):
        if self._building:
            return
        self._save_pending_changes()
        self.render_task_editor()

    def on_view_mode_changed(self, _):
        if self._building:
            return
        self._save_pending_changes()
        self.render_task_editor()

    def _build_virtual_config(self, task, account_key: str, account_name: str, only_diff: bool = False):
        task_class = AccountConfigTab._task_storage_name(task)
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
        blacklist, whitelist = self._account_config_rules(task)

        for key, default_value in self._account_config_schema(task, task_override).items():
            forced = key in whitelist
            if key in blacklist:
                continue
            if str(key).startswith("_") and not forced:
                continue

            type_meta = task.config_type.get(key) if task.config_type else None
            account_config_type = getattr(task, "account_config_type", None)
            if isinstance(account_config_type, dict) and key in account_config_type:
                type_meta = account_config_type.get(key)
            if type_meta and type_meta.get("type") in {"global", "button"} and not forced:
                continue

            if not self._is_supported_value(default_value):
                continue

            total_supported_keys += 1

            base_value = self._account_config_base_value(task, key, default_value)
            override_value = task_override.get(key, base_value)
            value = self._coerce_like(base_value, override_value)

            if only_diff and value == base_value and not forced:
                continue

            defaults[key] = default_value
            initial[key] = value
            base_values[key] = base_value
            editable_keys.append(key)

        return InMemoryConfig(initial, defaults), editable_keys, base_values, total_supported_keys

    def _hide_task_editor(self):
        if self.current_editor_card is not None:
            if self.current_task is not None:
                self._task_expand_state[AccountConfigTab._task_storage_name(self.current_task)] = bool(
                    self.current_editor_card.isExpand
                )
            self.current_editor_card.hide()
        self.current_editor_card = None
        self.editor_summary_label.hide()
        self.editor_empty_label.hide()

    def _set_current_task_editor_enabled(self, enabled: bool):
        if self.current_editor_card is not None:
            for widget in self.current_editor_card.config_widgets:
                widget.setEnabled(enabled)
            if self.current_editor_card.reset_config is not None:
                self.current_editor_card.reset_config.setEnabled(enabled)
        self.save_current_config_button.setEnabled(enabled)
        self.clear_task_override_button.setEnabled(enabled)
        self.clear_account_override_button.setEnabled(enabled)

    def _on_task_state_changed(self, _):
        task = self.current_task
        self._set_current_task_editor_enabled(not bool(task and task.running))

    def render_task_editor(self):
        self._hide_task_editor()
        self.current_virtual_config = None
        self.current_task = None
        self.current_account_key = ""
        self.current_account_name = ""
        self.current_editable_keys = []
        self.current_base_values = {}
        self.current_original_values = {}

        account_key = self._current_account_key()
        account_name = self._current_account_name()
        if not account_key:
            self.editor_empty_label.setText(og.app.tr("请先选择账号"))
            self.editor_empty_label.show()
            return

        task = self._current_task()
        if task is None:
            self.editor_empty_label.setText(og.app.tr("请先选择任务"))
            self.editor_empty_label.show()
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
                empty_text = og.app.tr("当前账号在该任务下没有差异项")
            else:
                empty_text = og.app.tr("该任务暂无可编辑配置项")
            self.editor_empty_label.setText(empty_text)
            self.editor_empty_label.show()
            return

        view_mode = og.app.tr("仅差异项") if only_diff else og.app.tr("全部配置")
        summary_text = (
            og.app.tr("当前视图：{view_mode} | 展示 {count} / {total} 项")
            .format(view_mode=view_mode, count=len(editable_keys), total=total_supported_keys)
        )
        self.editor_summary_label.setText(summary_text)
        self.editor_summary_label.show()

        config_description = dict(task.config_description or {})
        config_description.update(getattr(task, "account_config_description", {}) or {})
        config_type = dict(task.config_type or {})
        config_type.update(getattr(task, "account_config_type", {}) or {})
        config_type = {key: value for key, value in config_type.items() if key in editable_keys}

        cache_key = (
            id(task),
            tuple(editable_keys),
            repr(config_type),
            repr(config_description),
        )
        card = self._editor_cards.get(cache_key)
        if card is None:
            card = ConfigCard(
                None,
                task.name,
                virtual_config,
                "保存当前账号的完整任务配置快照。任务默认值变化不会影响已保存账号。",
                {},
                config_description,
                config_type,
                task.icon,
            )
            card.card.setTitle(f"{og.app.tr(task.name)} - {account_name or account_key}")
            self._editor_cards[cache_key] = card
            self.editor_layout.addWidget(card)
            while len(self._editor_cards) > 8:
                _, stale_card = self._editor_cards.popitem(last=False)
                self.editor_layout.removeWidget(stale_card)
                stale_card.deleteLater()
        else:
            self._editor_cards.move_to_end(cache_key)
            card.config.clear()
            card.config.update(virtual_config)
            card.config.default = dict(virtual_config.default)
            card.card.setTitle(f"{og.app.tr(task.name)} - {account_name or account_key}")
            card.update_config()

        desired_expand_state = self._task_expand_state.get(AccountConfigTab._task_storage_name(task), card.isExpand)
        card.setExpand(desired_expand_state)
        card.show()

        self.current_virtual_config = card.config
        self.current_task = task
        self.current_account_key = account_key
        self.current_account_name = account_name
        self.current_editable_keys = editable_keys
        self.current_base_values = base_values
        self.current_original_values = copy.deepcopy(dict(card.config))
        self.current_editor_card = card
        self._set_current_task_editor_enabled(not bool(task.running))

    def _apply_current_task_override(self, cleanup_blacklist: bool = False) -> bool:
        if self.current_virtual_config is None or self.current_task is None or not self.current_account_key:
            return False

        accounts = self.overrides_data.setdefault("accounts", {})
        account_map = accounts.setdefault(self.current_account_key, {})

        task_class = AccountConfigTab._task_storage_name(self.current_task)
        existing_config = account_map.get(task_class, {})
        snapshot, snapshot_keys, _, _ = self._build_virtual_config(
            self.current_task,
            self.current_account_key,
            getattr(self, "current_account_name", ""),
            only_diff=False,
        )
        full_config = {
            key: copy.deepcopy(snapshot[key])
            for key in snapshot_keys
            if key in snapshot
        }
        for key in self.current_editable_keys:
            if key not in self.current_virtual_config:
                continue
            full_config[key] = copy.deepcopy(self.current_virtual_config[key])

        changed = full_config != existing_config
        if full_config:
            account_map[task_class] = full_config
        else:
            account_map.pop(task_class, None)

        if not account_map:
            accounts.pop(self.current_account_key, None)
        return changed

    def _apply_current_map_content(self) -> bool:
        if not self.current_map_account_key:
            return False

        content = self.map_content_edit.text().strip()
        map_contents = self.overrides_data.setdefault("map_contents", {})
        previous = str(map_contents.get(self.current_map_account_key, self.current_map_value) or "").strip()
        if content == previous:
            return False

        if content:
            map_contents[self.current_map_account_key] = content
        else:
            map_contents.pop(self.current_map_account_key, None)
        self.current_map_value = content
        return True

    def _has_current_task_changes(self) -> bool:
        if self.current_virtual_config is None:
            return False
        return any(
            key in self.current_virtual_config
            and self.current_virtual_config[key] != self.current_original_values.get(key)
            for key in self.current_editable_keys
        )

    def _save_pending_changes(self, show_status: bool = False, cleanup_blacklist: bool = False) -> bool:
        task_dirty = self._has_current_task_changes()
        map_dirty = bool(
            self.current_map_account_key
            and self.map_content_edit.text().strip() != self.current_map_value
        )
        if not task_dirty and not map_dirty and not cleanup_blacklist:
            if show_status:
                self._set_status(og.app.tr("当前账号配置没有变化"))
            return False

        changes = {"task": False, "map": False}

        def merge(latest):
            self.overrides_data = latest
            if task_dirty or cleanup_blacklist:
                changes["task"] = self._apply_current_task_override(
                    cleanup_blacklist=cleanup_blacklist
                )
            if map_dirty:
                changes["map"] = self._apply_current_map_content()
            return self.overrides_data

        self.overrides_data = update_overrides(merge)
        changed = changes["task"] or changes["map"]
        if task_dirty and self.current_virtual_config is not None:
            self.current_original_values = copy.deepcopy(dict(self.current_virtual_config))
        if map_dirty:
            self.current_map_value = self.map_content_edit.text().strip()

        if show_status:
            account = self.current_account_name or self._get_account_name_by_key(self.current_map_account_key)
            message = og.app.tr("已保存当前账号配置" if changed else "当前账号配置没有变化")
            self._set_status(f"{message}：{account}" if account else message)
        return changed

    def save_current_account_config(self):
        if not self.current_map_account_key and not self.current_account_key:
            self._set_status(og.app.tr("请先选择账号"))
            return
        self._save_pending_changes(show_status=True, cleanup_blacklist=True)

    def clear_current_task_override(self):
        account_key = self._current_account_key()
        account_name = self._current_account_name()
        task = self._current_task()
        if not account_key or task is None:
            self._set_status(og.app.tr("请先选择账号与任务"))
            return

        map_dirty = bool(
            self.current_map_account_key
            and self.map_content_edit.text().strip() != self.current_map_value
        )
        task_class = AccountConfigTab._task_storage_name(task)

        def clear_task(latest):
            self.overrides_data = latest
            if map_dirty:
                self._apply_current_map_content()
            accounts = self.overrides_data.get("accounts", {})
            target_key = account_key
            account_map = accounts.get(target_key, {})
            if account_name and (
                not isinstance(account_map, dict) or (not account_map and account_name in accounts)
            ):
                legacy_account_map = accounts.get(account_name, {})
                if isinstance(legacy_account_map, dict):
                    account_map = legacy_account_map
                    target_key = account_name
            account_map.pop(task_class, None)
            if not account_map:
                accounts.pop(target_key, None)
            return self.overrides_data

        self.overrides_data = update_overrides(clear_task)
        if map_dirty:
            self.current_map_value = self.map_content_edit.text().strip()
        self.rebuild_account_selector()
        self.load_current_map_content()
        self.render_task_editor()
        self._set_status(og.app.tr("已清空：{account} / {task} 覆盖").format(
            account=account_name or account_key, task=task.name
        ))

    def clear_current_account_overrides(self):
        account_key = self._current_account_key()
        account_name = self._current_account_name()
        if not account_key:
            self._set_status(og.app.tr("请先选择账号"))
            return

        map_dirty = bool(
            self.current_map_account_key
            and self.map_content_edit.text().strip() != self.current_map_value
        )

        def clear_account(latest):
            self.overrides_data = latest
            if map_dirty:
                self._apply_current_map_content()
            accounts = self.overrides_data.get("accounts", {})
            if account_key in accounts:
                accounts.pop(account_key, None)
            elif account_name in accounts:
                accounts.pop(account_name, None)
            return self.overrides_data

        self.overrides_data = update_overrides(clear_account)
        if map_dirty:
            self.current_map_value = self.map_content_edit.text().strip()

        self.rebuild_account_selector()
        self.load_current_map_content()
        self.render_task_editor()
        self._set_status(og.app.tr("已清空账号全部覆盖：{account}").format(
            account=account_name or account_key
        ))
