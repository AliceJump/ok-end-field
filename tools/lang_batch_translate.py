"""
Batch-generate locale JSON files under assets/lang/<module>/<locale>.json from zh_CN sources.

Translation source:
- External translator via deep-translator (GoogleTranslator by default)
- Regex patterns keep their syntax; Chinese literal spans inside patterns are translated
    independently so regex structure is preserved.

Fallback:
- If a translation is missing or fails, keep the original Chinese text.
- zh_CN is preserved as-is.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any
from concurrent.futures import ThreadPoolExecutor, as_completed

from deep_translator import GoogleTranslator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.lang import SUPPORTED_LOCALES


LANG_ROOT = ROOT / "assets" / "lang"
SOURCE_LOCALE = "zh_CN"
TARGET_LOCALES = tuple(SUPPORTED_LOCALES)
CHINESE_RUN = re.compile(r"[\u4e00-\u9fff]+")
TRANSLATOR_TARGETS = {
    "en_US": "en",
    "es_ES": "es",
    "ja_JP": "ja",
    "ko_KR": "ko",
    "zh_CN": "zh-CN",
    "zh_TW": "zh-TW",
}


def get_translator(locale: str) -> GoogleTranslator | None:
    if locale == SOURCE_LOCALE:
        return None

    target = TRANSLATOR_TARGETS.get(locale)
    if not target:
        return None

    try:
        return GoogleTranslator(source="zh-CN", target=target)
    except Exception:
        return None


TRANSLATOR_CACHE: dict[str, GoogleTranslator | None] = {}
TEXT_CACHE: dict[tuple[str, str], str] = {}


def _translate_batch(locale: str, texts: list[str]) -> dict[str, str]:
    if locale == SOURCE_LOCALE:
        return {text: text for text in texts}

    translator = TRANSLATOR_CACHE.get(locale)
    if locale not in TRANSLATOR_CACHE:
        translator = get_translator(locale)
        TRANSLATOR_CACHE[locale] = translator

    if translator is None:
        return {text: text for text in texts}

    try:
        translated_values = translator.translate_batch(texts)
    except Exception:
        return {text: text for text in texts}

    if not isinstance(translated_values, list) or len(translated_values) != len(texts):
        return {text: text for text in texts}

    result: dict[str, str] = {}
    for original, translated in zip(texts, translated_values):
        if not isinstance(translated, str) or not translated.strip():
            translated = original
        result[original] = translated
        TEXT_CACHE[(locale, original)] = translated
    return result


def collect_translatable_texts(node: Any) -> list[str]:
    collected: list[str] = []
    seen: set[str] = set()

    def add_text(value: str) -> None:
        if not value or value in seen:
            return
        seen.add(value)
        collected.append(value)

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, str):
                    if key == "pattern":
                        for match in CHINESE_RUN.findall(item):
                            add_text(match)
                        add_text(item)
                    else:
                        add_text(item)
                elif isinstance(item, list):
                    for sub in item:
                        if isinstance(sub, str):
                            add_text(sub)
                        else:
                            walk(sub)
                else:
                    walk(item)
            return

        if isinstance(value, list):
            for item in value:
                walk(item)
            return

        if isinstance(value, str):
            add_text(value)

    walk(node)
    return collected


def build_translation_map(locale: str, texts: list[str]) -> dict[str, str]:
    if not texts:
        return {}
    return _translate_batch(locale, texts)


def translate_text(text: str, locale: str, translation_map: dict[str, str]) -> str:
    if locale == SOURCE_LOCALE or not text:
        return text
    return translation_map.get(text, text)


def translate_pattern(pattern: str, locale: str, translation_map: dict[str, str]) -> str:
    if locale == SOURCE_LOCALE:
        return pattern

    if not CHINESE_RUN.search(pattern):
        return pattern

    def repl(match: re.Match[str]) -> str:
        return translation_map.get(match.group(0), match.group(0))

    return CHINESE_RUN.sub(repl, pattern)


def translate_node(node: Any, locale: str, translation_map: dict[str, str]) -> Any:
    if isinstance(node, dict):
        translated: dict[str, Any] = {}
        for key, value in node.items():
            if key == "string" and isinstance(value, str):
                translated[key] = translate_text(value, locale, translation_map)
            elif key == "pattern" and isinstance(value, str):
                translated[key] = translate_pattern(value, locale, translation_map)
            elif key == "terms" and isinstance(value, list):
                translated[key] = [
                    translate_text(item, locale, translation_map) if isinstance(item, str) else item
                    for item in value
                ]
            else:
                translated[key] = translate_node(value, locale, translation_map)
        return translated

    if isinstance(node, list):
        return [translate_node(item, locale, translation_map) for item in node]

    if isinstance(node, str):
        return translate_text(node, locale, translation_map)

    return node


def main() -> int:
    source_files = sorted(LANG_ROOT.glob("*/zh_CN.json"))
    if not source_files:
        print("No zh_CN.json files found under assets/lang/")
        return 1

    module_sources: list[tuple[Path, Any]] = []
    global_texts: dict[str, list[str]] = {locale: [] for locale in TARGET_LOCALES}

    for source_file in source_files:
        module_dir = source_file.parent
        source_data = json.loads(source_file.read_text(encoding="utf-8"))
        module_sources.append((module_dir, source_data))

        collected = collect_translatable_texts(source_data)
        for locale in TARGET_LOCALES:
            if locale == SOURCE_LOCALE:
                continue
            global_texts[locale].extend(collected)

    global_maps: dict[str, dict[str, str]] = {}
    global_maps[SOURCE_LOCALE] = {}

    translate_jobs: dict[Any, str] = {}
    with ThreadPoolExecutor(max_workers=min(3, max(len(TARGET_LOCALES) - 1, 1))) as executor:
        for locale in TARGET_LOCALES:
            if locale == SOURCE_LOCALE:
                continue

            unique_texts = list(dict.fromkeys(global_texts[locale]))
            print(f"Translating {locale}: {len(unique_texts)} unique texts...")
            future = executor.submit(build_translation_map, locale, unique_texts)
            translate_jobs[future] = locale

        for future in as_completed(translate_jobs):
            locale = translate_jobs[future]
            global_maps[locale] = future.result()
            print(f"Done translate {locale}")

    written_files: list[Path] = []

    for module_dir, source_data in module_sources:
        print(f"Processing {module_dir.name}...")

        for locale in TARGET_LOCALES:
            target_file = module_dir / f"{locale}.json"
            translation_map = global_maps.get(locale, {})
            translated_data = translate_node(source_data, locale, translation_map)
            new_text = json.dumps(translated_data, ensure_ascii=False, indent=2) + "\n"

            existing_text = None
            if target_file.exists():
                existing_text = target_file.read_text(encoding="utf-8")

            if existing_text != new_text:
                target_file.write_text(new_text, encoding="utf-8")
                written_files.append(target_file)
                print(f"  wrote {target_file.relative_to(ROOT)}")

        print(f"Done {module_dir.name}")

    print(f"Updated {len(written_files)} locale files.")
    for path in written_files[:20]:
        print(path.relative_to(ROOT))
    if len(written_files) > 20:
        print(f"... and {len(written_files) - 20} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())