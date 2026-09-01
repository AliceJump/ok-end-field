import ast
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import json5
from src.data.lang import get_supported_locales

from scripts.i18n import gen_lang_stubs

SOURCE_ROOT = Path("src")
LANG_ROOT = Path("assets/lang")

SUPPORTED_LOCALES = get_supported_locales()
NODE_TYPES = {"string", "pattern", "terms"}


class LangReferenceVisitor(ast.NodeVisitor):
    def __init__(self):
        self.references = []

    def visit_Attribute(self, node):
        module = node.value
        root = module.value if isinstance(module, ast.Attribute) else None
        if (
            isinstance(module, ast.Attribute)
            and isinstance(root, ast.Attribute)
            and root.attr == "lang"
            and isinstance(root.value, ast.Name)
            and root.value.id == "self"
        ):
            self.references.append((module.attr, node.attr))
        self.generic_visit(node)


class LangTestCase(unittest.TestCase):
    # 缓存 lang json，避免重复读取
    lang_cache = {}

    # =========================
    # 提取源码中的 lang 引用
    # =========================
    def find_lang_references(self, file_path: Path):
        try:
            text = file_path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            self.fail(f"[READ FAIL] {file_path}: {exc}")

        try:
            tree = ast.parse(text, filename=str(file_path))
        except SyntaxError as exc:
            self.fail(f"[PARSE FAIL] {file_path}: {exc}")

        visitor = LangReferenceVisitor()
        visitor.visit(tree)
        refs = visitor.references

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
        """
        Scan source files for language references and collect missing-file or active-locale key errors.

        Returns:
            list[str]: Error messages for language files or keys missing from any active locale.
        """
        missing = []

        # 去重
        seen = set()

        file_count = 0
        ref_count = 0

        for file_path in SOURCE_ROOT.rglob("*.py"):
            file_count += 1

            refs = self.find_lang_references(file_path)

            for lang_group, key in refs:
                ref_count += 1

                print(f"  -> checking {lang_group}.{key}")

                # duplicate skip
                ref_id = (str(file_path), lang_group, key)

                if ref_id in seen:
                    print("     (SKIP duplicate)")
                    continue

                seen.add(ref_id)

                # load unified lang json
                data_map = self.load_unified_lang_json(lang_group)

                # missing file
                if data_map is None:
                    msg = f"[MISSING_FILE] {file_path} -> {lang_group}.json"

                    print("     [X]", msg)

                    missing.append(msg)

                    continue

                missing_langs = []

                # check all active locale entries
                for locale_code in SUPPORTED_LOCALES:
                    lang_data = data_map.get(locale_code)

                    if not isinstance(lang_data, dict):
                        missing_langs.append(locale_code)

                        print(f"     [!] MISSING locale {locale_code}")

                        continue

                    if key in lang_data:
                        print(f"     [OK] FOUND in {locale_code}")
                    else:
                        print(f"     [!] MISSING key in {locale_code}")

                        missing_langs.append(locale_code)

                if missing_langs:
                    msg = (
                        f"[MISSING_KEY] {file_path} -> {lang_group}.{key} "
                        f"(missing in {', '.join(missing_langs)})"
                    )
                    print("     [X]", msg)
                    missing.append(msg)

        # =========================
        # SUMMARY
        # =========================
        print("\n========== SUMMARY ==========")

        print(f"files scanned: {file_count}")
        print(f"refs found: {ref_count}")
        print(f"missing errors: {len(missing)}")

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

        self.assertEqual(missing, [], msg="\n".join(missing))

    def test_data_only_lang_modules_are_not_ocr_stubs(self):
        self.assertIn("effect_names", gen_lang_stubs.DATA_ONLY_MODULES)
        self.assertTrue((gen_lang_stubs.LANG_ROOT / "effect_names.json").exists())
        typed_stub = Path("src/data/lang/_lang_typed.py").read_text(encoding="utf-8")
        self.assertNotIn("class EffectNamesModule", typed_stub)
        self.assertNotIn("effect_names: EffectNamesModule", typed_stub)

        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            lang_root = temp_dir / "lang"
            lang_root.mkdir()
            (lang_root / "task.json").write_text(
                '{"confirm": {"zh_CN": {"string": "确认"}}}',
                encoding="utf-8",
            )
            (lang_root / "effect_names.json").write_text(
                '{"STATUS_TEST": {"zh_CN": {"string": "测试"}}}',
                encoding="utf-8",
            )
            typed_out = temp_dir / "_lang_typed.py"
            init_file = temp_dir / "__init__.py"
            init_file.write_text(
                "from ._lang_typed import _LangAccessorTyped\nclass LangAccessor(_LangAccessorTyped):\n    pass\n",
                encoding="utf-8",
            )
            with (
                patch.object(gen_lang_stubs, "REPO_ROOT", temp_dir),
                patch.object(gen_lang_stubs, "LANG_ROOT", lang_root),
                patch.object(gen_lang_stubs, "TYPED_OUT", typed_out),
                patch.object(gen_lang_stubs, "INIT_FILE", init_file),
            ):
                self.assertEqual(gen_lang_stubs.main(), 0)

            generated = typed_out.read_text(encoding="utf-8")
            self.assertIn("class TaskModule", generated)
            self.assertNotIn("EffectNamesModule", generated)
            self.assertNotIn("effect_names:", generated)

    def test_lang_node_schema_is_valid(self):
        errors = []
        for file_path in sorted(LANG_ROOT.glob("*.json")):
            try:
                raw = json5.loads(file_path.read_text(encoding="utf-8"))
            except Exception as exc:
                errors.append(f"{file_path}: {exc}")
                continue

            if not isinstance(raw, dict):
                errors.append(f"{file_path}: top-level value must be an object")
                continue

            for key, locale_dict in raw.items():
                if not isinstance(locale_dict, dict):
                    errors.append(f"{file_path}:{key}: locale map must be an object")
                    continue
                for locale, node in locale_dict.items():
                    if not isinstance(node, dict):
                        errors.append(f"{file_path}:{key}:{locale}: node must be an object")
                        continue
                    present = NODE_TYPES.intersection(node)
                    unknown = set(node).difference(NODE_TYPES)
                    if len(present) != 1 or unknown:
                        errors.append(
                            f"{file_path}:{key}:{locale}: expected exactly one node type, "
                            f"found {sorted(node)}"
                        )
                        continue
                    node_type = next(iter(present))
                    value = node[node_type]
                    if node_type in {"string", "pattern"}:
                        if not isinstance(value, str) or not value:
                            errors.append(f"{file_path}:{key}:{locale}: invalid {node_type}")
                        elif node_type == "pattern":
                            try:
                                re.compile(value)
                            except re.error as exc:
                                errors.append(f"{file_path}:{key}:{locale}: invalid regex: {exc}")
                    elif not (
                        isinstance(value, list)
                        and value
                        and all(isinstance(term, str) and term for term in value)
                    ):
                        errors.append(f"{file_path}:{key}:{locale}: invalid terms")

        self.assertEqual(errors, [], msg="\n".join(errors))


if __name__ == "__main__":
    unittest.main()
