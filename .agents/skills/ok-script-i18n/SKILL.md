---
name: ok-script-i18n
description: Add, sync, repair, and compile gettext translations for ok-script Python task classes and task metadata. Use when translating BaseTask and TriggerTask names, descriptions, default_config keys or values, config_description help text, config_type options, instructions, runtime messages, locale-specific ok.po catalogs, lang JSON vs gettext placement, or debugging i18n collection-pool pollution.
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

Use `scripts/task_i18n_helper.py` in this skill (`.agents/skills/ok-script-i18n/scripts/task_i18n_helper.py`) through the locked repository environment:

```powershell
uv run --locked python .agents/skills/ok-script-i18n/scripts/task_i18n_helper.py scan --task src/tasks/onetime/DailyTask.py
uv run --locked python .agents/skills/ok-script-i18n/scripts/task_i18n_helper.py check --i18n i18n
uv run --locked python .agents/skills/ok-script-i18n/scripts/task_i18n_helper.py compile --i18n i18n
```

The scanner covers literal assignments and `.update(...)` calls for common task metadata, including literal `self.config_type["key"] = {...}` assignments. It remains a helper, not a substitute for reading the task: values built through constants, imports, f-strings, comprehensions, helper functions, or `task`/`self._task` aliases still require manual review.

`check` verifies duplicate context/msgid/plural keys, empty translations, placeholder parity, and key-set consistency across locales. Run it before `compile`; for this repository also run `tests.TestPoLocaleConsistency` because project-specific pollution and translation-quality rules live there.

### Merging conflicting catalogs (git merge / stash pop)

When the same `ok.po` was modified on both sides, the text `po` may merge but the binary `ok.mo` conflicts. Resolve by merging the two po sides then recompiling. `scripts/merge_po.py` keys entries by `(msgctxt, msgid, msgid_plural)`, preserves full entry metadata, rejects duplicate keys, and keeps entries unique to either side. For real git conflicts always pass `--prefer ours` or `--prefer theirs`; files exported from git have artificial mtimes, so the default `newer` policy is only suitable when mtimes are meaningful.

```powershell
# 从合并状态取双方两侧：git show :2:path (ours), git show :3:path (theirs)
# 注意：必须显式 UTF-8 导出（PowerShell 5.1 的 > 重定向会写 UTF-16LE，polib 按 UTF-8 解码会失败）
git show :2:i18n/zh_CN/LC_MESSAGES/ok.po | Out-File -Encoding utf8 "$env:TEMP\ours.po"
git show :3:i18n/zh_CN/LC_MESSAGES/ok.po | Out-File -Encoding utf8 "$env:TEMP\theirs.po"
uv run --locked python .agents/skills/ok-script-i18n/scripts/merge_po.py "$env:TEMP\ours.po" "$env:TEMP\theirs.po" --output i18n/zh_CN/LC_MESSAGES/ok.po --prefer ours --compile
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

## Lang JSON Boundary (assets/lang/)

`assets/lang/` language JSONs are a separate, parallel system to gettext: they hold task OCR match text and tracked localized business-data modules, not `i18n/` catalogs. Everything about those files — schema, active OCR locales, key naming, file placement/archiving, `ocr_text_fix.json`, and verification — is owned by the `ok-script-ocr-lang` skill; read it before touching `assets/lang/` or `src/data/lang/`.

gettext-side rules that stay here:

- UI explanations (e.g. `instructions` rich text) do NOT go in lang JSON — use `self.tr("中文msgid")` through ok gettext: msgid into `i18n/*/LC_MESSAGES/ok.po` (msgid must match the code string verbatim, including full-width punctuation / `{placeholders}`), then `task_i18n_helper.py compile`.
- **Minimal principle**: emoji (`📍` `⚙️` `🖱️`), tree chars (`└─`/`├─`), HTML tags/colors that need no translation stay concatenated in code (e.g. `"📍 " + self.tr("滑索配置说明")`); only translatable plain text goes into i18n data (msgid/msgstr exclude emoji and decoration).
- **Dynamic key-name translation**: config key names read dynamically in `instructions` (delivery points / target names / deposit-point names) must also pass through `self.tr(键名)` for display; msgid goes into ok.po (zh_TW in Traditional; other locales may keep Simplified for cross-referencing the config JSON key name). Look up config values with the raw key, display with the translated key (see `src/tasks/mixin/zip_line_mixin.py`).

## Collection-Pool Safety

Debug mode records every `App.tr(key)` input in `to_translate`; rendering runtime data through `tr()` pollutes generated PO catalogs.

- Translation inputs must be stable templates: `self.tr("固定文本")` or `self.tr("模板 {value}").format(...)`. Never use `tr(f"...{runtime_value}...")` or concatenate a runtime value before calling `tr()`.
- Convert `re.Pattern` to `.pattern` before formatting it into logs; otherwise `re.compile('...')` becomes a collected runtime string.
- User-input dropdown values are not translatable. Register their config key in `src/core/dynamic_config_keys.py` so `dynamic_config_patch.py` bypasses `tr()`. Do not register project-resource dropdowns such as character or item lists.
- `log_info` and `mark_task_failure` do not format parameters. For messages with changing values, translate the stable outer template before `.format(...)`; translate only known textual enum/config labels, not OCR output, account names, or other unknown runtime text.
- Pure static log literals should remain `log_info("固定文本")`; the rendering layer translates them. Wrapping the same static literal at the call site is redundant.
- If catalogs are polluted, fix the producer first, remove the bad entries from every locale, run `task_i18n_helper.py check`, compile, and run `tests.TestPoLocaleConsistency`.
