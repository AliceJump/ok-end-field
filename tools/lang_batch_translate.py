from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List
from collections import defaultdict
from deep_translator import GoogleTranslator


# =============================
# CONFIG
# =============================
SOURCE_LOCALE = "zh_CN"

TARGET_LOCALES = (
    "en_US",
    "es_ES",
    "ja_JP",
    "ko_KR",
    "zh_TW",
)

LANG_MAP = {
    "en_US": "en",
    "es_ES": "es",
    "ja_JP": "ja",
    "ko_KR": "ko",
    "zh_TW": "zh-TW",
}


# =============================
# LOG
# =============================
def log(msg: str):
    print(f"[i18n] {msg}")


# =============================
# IO
# =============================
def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_json(path: Path, data: dict):
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


# =============================
# FLATTEN / UNFLATTEN (针对你的结构优化)
# =============================
def flatten(node, prefix=""):
    out = {}

    if isinstance(node, dict):
        for k, v in node.items():
            p = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                out.update(flatten(v, p))
            elif isinstance(v, str):
                out[p] = v
            else:
                out[p] = str(v)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            p = f"{prefix}[{i}]"
            out.update(flatten(v, p))

    return out


def unflatten(flat: Dict[str, str]):
    root = {}
    for path, value in flat.items():
        parts = path.split('.')
        cur = root
        for i, p in enumerate(parts[:-1]):
            if p not in cur:
                cur[p] = {}
            cur = cur[p]
        cur[parts[-1]] = value
    return root


# =============================
# TRANSLATOR (分块 + 更稳定)
# =============================
TRANSLATOR_CACHE = {}


def translate_batch(locale: str, texts: List[str], batch_size: int = 40) -> Dict[str, str]:
    if locale == SOURCE_LOCALE:
        return {t: t for t in texts}

    if locale not in TRANSLATOR_CACHE:
        TRANSLATOR_CACHE[locale] = GoogleTranslator(
            source="zh-CN",
            target=LANG_MAP[locale]
        )

    translator = TRANSLATOR_CACHE[locale]
    result = {}

    for i in range(0, len(texts), batch_size):
        chunk = texts[i:i + batch_size]
        try:
            translated_chunk = translator.translate_batch(chunk)
            for orig, trans in zip(chunk, translated_chunk):
                if isinstance(trans, str) and trans.strip():
                    result[orig] = trans
                else:
                    result[orig] = orig
        except Exception as e:
            log(f"Translate error {locale} chunk {i//batch_size}: {e}")
            for t in chunk:
                result[t] = t

    return result


# =============================
# SCAN
# =============================
def scan(lang_root: Path):
    log(f"LANG ROOT = {lang_root}")
    modules = [p for p in lang_root.iterdir() if p.is_dir()]

    missing_by_locale = defaultdict(lambda: defaultdict(dict))
    module_store = {}

    for module_dir in modules:
        zh_file = module_dir / "zh_CN.json"
        if not zh_file.exists():
            log(f"[SKIP] no zh_CN.json in {module_dir.name}")
            continue

        zh_data = load_json(zh_file)
        zh_flat = flatten(zh_data)
        module_name = module_dir.name

        module_store[module_name] = {
            "base": zh_data,           # 原始中文结构
            "translations": {}         # 各语言翻译
        }

        log(f"[SCAN] {module_name} base_keys = {len(zh_flat)}")

        for locale in TARGET_LOCALES:
            if locale == SOURCE_LOCALE:
                continue

            target_file = module_dir / f"{locale}.json"
            existing = flatten(load_json(target_file)) if target_file.exists() else {}

            missing = {k: v for k, v in zh_flat.items() if k not in existing}

            if missing:
                missing_by_locale[locale][module_name].update(missing)

    return missing_by_locale, module_store


# =============================
# APPLY RESULTS (关键修复)
# =============================
def apply(module_store, results):
    for locale, items in results.items():
        grouped = defaultdict(dict)
        for module, key, text in items:
            grouped[module][key] = text

        for module, new_trans in grouped.items():
            # 合并已有翻译 + 新翻译
            existing = module_store[module]["translations"].get(locale, {})
            existing_flat = flatten(existing) if existing else {}

            merged_flat = {**existing_flat, **new_trans}
            translated_tree = unflatten(merged_flat)

            module_store[module]["translations"][locale] = translated_tree


# =============================
# RUN
# =============================
def run(lang_root: Path):
    log("PIPELINE START")

    missing_by_locale, module_store = scan(lang_root)
    batches = defaultdict(list)

    for locale, modules in missing_by_locale.items():
        for module, items in modules.items():
            for k, v in items.items():
                batches[locale].append((module, k, v))

    log(f"BATCH SUMMARY = {{k: len(v) for k, v in batches.items()}}")

    results = {}

    for locale, items in batches.items():
        log(f"[{locale}] 待翻译数量 = {len(items)}")
        texts = [t[2] for t in items]

        translated = translate_batch(locale, texts)

        out = []
        for (module, key, _), text in zip(items, translated.values()):
            out.append((module, key, text))

        results[locale] = out
        log(f"[{locale}] 翻译完成")

    apply(module_store, results)

    # =============================
    # WRITE BACK
    # =============================
    for module, data in module_store.items():
        module_dir = lang_root / module
        module_dir.mkdir(parents=True, exist_ok=True)

        for locale in TARGET_LOCALES:
            if locale == SOURCE_LOCALE:
                continue
            if locale in data["translations"]:
                save_json(
                    module_dir / f"{locale}.json",
                    data["translations"][locale]
                )
                log(f"[SAVE] {module}/{locale}.json")

    log("PIPELINE DONE")


# =============================
# ENTRY
# =============================
if __name__ == "__main__":
    ROOT = Path(__file__).resolve().parents[1]
    LANG_ROOT = ROOT / "lang"

    run(LANG_ROOT)