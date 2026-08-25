"""Repair gettext entries whose translations use the wrong locale."""

from __future__ import annotations

import argparse
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import polib
from deep_translator import GoogleTranslator
from opencc import OpenCC


ROOT = Path(__file__).resolve().parents[2]
I18N_ROOT = ROOT / "i18n"
TARGETS = {"es_ES": "es", "ja_JP": "ja", "ko_KR": "ko"}
HAN_RE = re.compile(r"[\u3400-\u9fff]")
PLACEHOLDER_RE = re.compile(r"\{[^{}]+\}")
POLLUTED_MSGIDS = {
    "Account",
    "Account Config",
    "Account List",
    "Account List Operations",
    "Account Task Override Operations",
    "Auto-render attribute controls after selecting task",
    "Global Config",
    "One account (phone) per line, no password needed",
    "Override this task config per account. Unset items will use the original task config.",
    "Select from account list or existing overrides",
    "Show only items differing from default config",
    "View",
    "アカウント設定",
    "グローバル設定",
}
BOTTLE_PARTS = {
    "en_US": (
        ["Amethyst Bottle", "Cryston Bottle", "Ferrium Bottle", "Steel Bottle"],
        ["Jincao", "Yazhen", "Clean Water", "Liquid Xiranite"],
    ),
    "es_ES": (
        ["Frasco de Amatista", "Frasco de Cryston", "Frasco de Ferrium", "Frasco de Acero"],
        ["Jincao", "Yazhen", "Agua Limpia", "Xiranita Líquida"],
    ),
    "ja_JP": (
        ["紫晶瓶", "高晶瓶", "藍鉄瓶", "鋼製瓶"],
        ["錦草", "芽針", "清水", "液化息壌"],
    ),
    "ko_KR": (
        ["자정질 병", "고정질 병", "청철 병", "강철 병"],
        ["금초", "아침", "청수", "액화 식양"],
    ),
    "zh_CN": (
        ["紫晶瓶", "高晶瓶", "蓝铁瓶", "钢瓶"],
        ["锦草", "芽针", "清水", "液化息壤"],
    ),
    "zh_TW": (
        ["紫晶瓶", "高晶瓶", "藍鐵瓶", "鋼瓶"],
        ["錦草", "芽針", "清水", "液化息壤"],
    ),
}
BOTTLE_MSGIDS = [
    ["item_fbottle_glass_grass_1", "item_fbottle_glass_grass_2", "item_fbottle_glass_water", "item_fbottle_glass_xiranite"],
    ["item_fbottle_glassenr_grass_1", "item_fbottle_glassenr_grass_2", "item_fbottle_glassenr_water", "item_fbottle_glassenr_xiranite"],
    ["item_fbottle_iron_grass_1", "item_fbottle_iron_grass_2", "item_fbottle_iron_water", "item_fbottle_iron_xiranite"],
    ["item_fbottle_ironenr_grass_1", "item_fbottle_ironenr_grass_2", "item_fbottle_ironenr_water", "item_fbottle_ironenr_xiranite"],
]


def load_catalogs() -> dict[str, polib.POFile]:
    return {
        path.parents[1].name: polib.pofile(str(path))
        for path in I18N_ROOT.glob("*/LC_MESSAGES/ok.po")
    }


def placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER_RE.findall(text))


def translation_candidates(
    locale: str,
    catalog: polib.POFile,
    english: dict[str, str],
) -> list[polib.POEntry]:
    candidates = []
    for entry in catalog:
        if not entry.msgid or not HAN_RE.search(entry.msgid):
            continue
        english_text = english.get(entry.msgid, "")
        copied_english = bool(
            english_text
            and entry.msgstr == english_text
            and entry.msgstr != entry.msgid
        )
        untranslated_source = locale != "ja_JP" and entry.msgstr == entry.msgid
        if copied_english or untranslated_source:
            candidates.append(entry)
    return candidates


def translate_entries(locale: str, entries: list[polib.POEntry]) -> int:
    if not entries:
        return 0

    translator = GoogleTranslator(source="zh-CN", target=TARGETS[locale])
    source_strings = [entry.msgid for entry in entries]
    translated_strings = translator.translate_batch(source_strings)
    changed = 0
    for entry, translated in zip(entries, translated_strings):
        if not translated or placeholders(translated) != placeholders(entry.msgid):
            raise ValueError(f"Placeholder mismatch for {locale}: {entry.msgid!r} -> {translated!r}")
        if entry.msgstr != translated:
            entry.msgstr = translated
            changed += 1
    return changed


def repair_chinese(catalogs: dict[str, polib.POFile], english: dict[str, str]) -> dict[str, int]:
    simplified = catalogs["zh_CN"]
    traditional = catalogs["zh_TW"]
    to_simplified = OpenCC("t2s")
    to_traditional = OpenCC("s2twp")
    counts = {"zh_CN": 0, "zh_TW": 0}

    for entry in simplified:
        if not entry.msgid or not HAN_RE.search(entry.msgid):
            continue
        english_text = english.get(entry.msgid, "")
        replacement = to_simplified.convert(entry.msgstr)
        if english_text and entry.msgstr == english_text and entry.msgstr != entry.msgid:
            replacement = entry.msgid
        if entry.msgstr != replacement:
            entry.msgstr = replacement
            counts["zh_CN"] += 1

    for entry in traditional:
        if not entry.msgid or not HAN_RE.search(entry.msgid):
            continue
        english_text = english.get(entry.msgid, "")
        if english_text and entry.msgstr == english_text and entry.msgstr != entry.msgid:
            replacement = to_traditional.convert(entry.msgid)
        else:
            replacement = to_traditional.convert(entry.msgstr)
        if entry.msgstr != replacement:
            entry.msgstr = replacement
            counts["zh_TW"] += 1

    return counts


def save_catalogs(catalogs: dict[str, polib.POFile]) -> None:
    for locale, catalog in catalogs.items():
        for msgid in POLLUTED_MSGIDS:
            entry = catalog.find(msgid)
            if entry is not None:
                catalog.remove(entry)
        bottle_names, contents = BOTTLE_PARTS[locale]
        for bottle_index, msgids in enumerate(BOTTLE_MSGIDS):
            for content_index, msgid in enumerate(msgids):
                catalog.find(msgid).msgstr = f"{bottle_names[bottle_index]} ({contents[content_index]})"
        test_translations = {"es_ES": "Prueba", "ja_JP": "テスト", "ko_KR": "테스트"}
        if locale in test_translations:
            catalog.find("Test").msgstr = test_translations[locale]
        if locale == "ja_JP":
            catalog.find("致密源石粉末").msgstr = "緻密源石粉末"
        if locale == "zh_TW":
            tray_entry = catalog.find("Minimize Window to System Tray when Closing")
            tray_entry.msgstr = tray_entry.msgstr.replace("系統托盤", "系統匣")
        po_path = I18N_ROOT / locale / "LC_MESSAGES" / "ok.po"
        catalog.save(str(po_path))
        catalog.save_as_mofile(str(po_path.with_suffix(".mo")))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write repaired PO and MO files")
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="repair Chinese catalogs and polluted IDs without online translation",
    )
    args = parser.parse_args()

    catalogs = load_catalogs()
    english = {entry.msgid: entry.msgstr for entry in catalogs["en_US"]}
    candidates = {
        locale: translation_candidates(locale, catalogs[locale], english)
        for locale in TARGETS
    }
    print("translation candidates:", {locale: len(entries) for locale, entries in candidates.items()})
    if not args.apply:
        return

    counts = repair_chinese(catalogs, english)
    if not args.local_only:
        with ThreadPoolExecutor(max_workers=len(TARGETS)) as executor:
            futures = {
                executor.submit(translate_entries, locale, entries): locale
                for locale, entries in candidates.items()
            }
            for future in as_completed(futures):
                locale = futures[future]
                counts[locale] = future.result()

    save_catalogs(catalogs)
    print("repaired translations:", counts)


if __name__ == "__main__":
    main()
