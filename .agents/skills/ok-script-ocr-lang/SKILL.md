---
name: ok-script-ocr-lang
description: Maintain ok-end-field OCR matcher language JSON and OCR text-fix mappings. Use for assets/lang modules, active OCR locales, matcher references, or assets/ocr_fix/ocr_text_fix.json; GUI gettext belongs to ok-script-i18n.
---

# OK Script OCR Language Resources

`assets/lang/<module>.json` supplies task OCR matchers and localized business data; `assets/ocr_fix/ocr_text_fix.json` extends matching for known OCR confusion. GUI text belongs to gettext (`$ok-script-i18n`). See `docs/dev/i18n_OCR配置流程.md` for the full project convention.

## Matcher schema and locales

- Runtime reads one file per module, **not** `assets/lang/<module>/<locale>.json`. A top-level business key contains locale nodes; each node has exactly one of `{"string": "..."}`, `{"pattern": "..."}`, or `{"terms": [...]}`. The accessor returns a string, compiled regex, or list respectively. Do not mix types in one node.
- `src/data/lang/__init__.py` defines `ACTIVE_LOCALES_CONFIG`. Currently `zh_CN` and `zh_TW` are active for task OCR. Unknown or inactive locales normalize to `zh_CN`; a missing locale node falls back to `zh_TW` for Traditional Chinese, otherwise `zh_CN`, then the first available node.
- Missing files or keys yield an empty module or `None`. First check exact module/key spelling and locale before changing matcher code.

## Add or change a key

1. Edit `assets/lang/<module>.json` with a semantic key (legacy `k_*` hash keys can remain). Supply both active OCR locales. Reference it as `self.lang.<module>.<key>`.
2. Run `uv run --locked python -m unittest tests.TestCheckLang -v`. It checks code references, node shape, nonempty values, and regex syntax for active locales. Review the actual OCR region in a game window when behavior depends on visual matching.
3. Regenerate `src/data/lang/_lang_typed.py` with `uv run --locked python scripts/i18n/gen_lang_stubs.py` and inspect its diff.

Tracked full-locale business text belongs in `assets/lang/`; structured canonical data belongs in `assets/data/`. Preserve existing locale nodes, including extra locales. When adding a data-only module, update `DATA_ONLY_MODULES` in the stub generator; task OCR modules stay in the generated accessor hints.

## OCR confusion map

`ocr_text_fix.json` maps complete misread text to correct text. The patch uses only **same-length** pairs to derive character substitutions and extends the caller's `match` after the framework's regex fix. It does not normalize OCR output or change `Box.name`. Use language patterns or business parsing for length-changing and word-order errors. The old `src/data/ocr_normalize_map.py` is gone.

For a mismatch, confirm the runtime locale, key/node type, regex, search region, and generated hint before adding a confusion pair.
