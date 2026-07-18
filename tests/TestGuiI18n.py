import ast
import unittest
from pathlib import Path
from types import SimpleNamespace


GUI_ROOT = Path("src/gui")


class GuiI18nTestCase(unittest.TestCase):
    def test_translation_calls_do_not_build_ids_inline(self):
        invalid_calls = []
        for path in GUI_ROOT.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                func = node.func
                if not isinstance(func, ast.Attribute) or func.attr != "tr":
                    continue
                if isinstance(node.args[0], (ast.JoinedStr, ast.BinOp, ast.Call)):
                    invalid_calls.append(f"{path}:{node.lineno}")

        self.assertEqual(
            invalid_calls,
            [],
            "GUI translation IDs must be translated before interpolation: " + ", ".join(invalid_calls),
        )

    def test_framework_widgets_receive_untranslated_text(self):
        invalid_calls = []
        translated_widgets = {"ConfigCard", "LabelAndWidget"}
        for path in GUI_ROOT.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                    continue
                if node.func.id not in translated_widgets:
                    continue
                if any(
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and child.func.attr == "tr"
                    for arg in node.args
                    for child in ast.walk(arg)
                ):
                    invalid_calls.append(f"{path}:{node.lineno}")

        self.assertEqual(
            invalid_calls,
            [],
            "Framework widgets translate their own text: " + ", ".join(invalid_calls),
        )

    def test_numeric_runtime_values_are_not_collected(self):
        import ok
        from src.patches import i18n_collection_patch

        original_tr = ok.App.tr
        original_installed = i18n_collection_patch._PATCH_INSTALLED
        try:
            i18n_collection_patch.install_i18n_collection_patch()
            app = SimpleNamespace(
                to_translate=set(),
                po_translation="Failed",
                locale=SimpleNamespace(name=lambda: "zh_CN"),
            )

            self.assertEqual(ok.App.tr(app, "0705"), "0705")
            self.assertNotIn("0705", app.to_translate)
            self.assertEqual(ok.App.tr(app, "日常任务"), "日常任务")
            self.assertIn("日常任务", app.to_translate)
        finally:
            ok.App.tr = original_tr
            i18n_collection_patch._PATCH_INSTALLED = original_installed


if __name__ == "__main__":
    unittest.main()
