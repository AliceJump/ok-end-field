---
name: ok-script-i18n
description: Add, sync, repair, and compile gettext translations for ok-script Python task classes and task metadata. Use when Codex needs to translate or internationalize BaseTask and TriggerTask names, descriptions, default_config keys or values, config_description help text, config_type options, OCR-facing UI strings, or locale-specific ok.po catalogs in ok-script style projects.
---

# OK Script i18n

## Overview

Use this skill for gettext translation work in ok-script projects that keep catalogs under `i18n/<locale>/LC_MESSAGES/ok.po`. It complements `$ok-script-tasks`: create task behavior with the task skill, then use this skill to keep task UI strings translated and compiled.

## Workflow

1. Inspect the target task file and collect user-facing task metadata:
   - `self.name`
   - `self.description`
   - keys and string values in `self.default_config`
   - string values in `self.config_description`
   - dropdown, multi-selection, list, and button option strings in `self.config_type`
   - OCR text lists only when they are user-visible or intended to be localized
2. Discover locales by listing `i18n/*/LC_MESSAGES/ok.po`.
   Do not hard-code a fixed language list.
3. Check whether each source string already exists in every catalog.
4. Add missing `msgid` blocks to every locale.
   Preserve existing `msgstr` values unless the user asks to revise translations.
5. Translate missing entries into every locale present in the repo.
6. Compile every changed `ok.po` into `ok.mo`.
7. Verify catalog syntax and check for duplicate `msgid` entries.

## Helper Script

Use `scripts/task_i18n_helper.py` in this skill (`.agents/skills/ok-script-i18n/scripts/task_i18n_helper.py`) when helpful:

```powershell
.\.venv\Scripts\python.exe .agents\skills\ok-script-i18n\scripts\task_i18n_helper.py scan --task src\tasks\onetime\DailyTask.py
.\.venv\Scripts\python.exe .agents\skills\ok-script-i18n\scripts\task_i18n_helper.py check --i18n i18n
.\.venv\Scripts\python.exe .agents\skills\ok-script-i18n\scripts\task_i18n_helper.py compile --i18n i18n
```

The scanner is a helper, not a substitute for reading the task. It finds common literal strings but can miss values built through constants, imports, f-strings, comprehensions, or helper functions.

### Merging conflicting catalogs (git merge / stash pop)

When the same `ok.po` was modified on both sides (e.g. feature branch merged with `master` after a po sync commit like #252), the text `po` usually auto-merges but the binary `ok.mo` always conflicts. Resolve by merging the two po sides then recompiling — `scripts/merge_po.py` merges two po files by `msgid -> msgstr` dictionary, keeping entries unique to either side; when the same `msgid` exists on both sides with different translations, the **newer file (by mtime) wins** (override with `--prefer ours|theirs`). Recompile the merged po afterwards with `task_i18n_helper.py compile`.

```powershell
# 从合并状态取双方两侧：git show :2:path (ours), git show :3:path (theirs)
# 注意：必须显式 UTF-8 导出（PowerShell 5.1 的 > 重定向会写 UTF-16LE，polib 按 UTF-8 解码会失败）
git show :2:i18n/zh_CN/LC_MESSAGES/ok.po | Out-File -Encoding utf8 "$env:TEMP\ours.po"
git show :3:i18n/zh_CN/LC_MESSAGES/ok.po | Out-File -Encoding utf8 "$env:TEMP\theirs.po"
.\.venv\Scripts\python.exe .agents\skills\ok-script-i18n\scripts\merge_po.py "$env:TEMP\ours.po" "$env:TEMP\theirs.po" --output i18n\zh_CN\LC_MESSAGES\ok.po --prefer ours --compile
```

After merging all locales, run `task_i18n_helper.py check` to confirm no duplicate `msgid` remains.

## Catalog Rules

- Keep `msgid` exactly equal to the source string used by the code.
- Append new entries near the end if the catalog is not otherwise sorted.
- Do not add log-only strings unless the user explicitly asks.
- Focus on GUI-visible task metadata, config labels, config options, and help text.
- Empty `msgstr` is acceptable only when that locale intentionally falls back to the source language.
- Preserve translator comments, flags, previous `msgid` data, and existing entry order when possible.
- After editing `.po`, always compile `.mo`.

## Translation Guidance

- Prefer concise UI text over literal word-for-word translation.
- Keep placeholders, punctuation required by code, and hotkey names unchanged.
- Keep config keys stable when they are persisted in JSON. Translate the catalog entry for display, not the Python key, unless the project already stores localized keys.
- For Chinese locales, distinguish Simplified (`zh_CN`) and Traditional (`zh_TW`) when both catalogs exist.
- For option lists, translate each option string that appears in the UI.

## Integration With Task Work

When adding or modifying an ok-script task:

1. First use `$ok-script-tasks` to implement the task and identify user-facing strings.
2. Then use `$ok-script-i18n` to sync gettext catalogs for those strings.
3. Compile catalogs and include changed `.po` and `.mo` files in the final change summary.

## Lang JSON Convention (assets/lang/)

`assets/lang/` language JSONs are a separate, parallel system to gettext. They hold task OCR match text and tracked localized business-data modules; they are not the same as `i18n/` catalogs.

- **New keys use semantic names** (e.g. `inst_title`, `inst_delivery_targets`), not the legacy `k_<md5前8位>` hash style.
- Legacy `k_*` hash keys stay untouched — no migration needed; both styles coexist.
- Each key has 6 locale nodes (`zh_CN`/`zh_TW`/`en_US`/`ja_JP`/`ko_KR`/`es_ES`), formatted `{"string": "..."}`, `{"pattern": "..."}`, or `{"terms": [...]}` (node types: `string` literal, `pattern` regex, `terms` term list).
- Code reads via `self.lang.<模块名>.<语义化key>`, auto-selected by the current UI language (see `src/data/lang/`).
- **Tracked full-locale JSON belongs in `assets/lang/`**: if each top-level business key directly contains complete locale nodes, keep the tracked file at `assets/lang/<module>.json`, even when it primarily serves a plugin or data query. Keep canonical/structured business data in `assets/data/`. Local generated files ignored by `.gitignore` retain their existing paths and ignored status.
- **Task lang modules hold only OCR match text** (either `k_*` hash or semantic keys). Pure localized data modules such as `effect_names` and `yingtuo_stages` may serve plugins/data queries and do not need to be loaded by task OCR code. UI explanations (e.g. `instructions` rich text) do NOT go in lang JSON — use `self.tr("中文msgid")` through ok gettext: msgid into `i18n/*/LC_MESSAGES/ok.po` (msgid must match the code string verbatim, including full-width punctuation / `{placeholders}`), then `task_i18n_helper.py compile`.
- **Minimal principle**: emoji (`📍` `⚙️` `🖱️`), tree chars (`└─`/`├─`), HTML tags/colors that need no translation stay concatenated in code (e.g. `"📍 " + self.tr("滑索配置说明")`); only translatable plain text goes into i18n data (msgid/msgstr exclude emoji and decoration).
- **Dynamic key-name translation**: config key names read dynamically in `instructions` (delivery points / target names / deposit-point names) must also pass through `self.tr(键名)` for display; msgid goes into ok.po (zh_TW in Traditional; other locales may keep Simplified for cross-referencing the config JSON key name). Look up config values with the raw key, display with the translated key (see `src/tasks/mixin/zip_line_mixin.py`).
