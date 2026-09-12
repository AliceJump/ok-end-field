"""任务「使用说明」富文本混入与物品导航说明的测试。"""

import unittest

from src.tasks.mixin.instructions_mixin import InstructionsMixin, inst_gap, inst_line


class _DemoInstructions(InstructionsMixin):
    """以计数器验证延迟构建与缓存行为。"""

    def __init__(self):
        self.build_count = 0

    def build_instructions(self):
        self.build_count += 1
        return "EXTRA"


class TestInstructionsMixin(unittest.TestCase):
    def test_build_instructions_is_abstract(self):
        with self.assertRaises(NotImplementedError):
            InstructionsMixin().build_instructions()

    def test_instructions_appends_to_existing_text(self):
        task = _DemoInstructions()
        task.instructions = "BASE"
        self.assertEqual(task.instructions, "BASE<br><br>EXTRA")

    def test_instructions_falls_back_to_extra_when_base_empty(self):
        task = _DemoInstructions()
        task.instructions = None
        self.assertEqual(task.instructions, "EXTRA")

    def test_instructions_is_built_lazily_and_cached(self):
        task = _DemoInstructions()
        task.instructions = "BASE"

        self.assertEqual(task.instructions, "BASE<br><br>EXTRA")
        self.assertEqual(task.instructions, "BASE<br><br>EXTRA")
        self.assertEqual(task.build_count, 1)

    def test_setting_instructions_invalidates_cache(self):
        task = _DemoInstructions()
        task.instructions = "BASE"
        self.assertEqual(task.instructions, "BASE<br><br>EXTRA")

        task.instructions = "OTHER"
        self.assertEqual(task.instructions, "OTHER<br><br>EXTRA")

    def test_inst_line_applies_indent_bold_and_color(self):
        self.assertEqual(inst_line("文本"), '<span style="color:;">文本</span>')
        self.assertEqual(
            inst_line("文本", "#FF5555", bold=True, indent=1),
            '<span style="color:#FF5555;"><strong>&nbsp;&nbsp;&nbsp;&nbsp;文本</strong></span>',
        )

    def test_inst_gap_is_inline_spacer(self):
        self.assertIn("font-size", inst_gap())


if __name__ == "__main__":
    unittest.main()
