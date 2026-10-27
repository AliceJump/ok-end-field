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

from src.data.FeatureList import FeatureList as fL
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
    task.wait_pop_up = MagicMock(return_value=True)
    task.find_one = MagicMock(return_value=None)
    task.ocr = MagicMock(return_value=None)
    task.mark_task_failure = MagicMock()
    task.log_info = MagicMock()
    task.tr = MagicMock(side_effect=lambda message: message)  # 绕过框架 gettext
    return task


class TestLangResource(unittest.TestCase):
    """语言资源完整性：代码引用的键全部存在且可编译。"""

    def test_lang_keys_compile_and_cover_code_refs(self):
        code = (_REPO_ROOT / "src" / "tasks" / "onetime" / "MatrixCleanTask.py").read_text(encoding="utf-8")
        # 属性引用（self.lang.matrix_clean_task.k_xxx / 局部 lang.k_xxx）与
        # 字符串引用（元组字面量）
        refs = set(re.findall(r"(?:self\.lang\.matrix_clean_task|(?<!\w)lang)\.(k_[0-9a-f]{8})", code))
        refs |= set(re.findall(r'"(k_[0-9a-f]{8})"', code))
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


class TestDismantleByQuality(unittest.TestCase):
    """批循环：正常结束、异常中断、上限拦截。"""

    def test_open_failure_restores_main_without_dismantling(self):
        task = make_task()
        task._open_vault_essence_page = MagicMock(return_value=False)
        task._dismantle_by_quality = MagicMock()
        task.ensure_main = MagicMock()
        self.assertFalse(task.clean_matrix())
        task.ensure_main.assert_called_once_with(time_out=420)
        task._dismantle_by_quality.assert_not_called()

    def test_immediately_empty_ends_normally(self):
        task = make_task()
        task._enter_dismantle_mode = MagicMock(return_value=True)
        task._dismantle_one_batch = MagicMock(return_value=False)
        task._exit_dismantle_mode = MagicMock()
        self.assertTrue(task._dismantle_by_quality())
        task._dismantle_one_batch.assert_called_once()
        task._exit_dismantle_mode.assert_called_once()
        task.mark_task_failure.assert_not_called()

    def test_stops_after_batches(self):
        task = make_task()
        task._enter_dismantle_mode = MagicMock(return_value=True)
        task._dismantle_one_batch = MagicMock(side_effect=[True, True, False])
        task._exit_dismantle_mode = MagicMock()
        self.assertTrue(task._dismantle_by_quality())
        self.assertEqual(task._dismantle_one_batch.call_count, 3)

    def test_batch_failure_aborts(self):
        task = make_task()
        task._enter_dismantle_mode = MagicMock(return_value=True)
        task._dismantle_one_batch = MagicMock(return_value=None)
        task._exit_dismantle_mode = MagicMock()
        self.assertFalse(task._dismantle_by_quality())
        task._exit_dismantle_mode.assert_called_once()

    def test_enter_failure_aborts_without_exit(self):
        task = make_task()
        task._enter_dismantle_mode = MagicMock(return_value=False)
        task._dismantle_one_batch = MagicMock()
        self.assertFalse(task._dismantle_by_quality())
        task._dismantle_one_batch.assert_not_called()

    def test_batch_limit_guard(self):
        task = make_task()
        task._enter_dismantle_mode = MagicMock(return_value=True)
        task._dismantle_one_batch = MagicMock(return_value=True)
        task._exit_dismantle_mode = MagicMock()
        self.assertFalse(task._dismantle_by_quality())
        self.assertEqual(task._dismantle_one_batch.call_count, MatrixCleanTask.MAX_BATCHES)
        task.mark_task_failure.assert_called_once()


class TestEnterDismantleMode(unittest.TestCase):
    """进入分解模式：已在模式短路、定点点击、验证失败。"""

    def test_already_in_mode_skips_click(self):
        task = make_task()
        task.ocr = MagicMock(return_value=MagicMock())  # 底栏已有「想要分解」
        self.assertTrue(task._enter_dismantle_mode())
        task.click_relative.assert_not_called()

    def test_clicks_entry_and_verifies_hint(self):
        task = make_task()
        task.wait_ocr = MagicMock(return_value=MagicMock())  # 点击后「想要分解」出现
        self.assertTrue(task._enter_dismantle_mode())
        task.click_relative.assert_called_once_with(0.259, 0.93, after_sleep=1)
        task.mark_task_failure.assert_not_called()

    def test_fails_without_hint(self):
        task = make_task()
        task.wait_ocr = MagicMock(return_value=None)
        self.assertFalse(task._enter_dismantle_mode())
        task.click_relative.assert_called_once()
        task.mark_task_failure.assert_called_once_with("未进入分解模式")


class TestDismantleOneBatch(unittest.TestCase):
    """单批流程：批开始空网格短路、筛选面板弹出/不弹、快捷放入失败路径。

    面板「保存」即按筛选结果勾选物品并关面板，保存后直接点分解——
    不存在第二次快捷放入。
    空库时进入分解模式网格即显示「未拥有该类型道具」，批开始必须先查
    该标志直接结束，不做任何筛选动作。
    """

    def test_empty_grid_at_batch_start_is_end(self):
        # 网格空标志在批开始即命中：直接结束，不点快捷放入
        task = make_task()
        task._no_item_left = MagicMock(return_value=True)
        task.wait_click_ocr = MagicMock()
        self.assertFalse(task._dismantle_one_batch())
        task.wait_click_ocr.assert_not_called()
        task.mark_task_failure.assert_not_called()

    def test_filter_panel_flow(self):
        task = make_task()
        # 网格有物品；快捷放入命中；wait_ocr 命中「保存」（面板弹出）；
        # _apply_filter 内 5 次点击（品质 3 次、未上锁、保存）全命中
        task._no_item_left = MagicMock(return_value=False)
        task.wait_click_ocr = MagicMock(return_value=MagicMock())
        task.wait_ocr = MagicMock(return_value=MagicMock())
        task._confirm_dismantle = MagicMock(return_value=True)
        self.assertTrue(task._dismantle_one_batch())
        # 快捷放入 1 次、品质 3 次、未上锁、保存，共 6 次，无第二次快捷放入
        self.assertEqual(task.wait_click_ocr.call_count, 6)
        task._confirm_dismantle.assert_called_once()

    def test_no_panel_flow_skips_filter(self):
        task = make_task()
        task._no_item_left = MagicMock(return_value=False)
        task.wait_click_ocr = MagicMock(return_value=MagicMock())
        task.wait_ocr = MagicMock(return_value=None)  # 面板未弹出（规则已生效）
        task._confirm_dismantle = MagicMock(return_value=True)
        self.assertTrue(task._dismantle_one_batch())
        self.assertEqual(task.wait_click_ocr.call_count, 1)
        task._confirm_dismantle.assert_called_once()

    def test_quick_add_missing_is_failure(self):
        task = make_task()
        task._no_item_left = MagicMock(return_value=False)
        task.wait_click_ocr = MagicMock(return_value=None)
        self.assertIsNone(task._dismantle_one_batch())
        task.mark_task_failure.assert_called_once_with("未找到快捷放入按钮")

    def test_filter_panel_lost_before_apply(self):
        # 面板已检出但正式应用前消失（竞态）：标记失败而非误点底栏
        task = make_task()
        task._no_item_left = MagicMock(return_value=False)
        task.wait_click_ocr = MagicMock(return_value=MagicMock())
        task.wait_ocr = MagicMock(side_effect=[MagicMock(), None])  # 检出命中、复查落空
        self.assertIsNone(task._dismantle_one_batch())
        task.mark_task_failure.assert_called_once_with("筛选面板未弹出")


class TestApplyFilter(unittest.TestCase):
    """筛选面板：品质三项逐一点勾 + 未上锁 + 保存。"""

    def test_panel_not_open_is_failure(self):
        task = make_task()
        task.wait_ocr = MagicMock(return_value=None)  # 「保存」未出现
        self.assertFalse(task._apply_filter())
        task.mark_task_failure.assert_called_once_with("筛选面板未弹出")

    def test_quality_missing_is_failure(self):
        task = make_task()
        task.wait_ocr = MagicMock(return_value=MagicMock())
        # 第一个品质（稳定）就缺失
        task.wait_click_ocr = MagicMock(side_effect=[None])
        self.assertFalse(task._apply_filter())
        task.mark_task_failure.assert_called_once_with("未找到品质筛选选项")

    def test_unlock_missing_is_failure(self):
        task = make_task()
        task.wait_ocr = MagicMock(return_value=MagicMock())
        task.wait_click_ocr = MagicMock(side_effect=[MagicMock(), MagicMock(), MagicMock(), None])
        self.assertFalse(task._apply_filter())
        task.mark_task_failure.assert_called_once_with("未找到未上锁选项")

    def test_save_missing_is_failure(self):
        task = make_task()
        task.wait_ocr = MagicMock(return_value=MagicMock())
        task.wait_click_ocr = MagicMock(side_effect=[MagicMock(), MagicMock(), MagicMock(), MagicMock(), None])
        self.assertFalse(task._apply_filter())
        task.mark_task_failure.assert_called_once_with("未找到筛选保存按钮")

    def test_full_flow_checks_all_qualities(self):
        lang = make_task().lang.matrix_clean_task
        task = make_task()
        task.wait_ocr = MagicMock(return_value=MagicMock())
        task.wait_click_ocr = MagicMock(return_value=MagicMock())
        self.assertTrue(task._apply_filter())
        # 品质三项逐一点勾（面板初始为空）+ 未上锁 + 保存 = 5 次点击
        self.assertEqual(task.wait_click_ocr.call_count, 5)
        clicked = [c.kwargs.get("match") for c in task.wait_click_ocr.call_args_list]
        self.assertIn(lang.k_7ba0a5b1, clicked)
        self.assertIn(lang.k_16da2b84, clicked)
        self.assertIn(lang.k_1a40e234, clicked)
        self.assertIn(lang.k_2396a6d9, clicked)
        task.mark_task_failure.assert_not_called()


class TestConfirmDismantle(unittest.TestCase):
    """分解按钮 / 确认弹窗的失败与成功路径。

    入口先做无法分解先行判定（_dismantle_disabled 连查两次），命中即
    返回 False 结束循环，不再空等「分解」按钮。
    """

    def test_dismantle_button_disabled_is_end(self):
        # 「无法分解」在点击前就被识别，循环直接结束，不空等按钮
        task = make_task()
        task._no_item_left = MagicMock(return_value=False)
        task.ocr = MagicMock(return_value=[MagicMock()])  # 命中「无法分解」
        self.assertFalse(task._confirm_dismantle())
        task.wait_click_feature.assert_not_called()
        task.mark_task_failure.assert_not_called()

    def test_no_item_left_flag_is_end(self):
        # 网格出现「未拥有该类型道具」同样先行结束
        task = make_task()
        task._no_item_left = MagicMock(return_value=True)
        self.assertFalse(task._confirm_dismantle())
        task.wait_click_feature.assert_not_called()

    def test_dismantle_button_missing_without_flag_is_failure(self):
        task = make_task()
        task.wait_click_feature = MagicMock(return_value=False)
        task._no_item_left = MagicMock(return_value=False)
        task.ocr = MagicMock(return_value=None)
        self.assertIsNone(task._confirm_dismantle())
        task.mark_task_failure.assert_called_once_with("未找到分解按钮")

    def test_confirm_button_missing_is_failure(self):
        task = make_task()
        task._no_item_left = MagicMock(return_value=False)
        task.ocr = MagicMock(return_value=None)  # 先行判定未命中
        task.wait_click_feature = MagicMock(return_value=True)  # 「分解」已点击
        task.click_confirm = MagicMock(return_value=False)
        task._close_result_popup = MagicMock()
        self.assertIsNone(task._confirm_dismantle())
        task.mark_task_failure.assert_called_once_with("未找到分解确认按钮")
        task._close_result_popup.assert_not_called()

    def test_full_batch_success(self):
        task = make_task()
        task._no_item_left = MagicMock(return_value=False)
        task.ocr = MagicMock(return_value=None)
        task.wait_click_feature = MagicMock(return_value=True)
        task._close_result_popup = MagicMock(return_value=True)
        self.assertTrue(task._confirm_dismantle())
        # 「分解」按钮与弹窗「确认」共用 skip_dialog_confirm 图标模板
        self.assertEqual(task.wait_click_feature.call_args.args[0], fL.skip_dialog_confirm)
        task.click_confirm.assert_called_once()
        task._close_result_popup.assert_called_once()
        task.mark_task_failure.assert_not_called()


class TestCloseResultPopup(unittest.TestCase):
    """结果弹窗关闭：wait_pop_up 点对勾，对勾仍在就补点；未弹出/关不上都判失败。"""

    def test_closes_after_click(self):
        task = make_task()
        self.assertTrue(task._close_result_popup())
        task.wait_pop_up.assert_called_once_with(time_out=8, after_sleep=1)
        self.assertEqual(task.find_one.call_args.args[0], fL.reward_ok)
        task.click.assert_not_called()
        task.mark_task_failure.assert_not_called()

    def test_no_popup_is_failure(self):
        # 确认后 8 秒无结果弹窗 = 分解未完成，不能把本批计为成功
        task = make_task()
        task.wait_pop_up = MagicMock(return_value=False)
        self.assertFalse(task._close_result_popup())
        task.find_one.assert_not_called()
        task.mark_task_failure.assert_called_once_with("分解结果弹窗未出现")

    def test_clicks_again_while_ok_remains(self):
        task = make_task()
        ok_button = MagicMock()
        task.find_one = MagicMock(side_effect=[ok_button, None])
        self.assertTrue(task._close_result_popup())
        task.click.assert_called_once_with(ok_button, after_sleep=1)
        task.mark_task_failure.assert_not_called()

    def test_marks_failure_after_retries(self):
        task = make_task()
        task.find_one = MagicMock(return_value=MagicMock())  # 对勾一直在
        self.assertFalse(task._close_result_popup())
        # wait_pop_up 点 1 次 + 补点 3 次，每次点击后都查一次对勾
        self.assertEqual(task.click.call_count, 3)
        self.assertEqual(task.find_one.call_count, 4)
        task.mark_task_failure.assert_called_once_with("分解结果弹窗未能关闭")


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
        # 独立单次运行入口（业务与战斗 → 其他）与一条龙入口分开注册
        self.assertIn('"MatrixCleanOnceTask"', config_text)
        ast.parse(config_text)

    def test_once_task_registered_after_daily_impl(self):
        # DailyFeature._resolve_impl 按注册顺序 isinstance 匹配；
        # MatrixCleanOnceTask 是 MatrixCleanTask 子类，若注册在前，
        # 一条龙会解析到单次任务实例并读写错配置
        config_text = (_REPO_ROOT / "src" / "config.py").read_text(encoding="utf-8")
        self.assertLess(
            config_text.index("MatrixCleanTask"),
            config_text.index("MatrixCleanOnceTask"),
        )

    def test_matrix_clean_is_opt_in(self):
        # 分解不可回滚：旧配置迁移与全新默认两条路径都不勾选清理基质
        from src.tasks.onetime.DailyTask import DailyTask

        daily_text = (_REPO_ROOT / "src" / "tasks" / "onetime" / "DailyTask.py").read_text(encoding="utf-8")
        self.assertIs(DailyTask._LEGACY_DEFAULTS.get("清理基质"), False)
        self.assertIn('if task != "清理基质"', daily_text)

    def test_once_task_is_standalone_entry(self):
        from src.tasks.onetime.MatrixCleanTask import MatrixCleanOnceTask, MatrixCleanTask

        self.assertTrue(issubclass(MatrixCleanOnceTask, MatrixCleanTask))
        # 框架按 name 存任务配置，两个入口必须不同名
        self.assertNotEqual(MatrixCleanTask.__name__, MatrixCleanOnceTask.__name__)

    def test_dismantle_entry_is_fixed_click(self):
        # 分解入口是底栏左侧工具组的垃圾桶图标（纯图标无文字），定点点击；
        # essence_throw_away 模板是右上详情面板的单件分解按钮，全屏匹配在
        # 底栏落点偏高约 90px（(0.258, 0.865) 点到物品末行）
        src_text = (_REPO_ROOT / "src" / "tasks" / "onetime" / "MatrixCleanTask.py").read_text(encoding="utf-8")
        self.assertIn("_DISMANTLE_ENTRY", src_text)
        self.assertNotIn("essence_throw_away", src_text)

    def test_filter_checks_all_qualities(self):
        # 面板品质三项初始为空，必须逐项点勾（toggle）：只勾一项会把其他
        # 品质排除出快捷放入，该类清完时整批落空
        src_text = (_REPO_ROOT / "src" / "tasks" / "onetime" / "MatrixCleanTask.py").read_text(encoding="utf-8")
        for key in ("k_7ba0a5b1", "k_16da2b84", "k_1a40e234"):
            self.assertIn(key, src_text)
        self.assertIn("k_2396a6d9", src_text)

    def test_ui_fallback_group_map(self):
        import ast

        ui_text = (_REPO_ROOT / "src" / "gui" / "components" / "EndfieldSectionMatrix.py").read_text(encoding="utf-8")
        self.assertIn('"清理基质": "其他任务"', ui_text)
        ast.parse(ui_text)

    def test_theme_patch_group_mapping(self):
        import ast

        patch_text = (_REPO_ROOT / "src" / "patches" / "endfield_theme_patch.py").read_text(encoding="utf-8")
        # 任务的 group_name 由此映射改写为四大分栏，缺行则任务不出现在任何分栏
        self.assertIn('"MatrixCleanTask": "其他"', patch_text)
        # 独立入口落在业务与战斗的「收尾」组（页签显示为「其他」）
        self.assertIn('"MatrixCleanOnceTask": "收尾"', patch_text)
        ast.parse(patch_text)


if __name__ == "__main__":
    unittest.main()
