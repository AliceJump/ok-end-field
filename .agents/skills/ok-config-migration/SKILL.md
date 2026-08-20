---
name: ok-config-migration
description: Rename config keys in ok-script tasks without losing user data. Use when modifying default_config key names, key-name constants, or key-generation functions in a task class — the operation must follow a strict order (migration table → migrate test → i18n sync → docs sync → recovery fallback) to avoid losing user configuration stored in configs/*.json. Based on the DeliveryTask config_key_migrations pattern.
---

# OK Script Config Key Migration

## Purpose

Rename config keys in ok-script task classes safely. `configs/` JSON holds user runtime data; changing key names out of order silently orphans user values. Always follow the order below, in a single commit for the code pair.

## Workflow (strict order)

1. **先加迁移表，再改键名** — in the same task class:
   - Add `config_key_migrations = {旧键: 新键}` FIRST
   - Then modify `default_config` / key-name constants / key-generation functions
   - Both must land in the **same commit**. Never deploy in steps.
2. **迁移表生效前禁止运行程序** — do NOT launch the app to verify after renaming. Run the actual migration test first:
   ```powershell
   uv run python -m unittest tests.TestZipLineConfig -v
   ```
   The test asserts the old key value was migrated to the new key (the function under test is `migrate_config_file_keys(<任务名>, migrations)` in `src/tasks/onetime/DeliveryTask.py`).
3. **同步 i18n** — after key changes, sync all `i18n/*/LC_MESSAGES/ok.po` msgid (msgid must match the code key name exactly), then compile:
   ```powershell
   uv run python scripts/task_i18n_helper.py compile
   ```
4. **同步文档** — search `docs/` for the old key names and update them (e.g. the「通向送货点」key family).
5. **配置丢失可恢复** — if a user config was already lost:
   - `logs/ok-script.log` logs `Config:init self.config = {...}` (DEBUG level) with the full historical config per run.
   - Recover the user value from the last log line that still contains the old key name.
   - Confirm the restore on the next run's log.

## Reference

- Pattern source: `src/tasks/onetime/DeliveryTask.py` (`config_key_migrations`).
- Migration helper: `migrate_config_file_keys(<任务名>, migrations)`.
