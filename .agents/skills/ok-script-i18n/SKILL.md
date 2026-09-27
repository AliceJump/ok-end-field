---
name: ok-script-i18n
description: Maintain gettext translations for ok-script task UI and runtime messages. Use for task metadata, config labels and options, instructions, PO/MO catalogs, or translation-pool pollution; OCR matcher JSON belongs to ok-script-ocr-lang.
---

# OK Script i18n

## Sync task strings

1. Inspect every changed task class for visible names, descriptions, config keys/help/options, instructions, and runtime messages. In ok-end-field, `DailyTask` composes independent subtasks; scanning it alone misses strings in those classes.
2. Discover locales from `i18n/*/LC_MESSAGES/ok.po`. Keep `msgid` identical to the source text, add missing entries in **every** locale, and preserve existing translations and entry metadata.
3. Run the helper in the locked environment. Its scanner catches literal metadata assignments and `.update(...)` calls, but constants, imports, f-strings, comprehensions, and helper-built values still need manual review:

   `uv run --locked python .agents/skills/ok-script-i18n/scripts/task_i18n_helper.py scan --task src/tasks/onetime/DailyDeliveryTask.py`

4. Check catalogs, compile changed PO files to MO, and run the repository's pollution/consistency test:

   ```powershell
   uv run --locked python .agents/skills/ok-script-i18n/scripts/task_i18n_helper.py check --i18n i18n
   uv run --locked python .agents/skills/ok-script-i18n/scripts/task_i18n_helper.py compile --i18n i18n
   uv run --locked python -m unittest tests.TestPoLocaleConsistency -v
   ```

Translate concise UI text, preserving placeholders and code-significant punctuation. An empty `msgstr` is appropriate only for an intentional source-language fallback. Do not add log-only strings unless requested.

## Keep translation inputs stable

- Call `self.tr("固定文本")` or `self.tr("模板 {value}").format(...)`. Never pass formatted runtime data, OCR output, account names, or user input into `tr()`; debug collection would add them as bogus msgids.
- Static log literals can stay in `log_info("固定文本")` because the rendering layer translates them. For dynamic logs, translate the stable outer template before formatting; `log_info` and `mark_task_failure` do not format arguments.
- Dynamic user-input dropdown values bypass translation through `src/core/dynamic_config_keys.py`. Do not register project-resource option lists there. Use `re.Pattern.pattern` when formatting regexes into messages.
- If catalogs are polluted, fix the producer, remove bad entries from every locale, then rerun check, compile, and `TestPoLocaleConsistency`.

## Keep gettext separate from OCR language JSON

`assets/lang/` and `src/data/lang/` belong to `$ok-script-ocr-lang`; read that skill before editing them. GUI explanations use gettext, not lang JSON. Keep decoration such as emoji and HTML outside msgids when it needs no translation. Persisted config keys remain raw for lookup, but pass a stable key through `self.tr(key)` when displaying it; changing a stored key requires `$ok-config-migration`.

For PO merge conflicts, read [references/merge-po.md](references/merge-po.md). On Windows, programmatic PO writes may convert LF to CRLF; keep touched catalogs in the repository's LF format before compiling.
