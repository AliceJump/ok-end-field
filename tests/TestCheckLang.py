import os
import re
import json
import unittest
from pathlib import Path


SOURCE_ROOT = Path("src")
LANG_ROOT = Path("assets/lang")

PATTERN = re.compile(
    r"self\.lang\.([a-zA-Z0-9_]+)\.(k_[a-zA-Z0-9_]+)"
)


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
    # 加载一个 lang group 下所有 json
    # assets/lang/xxx/*.json
    # =========================
    def load_all_lang_json(self, folder: Path):

        # cache
        if folder in self.lang_cache:
            return self.lang_cache[folder]

        if not folder.exists():
            print(f"[MISSING FOLDER] {folder}")
            return None

        result = {}

        for file in folder.glob("*.json"):
            try:
                with open(file, "r", encoding="utf-8") as f:
                    result[file.stem] = json.load(f)
            except Exception as e:
                print(f"[JSON ERROR] {file} -> {e}")

        print(f"[LOAD LANG] {folder} -> {list(result.keys())}")

        self.lang_cache[folder] = result

        return result

    # =========================
    # 核心检查逻辑
    # =========================
    def collect_missing(self):

        # 完全缺失（FAIL）
        missing = []

        # 部分缺失（WARNING）
        partial_missing = []

        # 去重
        seen = set()

        file_count = 0
        ref_count = 0

        for root, _, files in os.walk(SOURCE_ROOT):

            for name in files:

                if not name.endswith(
                    (".java", ".kt", ".py", ".js", ".ts")
                ):
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
                    # load lang folder
                    # =========================
                    lang_folder = LANG_ROOT / lang_group
                    data_map = self.load_all_lang_json(lang_folder)

                    # =========================
                    # missing folder
                    # =========================
                    if data_map is None:

                        msg = (
                            f"[MISSING_FOLDER] "
                            f"{file_path} -> {lang_group}"
                        )

                        print("     ❌", msg)

                        missing.append(msg)

                        continue

                    found_langs = []
                    missing_langs = []

                    # =========================
                    # check all language files
                    # =========================
                    for lang_code, lang_data in data_map.items():

                        if not isinstance(lang_data, dict):

                            missing_langs.append(lang_code)

                            print(
                                f"     ⚠️ INVALID JSON in {lang_code}"
                            )

                            continue

                        if key in lang_data:

                            print(
                                f"     ✅ FOUND in {lang_code}"
                            )

                            found_langs.append(lang_code)

                        else:

                            print(
                                f"     ⚠️ MISSING in {lang_code}"
                            )

                            missing_langs.append(lang_code)

                    # =========================
                    # missing in ALL languages
                    # -> FAIL
                    # =========================
                    if not found_langs:

                        msg = (
                            f"[MISSING_KEY] "
                            f"{file_path} -> "
                            f"{lang_group}.{key} "
                            f"(missing in ALL languages)"
                        )

                        print("     ❌", msg)

                        missing.append(msg)

                    # =========================
                    # partial missing
                    # -> WARNING ONLY
                    # =========================
                    elif missing_langs:

                        partial_missing.append(
                            (
                                file_path,
                                lang_group,
                                key,
                                missing_langs
                            )
                        )

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

            print(
                "\n========== PARTIAL MISSING =========="
            )

            for (
                file_path,
                lang_group,
                key,
                missing_langs
            ) in partial_missing:

                print(
                    f"\n[PARTIAL] "
                    f"{file_path} -> "
                    f"{lang_group}.{key}"
                )

                print(
                    f"  missing languages: "
                    f"{missing_langs}"
                )

        # =========================
        # FULL MISSING SUMMARY
        # =========================
        if missing:

            print(
                "\n========== MISSING ERRORS =========="
            )

            for msg in missing:
                print(msg)

        return missing

    # =========================
    # unittest entry
    # =========================
    def test_lang_keys_valid(self):

        missing = self.collect_missing()

        # 只有完全缺失才 FAIL
        self.assertEqual(
            missing,
            [],
            msg="\n".join(missing)
        )


if __name__ == "__main__":
    unittest.main()