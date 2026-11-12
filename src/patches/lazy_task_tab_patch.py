"""Keep unvisited task pages out of the initial widget construction pass."""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from dataclasses import dataclass
from functools import partial, wraps

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


def _sub_config_children(config_types):
    children = {}
    for key, metadata in config_types.items():
        rules = metadata.get("sub_configs") if isinstance(metadata, dict) else None
        if isinstance(rules, dict):
            children[key] = list(
                dict.fromkeys(
                    child for keys in rules.values() for child in ([keys] if isinstance(keys, str) else (keys or []))
                )
            )
    return children


def _rendered_config_roots(config, config_types, controlled):
    for key in config:
        metadata = config_types.get(key)
        hidden = isinstance(metadata, dict) and metadata.get("hidden", False)
        if not key.startswith("_") and not hidden and key not in controlled:
            yield key
    for key, metadata in config_types.items():
        if (
            not key.startswith("_")
            and key not in controlled
            and _button_config(metadata)
            and not metadata.get("hidden", False)
        ):
            yield key


def _normalize_list_options(config, key, config_types, resolve_type):
    default = config.get_default(key)
    metadata = config_types.get(key)
    if not isinstance(default, list) or not isinstance(metadata, dict):
        return
    options = metadata.get("options_available")
    if options is None or resolve_type(metadata, default) not in (None, "drop_down"):
        return
    items = config.get(key)
    valid_items = [item for item in items if item in options]
    if valid_items != items:
        config[key] = valid_items


def _normalize_config_branch(key, config, config_types, children, added, resolve_type):
    if key in added:
        return
    added.add(key)
    _normalize_list_options(config, key, config_types, resolve_type)
    for child in children.get(key, ()):
        if not child.startswith("_") and (config.get(child) is not None or _button_config(config_types.get(child))):
            _normalize_config_branch(child, config, config_types, children, added, resolve_type)


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
    children = _sub_config_children(types)
    controlled = {key for keys in children.values() for key in keys}
    added = set()
    for key in _rendered_config_roots(config, types, controlled):
        _normalize_config_branch(key, config, types, children, added, resolve_type)


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


def _queue_refresh(page, state, original_refresh):
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
    QTimer.singleShot(0, page, lambda: _flush_refresh(page, state, original_refresh))


def _refresh_now(page, state, original_refresh):
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
        _queue_refresh(page, state, original_refresh)


def _flush_refresh(page, state, original_refresh):
    state.queued = False
    if state.disposed or state.building or not state.dirty or (not state.built and not page.isVisible()):
        return
    try:
        _refresh_now(page, state, original_refresh)
    except Exception:
        # Retain dirty state for the next explicit refresh/show. Retrying
        # automatically here could keep a broken page in a busy loop.
        logger.exception("Deferred task page construction failed")


def _wrap_refresh(original_refresh, collect_tasks, prepare_tasks):
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
            _refresh_now(self, state, original_refresh)
            return
        if prepare_tasks is not None:
            prepare_tasks(self)
        _queue_refresh(self, state, original_refresh)

    refresh_ui._okef_lazy_task_page = True
    return refresh_ui


def _wrap_show_event(original_show, original_refresh):
    @wraps(original_show)
    def show_event(self, event):
        original_show(self, event)
        _queue_refresh(self, _state_for(self), original_refresh)

    return show_event


def _wrap_dispose(original_dispose):
    @wraps(original_dispose)
    def dispose(self):
        state = _state_for(self)
        if state.disposed:
            return
        state.disposed = True
        original_dispose(self)

    return dispose


def _defer_first_refresh(tab_class, collect_tasks, prepare_tasks=None):
    """Wrap the final renderer, preserving its behavior after the first build."""
    original_refresh = tab_class.refresh_ui
    if getattr(original_refresh, "_okef_lazy_task_page", False):
        return
    original_show = tab_class.showEvent
    original_dispose = getattr(tab_class, "dispose", None)
    tab_class.refresh_ui = _wrap_refresh(original_refresh, collect_tasks, prepare_tasks)
    tab_class.showEvent = _wrap_show_event(original_show, original_refresh)
    if original_dispose is not None:
        tab_class.dispose = _wrap_dispose(original_dispose)


def _prepare_task_configs(page, *, config_content_class, matrix_groups, visible_types, resolve_type):
    # Matrix rows already open their config form only when requested. They
    # must not gain native-card cleanup as a side effect of this patch.
    if getattr(page, "_endfield_collect_tasks", None) is not None and page.group_name in matrix_groups:
        return
    for task in page.tasks:
        config = _BaseConfigView(task.config) if task.config is not None else None
        types = task.config_type
        if getattr(config_content_class._init_config_content, "_endfield_patched", False):
            types = visible_types(task, config, types)
        _normalize_rendered_lists(config, task.default_config, types, resolve_type)


def _collect_onetime_tasks(page, *, task_context):
    collect = getattr(page, "_endfield_collect_tasks", None)
    if collect is not None:
        return collect()
    return [
        task
        for task in task_context.executor.onetime_tasks
        if getattr(task, "visible", True)
        and (
            (page.is_standalone and not getattr(task, "group_name", None))
            or (page.group_name and getattr(task, "group_name", None) == page.group_name)
        )
    ]


def _collect_trigger_tasks(_page, *, task_context):
    return [task for task in task_context.executor.trigger_tasks if getattr(task, "visible", True)]


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

    prepare_tasks = partial(
        _prepare_task_configs,
        config_content_class=ConfigContentMixin,
        matrix_groups=MATRIX_GROUPS,
        visible_types=visible_config_types,
        resolve_type=resolve_config_type,
    )
    _use_base_config_for_task_cards(ConfigCard, TaskCard)
    _defer_first_refresh(OneTimeTaskTab, partial(_collect_onetime_tasks, task_context=og), prepare_tasks)
    _defer_first_refresh(TriggerTaskTab, partial(_collect_trigger_tasks, task_context=og), prepare_tasks)
