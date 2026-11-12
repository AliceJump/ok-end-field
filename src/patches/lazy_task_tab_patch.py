"""Keep unvisited task pages out of the initial widget construction pass."""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from dataclasses import dataclass
from functools import wraps

from PySide6.QtCore import QTimer

from src.core.param_preview_model import _button_config

logger = logging.getLogger(__name__)


class _BaseConfigView(MutableMapping):
    """Keep UI reads on base values without changing a worker's account getter."""

    def __init__(self, config):
        self._config = config

    def get(self, key, default=None):
        raw_get = getattr(self._config, "_raw_get", None)
        return (raw_get if callable(raw_get) else self._config.get)(key, default)

    def __getitem__(self, key):
        return self._config[key]

    def __setitem__(self, key, value):
        self._config[key] = value

    def __delitem__(self, key):
        del self._config[key]

    def __iter__(self):
        return iter(self._config)

    def __len__(self):
        return len(self._config)

    def __getattr__(self, name):
        return getattr(self._config, name)


def _use_base_config_for_task_cards(config_card_class, task_card_class):
    original_init = config_card_class.__init__
    if getattr(original_init, "_okef_base_task_config", False):
        return

    @wraps(original_init)
    def init(self, task, name, config, description, default_config, config_description, config_type, config_icon):
        if isinstance(self, task_card_class) and config is not None:
            config = _BaseConfigView(config)
        original_init(
            self, task, name, config, description, default_config, config_description, config_type, config_icon
        )

    init._okef_base_task_config = True
    config_card_class.__init__ = init


def _normalize_rendered_lists(config, default_config, config_types, resolve_type):
    """Retain ModifyListItem's pre-runtime cleanup without constructing widgets.

    The traversal matches ConfigContentMixin in pinned ok-script 2.0.22: all
    reachable sub-configs are constructed, including currently inactive ones.
    The preview model uses different visibility rules, so only its identical
    button predicate is shared here. Framework differential tests guard this.
    """
    if not config or not (config.has_user_config() or default_config or config_types):
        return
    if not isinstance(config, _BaseConfigView):
        config = _BaseConfigView(config)
    types = config_types or {}
    children = {}
    for key, metadata in types.items():
        rules = metadata.get("sub_configs") if isinstance(metadata, dict) else None
        if isinstance(rules, dict):
            children[key] = list(
                dict.fromkeys(
                    child for keys in rules.values() for child in ([keys] if isinstance(keys, str) else (keys or []))
                )
            )
    controlled = {key for keys in children.values() for key in keys}
    added = set()

    def add(key):
        if key in added:
            return
        added.add(key)
        default = config.get_default(key)
        metadata = types.get(key)
        if isinstance(default, list) and isinstance(metadata, dict):
            options = metadata.get("options_available")
            if options is not None and resolve_type(metadata, default) in (None, "drop_down"):
                items = config.get(key)
                valid_items = [item for item in items if item in options]
                if valid_items != items:
                    config[key] = valid_items
        for child in children.get(key, ()):
            if not child.startswith("_") and (config.get(child) is not None or _button_config(types.get(child))):
                add(child)

    for key in config:
        metadata = types.get(key)
        hidden = isinstance(metadata, dict) and metadata.get("hidden", False)
        if not key.startswith("_") and not hidden and key not in controlled:
            add(key)
    for key, metadata in types.items():
        if (
            not key.startswith("_")
            and key not in controlled
            and _button_config(metadata)
            and not metadata.get("hidden", False)
        ):
            add(key)


@dataclass
class _RefreshState:
    dirty: bool = False
    queued: bool = False
    building: bool = False
    built: bool = False
    disposed: bool = False


def _state_for(page):
    state = getattr(page, "_okef_task_refresh", None)
    if state is None:
        state = _RefreshState()
        page._okef_task_refresh = state

        def destroyed(*_args):
            state.disposed = True

        page.destroyed.connect(destroyed)
    return state


def _defer_first_refresh(tab_class, collect_tasks, prepare_tasks=None):
    """Wrap the final renderer, preserving its behavior after the first build."""
    original_refresh = tab_class.refresh_ui
    if getattr(original_refresh, "_okef_lazy_task_page", False):
        return
    original_show = tab_class.showEvent
    original_dispose = getattr(tab_class, "dispose", None)

    def queue_refresh(page, state):
        if (
            state.disposed
            or state.queued
            or state.building
            or not state.dirty
            or (not state.built and not page.isVisible())
        ):
            return
        state.queued = True
        # The QObject context cancels the callback if the page is destroyed.
        QTimer.singleShot(0, page, lambda: flush_refresh(page, state))

    def refresh_now(page, state):
        state.dirty = False
        state.building = True
        try:
            original_refresh(page)
        except Exception:
            state.dirty = True
            raise
        else:
            state.built = True
        finally:
            state.building = False
        # A task-list update during rendering needs one more pass, not recursion.
        if state.dirty:
            queue_refresh(page, state)

    def flush_refresh(page, state):
        state.queued = False
        if state.disposed or state.building or not state.dirty or (not state.built and not page.isVisible()):
            return
        try:
            refresh_now(page, state)
        except Exception:
            # Retain dirty state for the next explicit refresh/show. Retrying
            # automatically here could keep a broken page in a busy loop.
            logger.exception("Deferred task page construction failed")

    @wraps(original_refresh)
    def refresh_ui(self):
        state = _state_for(self)
        if state.disposed:
            return
        state.dirty = True
        # TaskTab's runtime information routing uses this membership even when
        # no cards exist yet. Read the current task objects, never a saved copy.
        self.tasks = collect_tasks(self)
        if state.building:
            return
        if state.built:
            # Keep existing card disposal and borrowed drawer views on their
            # original synchronous refresh path, including while hidden.
            refresh_now(self, state)
        else:
            if prepare_tasks is not None:
                prepare_tasks(self)
            queue_refresh(self, state)

    @wraps(original_show)
    def show_event(self, event):
        original_show(self, event)
        queue_refresh(self, _state_for(self))

    refresh_ui._okef_lazy_task_page = True
    tab_class.refresh_ui = refresh_ui
    tab_class.showEvent = show_event

    if original_dispose is not None:

        @wraps(original_dispose)
        def dispose(self):
            state = _state_for(self)
            if state.disposed:
                return
            state.disposed = True
            original_dispose(self)

        tab_class.dispose = dispose


def install_lazy_task_tab_patch():
    """Install after the theme has selected its task and daily-matrix renderers."""
    from ok import og
    from ok.core.config_schema import resolve_config_type
    from ok.ui.qt.tasks.ConfigCard import ConfigCard, ConfigContentMixin
    from ok.ui.qt.tasks.OneTimeTaskTab import OneTimeTaskTab
    from ok.ui.qt.tasks.TaskCard import TaskCard
    from ok.ui.qt.tasks.TriggerTaskTab import TriggerTaskTab

    from src.gui.components.EndfieldSectionMatrix import MATRIX_GROUPS
    from src.gui.EndfieldConfigContent import visible_config_types

    def prepare_tasks(page):
        # Matrix rows already open their config form only when requested. They
        # must not gain native-card cleanup as a side effect of this patch.
        if getattr(page, "_endfield_collect_tasks", None) is not None and page.group_name in MATRIX_GROUPS:
            return
        for task in page.tasks:
            config = _BaseConfigView(task.config) if task.config is not None else None
            types = task.config_type
            if getattr(ConfigContentMixin._init_config_content, "_endfield_patched", False):
                types = visible_config_types(task, config, types)
            _normalize_rendered_lists(config, task.default_config, types, resolve_config_type)

    def onetime_tasks(page):
        collect = getattr(page, "_endfield_collect_tasks", None)
        if collect is not None:
            return collect()
        return [
            task
            for task in og.executor.onetime_tasks
            if getattr(task, "visible", True)
            and (
                (page.is_standalone and not getattr(task, "group_name", None))
                or (page.group_name and getattr(task, "group_name", None) == page.group_name)
            )
        ]

    def trigger_tasks(_page):
        return [task for task in og.executor.trigger_tasks if getattr(task, "visible", True)]

    _use_base_config_for_task_cards(ConfigCard, TaskCard)
    _defer_first_refresh(OneTimeTaskTab, onetime_tasks, prepare_tasks)
    _defer_first_refresh(TriggerTaskTab, trigger_tasks, prepare_tasks)
