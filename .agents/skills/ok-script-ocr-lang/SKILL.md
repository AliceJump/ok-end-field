---
name: ok-script-ocr-lang
description: Add and maintain OCR language resources and OCR text-fix patches for ok-end-field (and similar ok-script apps that use the unified assets/lang/*.json schema). Use when adding new OCR match text, editing assets/lang/<module>.json, reasoning about the active locale system, adding entries to assets/ocr_fix/ocr_text_fix.json, or troubleshooting why an OCR language node is not taking effect. Complements ok-script-i18n (gettext GUI catalogs) — this skill is for the task OCR matcher side.
---

# OK Script OCR Language Resources

## Purpose

Manage the two OCR-facing language systems in ok-end-field. These are separate from `i18n/*/LC_MESSAGES/ok.po` (gettext GUI translation):

- `assets/lang/<module>.json` — task OCR matchers and localized business text.
- `assets/ocr_fix/ocr_text_fix.json` — OCR confusion patch (extends match params).

Source doc: `docs/dev/i18n_OCR配置流程.md`.

## 1. Two systems, one schema

- Runtime path is a single file `assets/lang/<module>.json` (NO `assets/lang/<module>/<locale>.json` directories at runtime).
- Top-level key = business key; second level = locale node.
- Each locale node uses exactly ONE of:
  | Node | Access result | Use |
  |------|---------------|-----|
  | `{"string": "确认"}` | `str` | fixed / display text |
  | `{"pattern": "^\\d+$"}` | compiled `re.Pattern` | regex OCR match |
  | `{"terms": ["A","B"]}` | `list` | multiple candidates |

  Attribute resolution order: `string` → `pattern` → `terms` (direct attribute). `build_matcher` order: `pattern` → `string` → `terms`. Do NOT mix fields in one node.

- Value fallback order for a key:
  1. current normalized locale;
  2. `zh_TW` if current is `zh_TW`, else `zh_CN`;
  3. first available locale under the key.
- Missing module file / failed JSON / missing key → empty module / `None`.

## 2. Active locale

- `src/data/lang/__init__.py` `ACTIVE_LOCALES_CONFIG` controls enabled OCR locales. Currently only `zh_CN`, `zh_TW` are active (others exist in files but are NOT task OCR locales).
- Normalization: executor.locale else self.locale; supports Enum / `.name` objects / strings; `-`→`_`; case-insensitive match against active locales; unknown/inactive/empty → `zh_CN`.

## 3. Adding new OCR text

1. Determine module name (usually matches the task class or Python module, e.g. `DeliveryTask`, `login_mixin`).
2. Edit `assets/lang/<module>.json`, add a top-level key.
3. Add the node at least for active locales `zh_CN` and `zh_TW`.
4. Use only one of `string` / `pattern` / `terms` per node.
5. Reference in code: `self.lang.<module>.<key>`.
6. Run the language reference test + verify the OCR region on a real window.

```powershell
uv run --locked python -m unittest tests.TestCheckLang -v
```

`TestCheckLang` parses Python source with `ast` and covers both legacy `k_*` and semantic `self.lang.<module>.<key>` references. It fails when a referenced module/key is missing from either active locale. It also validates every lang node: exactly one of `string`/`pattern`/`terms`, non-empty values, and compilable regular expressions.

After adding or renaming lang keys, regenerate the checked-in type hints, review the generated diff, and include the expected output:

```powershell
uv run --locked python scripts/i18n/gen_lang_stubs.py
git diff -- src/data/lang/_lang_typed.py
```

### Naming and file-placement conventions

- **New keys use semantic names** (e.g. `inst_title`, `inst_delivery_targets`), not the legacy `k_<md5前8位>` hash style. Legacy `k_*` hash keys stay untouched — no migration needed; both styles coexist.
- Tracked full-locale business-data files keep the six core locales (`zh_CN`/`zh_TW`/`en_US`/`ja_JP`/`ko_KR`/`es_ES`); existing modules may additionally carry other locales and those nodes must be preserved.
- **Tracked full-locale JSON belongs in `assets/lang/`**: if each top-level business key directly contains complete locale nodes, keep the tracked file at `assets/lang/<module>.json`, even when it primarily serves a plugin or data query. Keep canonical/structured business data in `assets/data/`. Local generated files ignored by `.gitignore` retain their existing paths and ignored status.
- **Task lang modules hold only OCR match text** (either `k_*` hash or semantic keys). Pure localized data modules such as `effect_names` and `yingtuo_stages` may serve plugins/data queries and do not need to be loaded by task OCR code.
- Keep `scripts/i18n/gen_lang_stubs.py` `DATA_ONLY_MODULES` synchronized when adding another plugin/data-only module; runtime OCR modules may be accessed dynamically and must remain in the generated `self.lang` type hints even when no direct attribute reference is found.
- gettext-side rules for UI text (`self.tr` usage, emoji minimal principle, dynamic key-name translation) live in the `ok-script-i18n` skill; UI explanations do not go in lang JSON.

## 4. OCR confusion patch (ocr_text_fix.json)

- File: `assets/ocr_fix/ocr_text_fix.json`, schema is full-text `OCR 错误文本 -> 正确文本` pairs.
- Behavior: does NOT replace OCR output and does NOT write `TaskExecutor.text_fix`. It:
  1. reads only same-length error/correct pairs;
  2. builds per-char map `正确字符 -> OCR 错误字符` (e.g. `幹 -> 乾`);
  3. keeps the first mapping on conflicts (skips conflicting duplicates);
  4. extends the caller's `match` AFTER the framework's `OCR.fix_match_regex`.
- Per match type: `str` → up to 4 variants (list when multiple); `re.Pattern` → only safe literal chars extended, keep flags; `list` → recursive extend + flatten; other → returned as-is.
- Regex meta-characters in confusion chars are skipped; failures return the original match.
- This is match-compatibility only, NOT OCR-result normalization — business code reading `box.name` may still see the raw misrecognized text.
- Legacy `src/data/ocr_normalize_map.py` no longer exists. Normalization (if needed) must live in business parsing.

## 5. GUI gettext (separate)

GUI text uses `i18n/<locale>/LC_MESSAGES/ok.po`. Verification:
```powershell
uv run --locked python -m unittest tests.TestGuiI18n -v
uv run --locked python -m unittest tests.TestPoLocaleConsistency -v
```
`TestPoLocaleConsistency` checks catalog duplicates/empty translations, placeholder consistency, and known runtime pollution of msgid. It does NOT decide OCR `SUPPORTED_LOCALES`.

## 6. Tool status (avoid)

- Legacy batch-translate / migrate tools for the OLD `assets/lang/<module>/<locale>.json` schema were deleted with the schema switch. Do not try to run them.
- Reliable path: hand-edit the unified JSON, validate references with `TestCheckLang`, manually review regexes and game-specific terms.

## 7. Troubleshooting checklist

Locale node not taking effect — check in order:
1. File is `assets/lang/<module>.json`.
2. Top-level key exactly matches the code attribute.
3. Runtime locale is enabled in `ACTIVE_LOCALES_CONFIG`.
4. Locale node contains exactly one valid type field.
5. Regex string is a valid Python regex.
6. Regenerated `src/data/lang/_lang_typed.py` includes the module/key and every diff is explained by the source lang change or generator update.

Stable OCR misrecognition: first confirm it's purely a match problem. Only same-length char confusion belongs in `ocr_text_fix.json`; length-changing / word-order / business-only corrections belong in the language pattern or business parsing.
