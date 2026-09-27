---
name: ok-config-migration
description: Migrate persisted ok-end-field task settings when a key, value format, or owning task changes. Use for task JSON and account-specific overrides so existing user values survive.
---

# OK Script Config Migration

`Config.verify_config` deletes keys absent from a task's `default_config` on load. Add the migration **with** the schema change, test old persisted data before launching the app, and keep the new key in the target defaults. Paths come from `config_folder`, not a hard-coded `configs/` directory.

## Same task file

- Rename: add `config_key_migrations = {"旧键": "新键"}` to the task or mixin. `BaseEfTask.load_config` collects MRO tables and copies values before framework loading. Old keys remain for rollback; an existing new value wins.
- Value format: add `config_value_migrations = {"新键": transform}`. Key copies run first; see `src/core/config_migration.py` for helpers and `_NO_MIGRATION`.
- These tables touch only `<TaskClass>.json`. If accounts can override the setting, migrate the matching entries in `account_scoped_overrides.json` through `src/tasks/account/account_scope_store.py:update_overrides`, preserving existing new values.

## Between task files

For the daily task split, append a **new batch ID** to `DAILY_SPLIT_IMPORTS` in `src/tasks/daily/split_config_migrator.py`. Map source task/key to target task/key or a converter; never edit a completed batch because its marker prevents reruns. `BaseEfTask.load_config` runs pending imports before any task's `verify_config`. The migrator backs up sources, copies account overrides, and retains old values. For another cross-task move, preserve the same pre-load ordering. Check older migration backups when a previous app version may already have removed source keys.

## Verify

Add a focused test for the actual change: old-only data, existing target value, missing data, rerun, conversion, and account overrides as applicable. Existing examples are `tests.TestZipLineConfig` and `tests.TestDailySplitConfigMigration`; they do not cover a new mapping automatically. Update gettext and docs only when visible names or options change (`$ok-script-i18n`). Recover from preserved old keys or `daily_split_migration_backup/<batch>/` before consulting logs that may contain private data.
