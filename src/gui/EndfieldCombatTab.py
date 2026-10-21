"""Shared combat settings and existing task/account exceptions in one page."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from ok import og
from ok.ui.qt.tasks.ConfigCard import ConfigCard
from ok.ui.qt.widget.Tab import Tab
from PySide6.QtCore import QTimer  # type: ignore
from PySide6.QtWidgets import (  # type: ignore
    QApplication,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import ComboBox, FluentIcon  # type: ignore

from src.core.base_mixin.account_override_mixin import AccountOverrideMixin
from src.core.BattleConfig import (
    BATTLE_CONFIG_DESCRIPTION,
    BATTLE_CONFIG_MODE_KEY,
    BATTLE_CONFIG_NAME,
    BATTLE_CONFIG_TYPE,
    DEFAULT_BATTLE_CONFIG,
)
from src.core.global_config_store import get_global_config
from src.gui.EndfieldPrimitives import EndfieldActionButton, ui_tr
from src.image.enemy_health_probe import KEY_SAVE_ENEMY_PRESENCE_FRAMES
from src.tasks.account.account_scope_store import load_overrides, update_overrides


def task_storage_name(task: Any) -> str:
    return str(getattr(task, "account_override_name", task.__class__.__name__))


def resolve_battle_values(task: Any, overrides: dict, common: dict) -> tuple[bool, dict]:
    """Match the runtime account → task → shared priority without binding an account."""
    base_mode = dict.get(task.config, BATTLE_CONFIG_MODE_KEY, False)
    mode = AccountOverrideMixin._coerce_override_value(base_mode, overrides.get(BATTLE_CONFIG_MODE_KEY, base_mode))
    independent = task._parse_use_independent(mode)
    values = {}
    for key, default in DEFAULT_BATTLE_CONFIG.items():
        shared_value = common.get(key, default)
        if independent:
            base = dict.get(task.config, key, shared_value)
            values[key] = deepcopy(AccountOverrideMixin._coerce_override_value(base, overrides.get(key, base)))
        else:
            values[key] = deepcopy(shared_value)
    return independent, values


class CombatEditBuffer(dict):
    def get_default(self, key):
        return DEFAULT_BATTLE_CONFIG.get(key)

    def has_user_config(self):
        return bool(self)


class SharedCombatConfig(dict):
    """Check the live invocation at the actual write, including runs without UI signals."""

    def __init__(self, source, editable, keys=None):
        super().__init__(source if keys is None else {key: source[key] for key in keys})
        self._source = source
        self._editable = editable

    def __setitem__(self, key, value):
        if self._editable():
            self._source[key] = value
            super().__setitem__(key, value)

    def get_default(self, key):
        return self._source.get_default(key)

    def has_user_config(self):
        return self._source.has_user_config()


@dataclass
class CombatTarget:
    label: str
    task: Any = None
    account_key: str = ""


class EndfieldCombatTab(Tab):
    def __init__(self):
        super().__init__()
        self._targets: list[CombatTarget] = []
        self._editor: ConfigCard | None = None
        self._debug_editor: ConfigCard | None = None
        self._buffer: CombatEditBuffer | None = None
        self._original: dict = {}
        self._loaded = False
        self._target = CombatTarget(ui_tr("通用战斗设置"))
        self._target_index = 0
        self._state_timer = QTimer(self)
        self._state_timer.setInterval(150)
        self._state_timer.timeout.connect(self._on_task_state_changed)

        selector = QWidget(self)
        self._selector = selector
        selector.setObjectName("combatSettingsHeader")
        selector.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        layout = QVBoxLayout(selector)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        row = QHBoxLayout()
        row.addWidget(QLabel(ui_tr("配置范围"), selector))
        self.target_combo = ComboBox(selector)
        self.target_combo.setMinimumWidth(280)
        row.addWidget(self.target_combo)
        row.addStretch(1)
        self.save_button = EndfieldActionButton(FluentIcon.SAVE, ui_tr("保存专用设置"), selector)
        self.shared_button = EndfieldActionButton(None, ui_tr("改用通用设置"), selector)
        self.custom_button = EndfieldActionButton(None, ui_tr("改用专用设置"), selector)
        row.addWidget(self.custom_button)
        row.addWidget(self.save_button)
        row.addWidget(self.shared_button)
        layout.addLayout(row)
        self.source_label = QLabel(selector)
        self.source_label.setObjectName("combatSource")
        self.source_label.setWordWrap(True)
        layout.addWidget(self.source_label)
        self.vBoxLayout.insertWidget(0, selector)
        self.target_combo.currentIndexChanged.connect(self._select_target)
        self.save_button.clicked.connect(self._save_settings)
        self.shared_button.clicked.connect(self._use_shared)
        self.custom_button.clicked.connect(self._use_task_settings)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._save_settings)
        from ok.ui.qt.Communicate import communicate

        communicate.task.connect(self._on_task_state_changed)  # type: ignore[attr-defined]  # EventBus creates signals dynamically.

        # The task's enable switch belongs to the world page; only its diagnostic option lives here.
        self.auto_task = next(
            (t for t in getattr(og.executor, "trigger_tasks", []) if t.__class__.__name__ == "AutoCombatTask"),
            None,
        )
        if self.auto_task is not None:
            debug_config = SharedCombatConfig(
                self.auto_task.config,
                lambda: not self._battle_is_running(),
                keys=(KEY_SAVE_ENEMY_PRESENCE_FRAMES,),
            )
            self._debug_editor = ConfigCard(
                None,
                "敌人检测",
                debug_config,
                "自动战斗诊断设置",
                {},
                self.auto_task.config_description,
                {},
                FluentIcon.DEVELOPER_TOOLS,
            )
            self._debug_editor.card.iconLabel.hide()
            self.vBoxLayout.addWidget(self._debug_editor)
        self.vBoxLayout.addStretch(1)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded:
            self._loaded = True
            self._load_targets()
        elif not self._battle_is_running():
            # 账号页可能已新增或清空覆盖：先保存当前编辑，再重建范围并按任务和账号保留选择
            self._save_settings()
            self._load_targets(keep=(self._target.task, self._target.account_key))
        self._state_timer.start()

    def _load_targets(self, keep: tuple[Any, str] | None = None):
        targets = [CombatTarget(ui_tr("通用战斗设置"))]
        tasks = list(getattr(og.executor, "onetime_tasks", [])) + list(getattr(og.executor, "trigger_tasks", []))
        tasks = [t for t in tasks if BATTLE_CONFIG_MODE_KEY in (getattr(t, "default_config", {}) or {})]
        data = load_overrides()
        for task in tasks:
            targets.append(CombatTarget(ui_tr(task.name), task))
            storage = task_storage_name(task)
            for account_key, task_map in data.get("accounts", {}).items():
                overrides = task_map.get(storage, {})
                if not any(k in overrides for k in (*DEFAULT_BATTLE_CONFIG, BATTLE_CONFIG_MODE_KEY)):
                    continue
                # Names stay in the application; do not put account identities in logs.
                account_name = data.get("account_registry", {}).get(account_key, {}).get("username", account_key)
                targets.append(CombatTarget(f"{account_name} / {ui_tr(task.name)}", task, account_key))
        index = next((i for i, t in enumerate(targets) if (t.task, t.account_key) == keep), 0)
        if keep is not None and (targets[index].task, targets[index].account_key) == keep:
            # Same scope at a new position: keep the editor's expanded state.
            self._target_index = index
        self._targets = targets
        self.target_combo.blockSignals(True)
        self.target_combo.clear()
        self.target_combo.addItems([t.label for t in targets])
        self.target_combo.setCurrentIndex(index)
        self.target_combo.blockSignals(False)
        self._select_target(index)

    def _overrides(self, target: CombatTarget) -> dict:
        if not target.account_key:
            return {}
        data = load_overrides()
        return data.get("accounts", {}).get(target.account_key, {}).get(task_storage_name(target.task), {})

    def _select_target(self, index: int):
        if not 0 <= index < len(self._targets):
            return
        # A running invocation may block persistence, so keep its pending buffer intact.
        if self._editor is not None and self._battle_is_running():
            self.target_combo.blockSignals(True)
            self.target_combo.setCurrentIndex(self._target_index)
            self.target_combo.blockSignals(False)
            return
        expanded = bool(self._editor and index == self._target_index and self._editor.isExpand)
        self._save_settings()
        self._target_index = index
        self._target = target = self._targets[index]
        common = get_global_config(BATTLE_CONFIG_NAME)
        independent, values = (
            resolve_battle_values(target.task, self._overrides(target), common)
            if target.task is not None
            else (False, {})
        )
        if self._editor is not None:
            self.vBoxLayout.removeWidget(self._editor)
            self._editor.hide()
            self._editor.deleteLater()
        self._buffer = CombatEditBuffer(values) if independent else None
        self._original = deepcopy(values)
        config = self._buffer if independent else SharedCombatConfig(common, lambda: not self._battle_is_running())
        source = "专用设置" if independent else "通用战斗设置"
        self.source_label.setText(
            ui_tr(
                "{}：使用专用设置。修改后保存；仅影响当前范围。"
                if independent
                else "{}：使用通用战斗设置。修改后自动保存，所有沿用通用设置的任务同步生效。"
            ).format(target.label)
        )
        self.save_button.setVisible(independent)
        self.shared_button.setVisible(independent)
        self.custom_button.setVisible(target.task is not None and not independent)
        self._editor = ConfigCard(
            None,
            source,
            config,
            "战斗策略与技能设置",
            {},
            BATTLE_CONFIG_DESCRIPTION,
            BATTLE_CONFIG_TYPE,
            FluentIcon.PLAY,
        )
        self._editor.card.iconLabel.hide()
        self._editor.setExpand(expanded)
        self.vBoxLayout.insertWidget(self.vBoxLayout.indexOf(self._selector) + 1, self._editor)
        self._on_task_state_changed()

    def _battle_is_running(self) -> bool:
        current = getattr(og.executor, "current_task", None)
        return bool(getattr(current, "running", False)) or any(
            getattr(target.task, "running", False) for target in self._targets if target.task
        )

    def _on_task_state_changed(self, *_args):
        running = self._battle_is_running()
        if self._editor is not None:
            self._editor.setEnabled(not running)
        if self._debug_editor is not None:
            self._debug_editor.setEnabled(not running)
        self.target_combo.setEnabled(not running)
        self.save_button.setEnabled(not running)
        self.shared_button.setEnabled(not running)
        self.custom_button.setEnabled(not running)

    def _save_settings(self):
        if self._buffer is None or self._target.task is None:
            return
        if self._battle_is_running():
            return
        changes = {k: deepcopy(v) for k, v in self._buffer.items() if v != self._original.get(k)}
        if not changes:
            return
        if self._target.account_key:
            target = self._target

            def merge(data):
                current = (
                    data.setdefault("accounts", {})
                    .setdefault(target.account_key, {})
                    .setdefault(task_storage_name(target.task), {})
                )
                current.update(changes)
                return data

            update_overrides(merge)
        else:
            for key, value in changes.items():
                self._target.task.config[key] = value
        self._original = deepcopy(dict(self._buffer))

    def _use_task_settings(self):
        target = self._target
        if target.task is None or self._battle_is_running():
            return
        if target.account_key:

            def merge(data):
                data.setdefault("accounts", {}).setdefault(target.account_key, {}).setdefault(
                    task_storage_name(target.task), {}
                )[BATTLE_CONFIG_MODE_KEY] = True
                return data

            update_overrides(merge)
        else:
            target.task.config[BATTLE_CONFIG_MODE_KEY] = True
        self._select_target(self.target_combo.currentIndex())

    def _use_shared(self):
        target = self._target
        if target.task is None:
            return
        if self._battle_is_running():
            return
        self._save_settings()
        if target.account_key:

            def merge(data):
                data.setdefault("accounts", {}).setdefault(target.account_key, {}).setdefault(
                    task_storage_name(target.task), {}
                )[BATTLE_CONFIG_MODE_KEY] = False
                return data

            update_overrides(merge)
        else:
            target.task.config[BATTLE_CONFIG_MODE_KEY] = False
        self._select_target(self.target_combo.currentIndex())

    def hideEvent(self, event):
        self._state_timer.stop()
        self._save_settings()
        super().hideEvent(event)
