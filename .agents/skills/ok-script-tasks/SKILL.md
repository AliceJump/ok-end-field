---
name: ok-script-tasks
description: Create, modify, register, or review ok-script BaseTask and TriggerTask classes. Use for task lifecycle, configuration, GUI metadata, project registration, or task behavior in any ok-script app.
---

# OK Script Tasks

Inspect the target project's existing tasks, base classes, helpers, and registration before adding a task. Use [task API](references/task-api.md) when lifecycle or framework behavior is uncertain, [templates](references/templates.md) for a new task, and [config UI](references/config-ui.md) only when conditional fields or numeric bounds are involved.

## Build the task

1. Choose a one-time `BaseTask` (or project base) for a workflow that finishes, or `TriggerTask` for repeated background checks. Call `super().__init__(*args, **kwargs)` before setting metadata.
2. Define only needed metadata: name, description, `default_config`, config help/types, grouping, scheduling, and supported locales. Keep persisted keys stable and follow project naming; use the translation system for display text.
3. Implement `run()` with observable steps and task helpers such as `wait_until`, `wait_ocr`, `wait_click_feature`, `sleep`, `click_relative`, and `log_info`. Prefer state-dependent waits over fixed polling or direct device calls.
4. Register by the project's convention (built-in config, `ok_tasks`, or imported package). In ok-end-field, daily subtasks are independent registered classes; `DailyTask` composes them through `src/tasks/daily/daily_feature.py` and keeps their parameters on the subtask classes.
5. Run focused tests or the available headless path. If device execution is unavailable, import and instantiate the class at minimum.

## Preserve task behavior and data

- Read persisted settings through `self.config.get(...)` after `after_init()`; declare defaults in `self.default_config`. A one-time task disables after normal completion.
- For `TriggerTask`, choose `_enabled` and `trigger_interval` deliberately. Return truthy only after meaningful work so the executor restarts trigger scanning; otherwise return falsey.
- Include matcher text for every active OCR locale in the target project. Use `supported_languages` only when a task truly cannot run in another locale.
- If changing a persisted key, value format, or owning task, load `$ok-config-migration` before changing defaults or running the app. For gettext catalogs, use `$ok-script-i18n`; for OCR matcher JSON, use `$ok-script-ocr-lang`.
