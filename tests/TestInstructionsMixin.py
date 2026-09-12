"""任务「使用说明」富文本混入与物品导航说明的测试。"""

import unittest
from pathlib import Path
from types import SimpleNamespace

import polib

from src.tasks.mixin.instructions_mixin import InstructionsMixin, inst_gap, inst_line
from src.tasks.trigger.ItemNavigatorTask import ItemNavigatorTask

I18N_ROOT = Path("i18n")
MARKED_STORE = Path("configs") / "marked_points.json"


class _DemoInstructions(InstructionsMixin):
    """以计数器验证延迟构建与缓存行为。"""

    def __init__(self):
        self.build_count = 0

    def build_instructions(self):
        self.build_count += 1
        return "EXTRA"


def _navigator_stub(recorded=None, hold_seconds=2.0):
    """构造只暴露 build_instructions 所需属性的物品导航替身。"""

    def tr(text, *args, **kwargs):
        if recorded is not None:
            recorded.append(text)
        return text

    return SimpleNamespace(
        tr=tr,
        _marked_store=MARKED_STORE,
        _near_xz_threshold=20.0,
        _format_seconds=ItemNavigatorTask._format_seconds,
        _mark_hold_seconds=lambda: hold_seconds,
    )


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


class TestItemNavigatorInstructions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recorded = []
        cls.html = ItemNavigatorTask.build_instructions(_navigator_stub(cls.recorded))

    def test_instructions_cover_key_topics(self):
        for expected in (
            "物品导航配置说明",
            "关键配置",
            "标记已获取",
            "本地 WS 模式准备",
            "浮层显示",
            "显示条件",
        ):
            self.assertIn(expected, self.html)

    def test_instructions_show_configured_hold_seconds(self):
        self.assertIn("水平距离 20 以内连续按住标记键 2 秒", self.html)

        html = ItemNavigatorTask.build_instructions(_navigator_stub(hold_seconds=1.0))
        self.assertIn("水平距离 20 以内连续按住标记键 1 秒", html)

    def test_runtime_values_are_not_passed_to_tr(self):
        """运行时数据只能经 .format() 注入；直接喂给 tr() 会污染 gettext 收集池。"""
        self.assertIn("configs/marked_points.json", self.html)
        self.assertIn("assets/scripts/endfield-ws-position-relay.user.js", self.html)
        for text in self.recorded:
            self.assertNotIn("configs/marked_points.json", text)
            self.assertNotIn("endfield-ws-position-relay.user.js", text)

    def test_every_msgid_exists_in_all_locales(self):
        self.assertTrue(self.recorded)
        missing = []
        for path in sorted(I18N_ROOT.glob("*/LC_MESSAGES/ok.po")):
            locale = path.parents[1].name
            entries = {entry.msgid: entry for entry in polib.pofile(str(path)) if entry.msgid}
            for msgid in self.recorded:
                entry = entries.get(msgid)
                if entry is None:
                    missing.append(f"{locale}: 缺少 msgid {msgid!r}")
                elif not entry.msgstr:
                    missing.append(f"{locale}: 未翻译 {msgid!r}")
        self.assertEqual(missing, [], "\n".join(missing))


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
