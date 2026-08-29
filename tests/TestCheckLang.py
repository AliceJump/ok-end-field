import os
import re
import unittest
from pathlib import Path

import json5

SOURCE_ROOT = Path("src")
LANG_ROOT = Path("assets/lang")

PATTERN = re.compile(r"self\.lang\.([a-zA-Z0-9_]+)\.(k_[a-zA-Z0-9_]+)")


SUPPORTED_LOCALES = ["zh_CN", "zh_TW"]


class LangTestCase(unittest.TestCase):
    # 缓存 lang json，避免重复读取
    lang_cache = {}

    # =========================
    # 提取源码中的 lang 引用
    # =========================
    def find_lang_references(self, file_path: Path):
        try:
            text = file_path.read_text(encoding="utf-8")
        except Exception:
            print(f"[READ FAIL] {file_path}")
            return []

        refs = PATTERN.findall(text)

        if refs:
            print(f"[SCAN] {file_path} -> {len(refs)} refs")

        return refs

    # =========================
    # 加载统一的多语言 JSON 文件
    # assets/lang/<module>.json
    # =========================
    def load_unified_lang_json(self, module_name: str):
        """Load unified lang JSON and return {locale: {key: value}} format.

        New format: assets/lang/<module>.json
        Structure: {"k_xxx": {"zh_CN": {"pattern": "..."}, "en_US": {...}}, ...}
        """
        cache_key = f"__unified__{module_name}"

        if cache_key in self.lang_cache:
            return self.lang_cache[cache_key]

        file_path = LANG_ROOT / f"{module_name}.json"
        if not file_path.exists():
            print(f"[MISSING FILE] {file_path}")
            return None

        try:
            with open(file_path, encoding="utf-8") as f:
                raw = json5.load(f)
        except Exception as e:
            print(f"[JSON ERROR] {file_path} -> {e}")
            return None

        # 按 locale 组织数据：{locale: {key: value, ...}, ...}
        result: dict[str, dict] = {}
        for key, locale_dict in raw.items():
            if not isinstance(locale_dict, dict):
                continue
            for locale_code, value in locale_dict.items():
                if locale_code not in result:
                    result[locale_code] = {}
                result[locale_code][key] = value

        print(f"[LOAD LANG] {module_name}.json -> {list(result.keys())} locales")

        self.lang_cache[cache_key] = result

        return result

    # =========================
    # 核心检查逻辑
    # =========================
    def collect_missing(self):
        # 完全缺失（FAIL）
        """
        Scan source files for language references and collect missing-file and fully missing-key errors.

        Returns:
            list[str]: Error messages for language files or keys missing from all supported locales.
        """
        missing = []

        # 部分缺失（WARNING）
        partial_missing = []

        # 去重
        seen = set()

        file_count = 0
        ref_count = 0

        for root, _, files in os.walk(SOURCE_ROOT):
            for name in files:
                if not name.endswith((".java", ".kt", ".py", ".js", ".ts")):
                    continue

                file_path = Path(root) / name
                file_count += 1

                refs = self.find_lang_references(file_path)

                for lang_group, key in refs:
                    ref_count += 1

                    print(f"  -> checking {lang_group}.{key}")

                    # =========================
                    # duplicate skip
                    # =========================
                    ref_id = (str(file_path), lang_group, key)

                    if ref_id in seen:
                        print("     (SKIP duplicate)")
                        continue

                    seen.add(ref_id)

                    # =========================
                    # load unified lang json
                    # =========================
                    data_map = self.load_unified_lang_json(lang_group)

                    # =========================
                    # missing file
                    # =========================
                    if data_map is None:
                        msg = f"[MISSING_FILE] {file_path} -> {lang_group}.json"

                        print("     [X]", msg)

                        missing.append(msg)

                        continue

                    found_langs = []
                    missing_langs = []

                    # =========================
                    # check all locale entries
                    # =========================
                    for locale_code in SUPPORTED_LOCALES:
                        lang_data = data_map.get(locale_code)

                        if not isinstance(lang_data, dict):
                            missing_langs.append(locale_code)

                            print(f"     [!] MISSING locale {locale_code}")

                            continue

                        if key in lang_data:
                            print(f"     [OK] FOUND in {locale_code}")

                            found_langs.append(locale_code)

                        else:
                            print(f"     [!] MISSING key in {locale_code}")

                            missing_langs.append(locale_code)

                    # =========================
                    # missing in ALL languages
                    # -> FAIL
                    # =========================
                    if not found_langs:
                        msg = f"[MISSING_KEY] {file_path} -> {lang_group}.{key} (missing in ALL languages)"

                        print("     [X]", msg)

                        missing.append(msg)

                    # =========================
                    # partial missing
                    # -> WARNING ONLY
                    # =========================
                    elif missing_langs:
                        partial_missing.append((file_path, lang_group, key, missing_langs))

        # =========================
        # SUMMARY
        # =========================
        print("\n========== SUMMARY ==========")

        print(f"files scanned: {file_count}")
        print(f"refs found: {ref_count}")
        print(f"missing errors: {len(missing)}")
        print(f"partial missing: {len(partial_missing)}")

        # =========================
        # PARTIAL MISSING SUMMARY
        # =========================
        if partial_missing:
            print("\n========== PARTIAL MISSING ==========")

            for file_path, lang_group, key, missing_langs in partial_missing:
                print(f"\n[PARTIAL] {file_path} -> {lang_group}.{key}")

                print(f"  missing languages: {missing_langs}")

        # =========================
        # FULL MISSING SUMMARY
        # =========================
        if missing:
            print("\n========== MISSING ERRORS ==========")

            for msg in missing:
                print(msg)

        return missing

    # =========================
    # unittest entry
    # =========================
    def test_lang_keys_valid(self):

        missing = self.collect_missing()

        # 只有完全缺失才 FAIL
        self.assertEqual(missing, [], msg="\n".join(missing))


if __name__ == "__main__":
    unittest.main()
