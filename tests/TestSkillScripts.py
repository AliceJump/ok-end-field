import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import polib


ROOT = Path(__file__).resolve().parent.parent


def load_script_module(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


NEXT_TAG = load_script_module(
    "skill_next_tag",
    ".agents/skills/deploy/scripts/next_tag.py",
)
MERGE_PO = load_script_module(
    "skill_merge_po",
    ".agents/skills/ok-script-i18n/scripts/merge_po.py",
)
I18N_HELPER = load_script_module(
    "skill_task_i18n_helper",
    ".agents/skills/ok-script-i18n/scripts/task_i18n_helper.py",
)
LANG_STUBS = load_script_module(
    "script_gen_lang_stubs",
    "scripts/i18n/gen_lang_stubs.py",
)


class SkillScriptTestCase(unittest.TestCase):
    def test_next_tag_variants(self):
        cases = [
            ("release", ["v1.2.3"], "v1.2.4"),
            ("beta", ["v1.2.3"], "v1.2.4-beta.1"),
            ("beta", ["v1.2.3", "v1.2.4-beta.1"], "v1.2.4-beta.2"),
            (
                "alpha",
                ["v1.2.3", "v1.2.4-alpha.2", "v1.2.4-beta.7"],
                "v1.2.4-alpha.3",
            ),
            (
                "beta",
                ["v1.2.3", "v1.2.4-beta.2", "v1.2.4"],
                "v1.2.5-beta.1",
            ),
        ]
        for channel, tags, expected in cases:
            with self.subTest(channel=channel, tags=tags):
                self.assertEqual(NEXT_TAG.next_tag(channel, tags), expected)

    def test_merge_po_preserves_context_plural_and_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ours_path = root / "ours.po"
            theirs_path = root / "theirs.po"

            ours = polib.POFile()
            ours.metadata = {"Language": "zh_CN"}
            ours.append(
                polib.POEntry(
                    msgid="same",
                    msgstr="ours",
                    msgctxt="menu",
                    comment="ours-comment",
                )
            )
            ours.append(
                polib.POEntry(
                    msgid="item",
                    msgid_plural="items",
                    msgstr_plural={0: "项目", 1: "项目们"},
                )
            )
            ours.save(str(ours_path))

            theirs = polib.POFile()
            theirs.metadata = {"Language": "en_US"}
            theirs.append(
                polib.POEntry(
                    msgid="same",
                    msgstr="theirs",
                    msgctxt="menu",
                    comment="theirs-comment",
                )
            )
            theirs.append(
                polib.POEntry(
                    msgid="same",
                    msgstr="other-context",
                    msgctxt="dialog",
                )
            )
            theirs.save(str(theirs_path))

            merged, notes = MERGE_PO.merge_po(
                str(ours_path),
                str(theirs_path),
                prefer="ours",
            )

            self.assertEqual(merged.metadata["Language"], "zh_CN")
            self.assertEqual(merged.find("same", msgctxt="menu").msgstr, "ours")
            self.assertEqual(
                merged.find("same", msgctxt="dialog").msgstr,
                "other-context",
            )
            self.assertEqual(merged.find("item").msgstr_plural[1], "项目们")
            self.assertTrue(notes)

    def test_merge_po_rejects_duplicate_composite_keys(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            catalog_path = Path(temp_dir) / "duplicate.po"
            catalog = polib.POFile()
            catalog.append(polib.POEntry(msgid="same", msgstr="one", msgctxt="menu"))
            catalog.append(polib.POEntry(msgid="same", msgstr="two", msgctxt="menu"))
            catalog.save(str(catalog_path))

            with self.assertRaises(ValueError):
                MERGE_PO.load_catalog(str(catalog_path))

    def test_i18n_scanner_reads_config_type_subscript_fields(self):
        tree = I18N_HELPER.ast.parse(
            """
class Demo:
    def setup(self):
        self.config_type["执行时机"] = {
            "type": "drop_down",
            "options": ["任务最开始", "任务最后"],
        }
        self.config_type["帮助"] = {
            "type": "button",
            "text": "打开帮助",
        }
"""
        )
        visitor = I18N_HELPER.TaskStringVisitor()
        visitor.visit(tree)

        self.assertEqual(
            visitor.strings,
            ["执行时机", "任务最开始", "任务最后", "帮助", "打开帮助"],
        )

    def test_lang_stub_generator_excludes_data_only_modules(self):
        self.assertIn("effect_names", LANG_STUBS.DATA_ONLY_MODULES)
        self.assertIn("yingtuo_stages", LANG_STUBS.DATA_ONLY_MODULES)
        modules = []
        for path in sorted(LANG_STUBS.LANG_ROOT.glob("*.json")):
            if path.stem not in LANG_STUBS.DATA_ONLY_MODULES:
                modules.append(path.stem)
        self.assertNotIn("effect_names", modules)
        self.assertNotIn("yingtuo_stages", modules)

    def test_i18n_helper_rejects_missing_catalog_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(SystemExit):
                I18N_HELPER.check_i18n(temp_dir)


if __name__ == "__main__":
    unittest.main()