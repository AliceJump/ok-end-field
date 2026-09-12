"""任务「使用说明」富文本混入与物品导航说明的测试。"""

import unittest
from types import SimpleNamespace

from src.tasks.mixin.instructions_mixin import InstructionsMixin, inst_gap, inst_line
from src.tasks.trigger.ItemNavigatorTask import ItemNavigatorTask


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


class TestMarkHoldSeconds(unittest.TestCase):
    """「标记按住时长」配置必须真正生效，非法值回退默认。"""

    @staticmethod
    def _stub(config, default=2.0):
        return SimpleNamespace(default_config={"标记按住时长": default}, config=config)

    def test_reads_configured_value(self):
        self.assertEqual(ItemNavigatorTask._mark_hold_seconds(self._stub({"标记按住时长": 1.0})), 1.0)

    def test_missing_key_falls_back_to_default(self):
        self.assertEqual(ItemNavigatorTask._mark_hold_seconds(self._stub({})), 2.0)

    def test_custom_default_is_respected(self):
        self.assertEqual(ItemNavigatorTask._mark_hold_seconds(self._stub({}, default=3.0)), 3.0)

    def test_non_positive_falls_back_to_default(self):
        for value in (0, 0.0, -1):
            self.assertEqual(ItemNavigatorTask._mark_hold_seconds(self._stub({"标记按住时长": value})), 2.0)

    def test_invalid_value_falls_back_to_default(self):
        self.assertEqual(ItemNavigatorTask._mark_hold_seconds(self._stub({"标记按住时长": "abc"})), 2.0)

    def test_numeric_string_is_accepted(self):
        self.assertEqual(ItemNavigatorTask._mark_hold_seconds(self._stub({"标记按住时长": "1.5"})), 1.5)

    def test_format_seconds_trims_trailing_zeros(self):
        self.assertEqual(ItemNavigatorTask._format_seconds(20.0), "20")
        self.assertEqual(ItemNavigatorTask._format_seconds(2.0), "2")
        self.assertEqual(ItemNavigatorTask._format_seconds(0.8), "0.8")
        self.assertEqual(ItemNavigatorTask._format_seconds(1.25), "1.25")


if __name__ == "__main__":
    unittest.main()
