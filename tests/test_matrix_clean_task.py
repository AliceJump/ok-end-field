"""MatrixCleanTask 单元测试。

不启动 ok 框架与游戏环境：用 ``__new__`` 构造裸任务实例，
mock 全部 UI 交互（OCR / 模板 / 点击 / 按键），只驱动状态机逻辑；
``lang`` 用真实 LangAccessor 加载，顺带验证语言 JSON 可用。
"""

import json
import re
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.data.lang import get_lang_accessor
from src.tasks.onetime.MatrixCleanTask import MatrixCleanTask

_REPO_ROOT = Path(__file__).resolve().parents[1]


def make_task():
    """构造不打扰 UI 的裸任务实例，交互方法全部替换为 MagicMock。"""
    task = MatrixCleanTask.__new__(MatrixCleanTask)
    task.lang = get_lang_accessor("zh_CN")
    task.box = MagicMock()
    task.box_of_screen = MagicMock(return_value=None)
    task.click = MagicMock()
    task.click_relative = MagicMock()
    task.press_key = MagicMock()
    task.sleep = MagicMock()
    task.wait_feature = MagicMock(return_value=None)
    task.wait_ocr = MagicMock(return_value=None)
    task.wait_click_ocr = MagicMock(return_value=None)
    task.wait_click_feature = MagicMock(return_value=None)
    task.click_confirm = MagicMock(return_value=True)
    task.ocr = MagicMock(return_value=None)
    task.mark_task_failure = MagicMock()
    task.log_info = MagicMock()
    task.tr = MagicMock(side_effect=lambda message: message)  # 绕过框架 gettext
    return task


class TestLangResource(unittest.TestCase):
    """语言资源完整性：代码引用的键全部存在且可编译。"""

    def test_lang_keys_compile_and_cover_code_refs(self):
        code = (_REPO_ROOT / "src" / "tasks" / "onetime" / "MatrixCleanTask.py").read_text(encoding="utf-8")
        refs = set(re.findall(r"self\.lang\.matrix_clean_task\.(k_[0-9a-f]{8})", code))
        self.assertTrue(refs, "代码里没有任何语言键引用")

        data = json.loads((_REPO_ROOT / "assets" / "lang" / "matrix_clean_task.json").read_text(encoding="utf-8"))
        self.assertEqual(refs - set(data), set(), "代码引用了 JSON 里不存在的键")
        self.assertEqual(set(data) - refs, set(), "JSON 里有未被代码使用的键")

        for key, locales in data.items():
            for locale in ("zh_CN", "zh_TW"):
                pattern = locales.get(locale, {}).get("pattern")
                self.assertTrue(pattern, f"{key} 缺 {locale} pattern")
                re.compile(pattern)  # 必须是合法正则

        accessor = get_lang_accessor("zh_CN")
        module = accessor.matrix_clean_task
        for key in refs:
            self.assertIsNotNone(getattr(module, key), f"{key} 经 LangAccessor 取值为空")


class TestDismantleUntilEmpty(unittest.TestCase):
    """循环状态机：批数统计、正常结束、异常中断、上限拦截。"""

    def test_immediately_empty(self):
        task = make_task()
        task._no_item_left = MagicMock(return_value=True)
        self.assertTrue(task._dismantle_until_empty())
        task.mark_task_failure.assert_not_called()

    def test_stops_after_batches(self):
        task = make_task()
        task._no_item_left = MagicMock(side_effect=[False, False, False, True])
        task._dismantle_one_batch = MagicMock(return_value=True)
        self.assertTrue(task._dismantle_until_empty())
        self.assertEqual(task._dismantle_one_batch.call_count, 3)
        task.mark_task_failure.assert_not_called()

    def test_batch_reports_empty_ends_normally(self):
        task = make_task()
        task._no_item_left = MagicMock(return_value=False)
        task._dismantle_one_batch = MagicMock(return_value=False)
        self.assertTrue(task._dismantle_until_empty())
        self.assertEqual(task._dismantle_one_batch.call_count, 1)

    def test_batch_failure_aborts(self):
        task = make_task()
        task._no_item_left = MagicMock(return_value=False)
        task._dismantle_one_batch = MagicMock(return_value=None)
        self.assertFalse(task._dismantle_until_empty())

    def test_batch_limit_guard(self):
        task = make_task()
        task._no_item_left = MagicMock(return_value=False)
        task._dismantle_one_batch = MagicMock(return_value=True)
        self.assertFalse(task._dismantle_until_empty())
        self.assertEqual(task._dismantle_one_batch.call_count, MatrixCleanTask.MAX_BATCHES)
        task.mark_task_failure.assert_called_once()


class TestDismantleOneBatch(unittest.TestCase):
    """单批分支：快捷放入 / 分解按钮 / 确认弹窗的失败与成功路径。"""

    def test_quick_add_missing_with_no_item_is_end(self):
        task = make_task()
        task.wait_click_ocr = MagicMock(return_value=None)
        task._no_item_left = MagicMock(return_value=True)
        self.assertFalse(task._dismantle_one_batch())
        task.mark_task_failure.assert_not_called()

    def test_quick_add_missing_with_items_is_failure(self):
        task = make_task()
        task.wait_click_ocr = MagicMock(return_value=None)
        task._no_item_left = MagicMock(return_value=False)
        self.assertIsNone(task._dismantle_one_batch())
        task.mark_task_failure.assert_called_once()

    def test_dismantle_button_disabled_is_end(self):
        task = make_task()
        # 第一次 wait_click_ocr（快捷放入）命中，第二次（分解按钮）落空
        task.wait_click_ocr = MagicMock(side_effect=[MagicMock(), None])
        task._no_item_left = MagicMock(return_value=False)
        task.ocr = MagicMock(return_value=[MagicMock()])  # 命中「无法分解」
        self.assertFalse(task._dismantle_one_batch())
        task.mark_task_failure.assert_not_called()

    def test_dismantle_button_missing_without_flag_is_failure(self):
        task = make_task()
        task.wait_click_ocr = MagicMock(side_effect=[MagicMock(), None])
        task._no_item_left = MagicMock(return_value=False)
        task.ocr = MagicMock(return_value=None)
        self.assertIsNone(task._dismantle_one_batch())
        task.mark_task_failure.assert_called_once()

    def test_confirm_dialog_missing_is_failure(self):
        task = make_task()
        task.wait_click_ocr = MagicMock(return_value=MagicMock())
        task.wait_ocr = MagicMock(return_value=None)  # 确认弹窗标题不出现
        self.assertIsNone(task._dismantle_one_batch())
        task.mark_task_failure.assert_called_once()

    def test_full_batch_success(self):
        task = make_task()
        task.wait_click_ocr = MagicMock(return_value=MagicMock())
        # 第一次 wait_ocr 命中确认弹窗标题；_close_result_popup 内部再查标题消失
        task.wait_ocr = MagicMock(side_effect=[MagicMock(), MagicMock()])
        task._close_result_popup = MagicMock()
        self.assertTrue(task._dismantle_one_batch())
        task.mark_task_failure.assert_not_called()
        task._close_result_popup.assert_called_once()


class TestCloseResultPopup(unittest.TestCase):
    """结果弹窗关闭：点对勾直至标题消失，超次标记失败。"""

    def test_closes_after_click(self):
        task = make_task()
        task.wait_ocr = MagicMock(side_effect=lambda **kw: [MagicMock()])  # 标题出现
        task.ocr = MagicMock(return_value=None)  # 点击后标题消失
        task._close_result_popup()
        task.click_relative.assert_called_once_with(0.5, 0.86, after_sleep=1)

    def test_marks_failure_after_retries(self):
        task = make_task()
        task.wait_ocr = MagicMock(return_value=[MagicMock()])  # 标题出现
        task.ocr = MagicMock(return_value=[MagicMock()])  # 标题一直不消失
        task._close_result_popup()
        self.assertEqual(task.click_relative.call_count, 4)
        task.mark_task_failure.assert_called_once()

    def test_no_popup_is_noop(self):
        task = make_task()
        task.wait_ocr = MagicMock(return_value=None)
        task._close_result_popup()
        task.click_relative.assert_not_called()
        task.mark_task_failure.assert_not_called()


class TestDailyWiring(unittest.TestCase):
    """每日一条龙接线：清单、旧键迁移映射与执行顺序。"""

    def test_other_tasks_contains_matrix_clean_after_mail(self):
        from src.tasks.onetime.DailyTask import DailyTask

        self.assertIn("清理基质", DailyTask.OTHER_TASKS)
        self.assertLess(DailyTask.OTHER_TASKS.index("收邮件"), DailyTask.OTHER_TASKS.index("清理基质"))
        # 清理基质排在地区建设/造装备前，分解产出的调度券可被后续任务使用
        self.assertLess(DailyTask.OTHER_TASKS.index("清理基质"), DailyTask.OTHER_TASKS.index("地区建设"))

    def test_legacy_key_mapping(self):
        from src.tasks.onetime.DailyTask import DailyTask

        self.assertEqual(DailyTask._LEGACY_TASK_KEYS.get("清理基质"), "⭐清理基质")

    def test_registered_in_onetime_tasks(self):
        import ast

        config_text = (_REPO_ROOT / "src" / "config.py").read_text(encoding="utf-8")
        self.assertIn("src.tasks.onetime.MatrixCleanTask", config_text)
        ast.parse(config_text)

    def test_ui_fallback_group_map(self):
        import ast

        ui_text = (_REPO_ROOT / "src" / "gui" / "components" / "EndfieldSectionMatrix.py").read_text(encoding="utf-8")
        self.assertIn('"清理基质": "其他任务"', ui_text)
        ast.parse(ui_text)


if __name__ == "__main__":
    unittest.main()
