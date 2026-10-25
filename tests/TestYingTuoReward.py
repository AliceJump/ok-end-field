import functools
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np
from ok import TaskDisabledException

from src.data.lang import LangAccessor
from src.image.reward_card_probe import CARD_CLAIMABLE, CARD_CLAIMED, CARD_UNFINISHED
from src.tasks.onetime.YingTuoTask import YingTuoTask

# 任务格实测底色：未领取为暗红，已完成且已领取转为灰底，未完成是近黑底。
# 黑与灰按亮度中位数分流：实录 V=29~42 为黑，V=70 为灰。
RED_CARD_BGR = (48, 46, 192)
CLAIMED_CARD_BGR = 70
UNFINISHED_CARD_BGR = 35

PANEL = mock.Mock(x=1190, y=324, width=710, height=594)


class _Task:
    """只提供领取流程用到的属性与调用，避开 GUI 与设备依赖。"""

    width = 1920
    height = 1080
    REWARD_PANEL_BOX = YingTuoTask.REWARD_PANEL_BOX
    CARD_SETTLE_TIME = YingTuoTask.CARD_SETTLE_TIME

    def __init__(self, ocr_boxes=None):
        self.ocr_boxes = ocr_boxes or []
        self.logs = []
        self.lang = LangAccessor("zh_CN")

    def box_of_screen(self, *_args, **_kwargs):
        return PANEL

    def ocr(self, match=None, box=None, frame=None, **_kwargs):
        """按 match 过滤带文字的框；没有文字的替身框视为命中。"""
        if match is None:
            return self.ocr_boxes
        return [b for b in self.ocr_boxes if not isinstance(getattr(b, "name", None), str) or match.search(b.name)]

    def log_info(self, message):
        self.logs.append(message)

    def _top_card_box(self, frame):
        return YingTuoTask._top_card_box(self, frame)

    def find_claim_point(self, frame):
        return YingTuoTask._find_claim_point(self, frame)

    def find_card_state(self, frame):
        return YingTuoTask._find_card_state(self, frame)

    _find_card_state = find_card_state
    _find_claim_point = find_claim_point

    def _settled_state(self):
        return YingTuoTask._settled_state(self)

    def wait_until(self, condition, time_out=6, **_kwargs):
        """逐帧重试的最小替身：条件为真时返回，否则按帧数耗尽后返回 None。"""
        for _ in range(int(time_out * 60)):
            result = condition()
            if result:
                return result
        return None


def _frame(bgr):
    return np.full((1080, 1920, 3), bgr, dtype=np.uint8)


def _mock_task(claim_enabled=True):
    task = mock.Mock()
    task.CFG_CLAIM_REWARD = YingTuoTask.CFG_CLAIM_REWARD
    task.config = {YingTuoTask.CFG_CLAIM_REWARD: claim_enabled}
    # 活动阶段用真实实现，才能检查它与其余海报阶段的先后顺序
    task.run_activity_stages = functools.partial(YingTuoTask.run_activity_stages, task)
    return task


def _run_task(claim_enabled=True, claims=(), challenge=True, hardship=False):
    """用 Mock 替身跑 run()。

    claims 依次作为两次领奖的顶格底色，hardship 是阶段 A 读到的苦难任务是否未完成，
    challenge 是普通关卡挑战的结果。
    """
    task = _mock_task(claim_enabled)
    task.check_activity_tasks.side_effect = [(state, hardship and i == 0) for i, state in enumerate(claims)]
    task.enter_yingtuo.return_value = True
    task.challenge_normal_stages.return_value = challenge
    YingTuoTask.run.__wrapped__(task)
    return task


def _last_log(task):
    return task.log_info.call_args_list[-1].args[0]


class TestYingTuoReward(unittest.TestCase):
    def test_red_first_card_returns_its_text_center(self):
        task = _Task([mock.Mock(x=1325, y=364, width=283, height=89)])

        point = task.find_claim_point(_frame(RED_CARD_BGR))

        self.assertAlmostEqual(point[0], (1325 + 283 / 2) / 1920)
        self.assertAlmostEqual(point[1], (364 + 89 / 2) / 1080)

    def test_claimed_first_card_exits_without_click(self):
        # 灰底：已完成且已领取，再点没有意义
        task = _Task([mock.Mock(x=1325, y=364, width=283, height=89)])

        self.assertIsNone(task.find_claim_point(_frame(CLAIMED_CARD_BGR)))

    def test_unfinished_first_card_exits_without_click(self):
        # 黑底：任务还没完成，点了也领不到
        task = _Task([mock.Mock(x=1325, y=364, width=283, height=89)])

        self.assertIsNone(task.find_claim_point(_frame(UNFINISHED_CARD_BGR)))

    def test_only_red_and_not_claimable_are_distinguished(self):
        # 需求只要「可领取 / 不可领取」两态，灰底与黑底走同一条退出路径
        results = {}
        for name, bgr in (("claimed", CLAIMED_CARD_BGR), ("unfinished", UNFINISHED_CARD_BGR)):
            task = _Task([mock.Mock(x=1325, y=364, width=283, height=89)])
            results[name] = task.find_claim_point(_frame(bgr))

        self.assertIsNone(results["claimed"])
        self.assertIsNone(results["unfinished"])

    def test_topmost_matched_card_is_used(self):
        # 未领取卡片固定置顶，多格命中时只看最靠上的一格
        top = mock.Mock(x=1325, y=364, width=283, height=89)
        lower = mock.Mock(x=1325, y=483, width=287, height=88)
        frame = _frame(RED_CARD_BGR)
        task = _Task([top, lower])

        point = task.find_claim_point(frame)

        self.assertAlmostEqual(point[1], (364 + 89 / 2) / 1080)

    def test_card_anchor_matches_every_task_first_line(self):
        pattern = LangAccessor("zh_CN").daily_routine_mixin.k_2da75f4f
        for text in ("在「影拓丰碑」中，通关普通模式", "活动期间，挑战“深林呓语”任意苦难"):
            self.assertTrue(pattern.search(text), text)
        # 右上角标题与按钮不是任务格
        for text in ("丰碑留名·魔林", "挑战活动"):
            self.assertIsNone(pattern.search(text), text)

    def test_activity_period_top_card_is_not_skipped(self):
        # 真机：领完「怨怒延展」后顶格是苦难黑卡，首行没有「影拓」。
        # 只认「影拓」会跳过它，读到下面的灰卡，误判活动任务已全部完成。
        frame = _frame(UNFINISHED_CARD_BGR)
        frame[600:] = CLAIMED_CARD_BGR
        hardship = SimpleNamespace(name="活动期间，挑战“深林呓语”任意苦难", x=1325, y=364, width=283, height=89)
        normal = SimpleNamespace(name="在「影拓丰碑」中，通关普通模式", x=1325, y=700, width=283, height=89)
        task = _Task([hardship, normal])

        self.assertEqual(task.find_card_state(frame), CARD_UNFINISHED)

    def test_no_anchor_match_returns_none(self):
        task = _Task([])

        self.assertIsNone(task.find_claim_point(_frame(RED_CARD_BGR)))

    def test_wait_uses_project_wait_until(self):
        # 转场期间画面还是上一页，靠 wait_until 的超时窗口逐帧重试，不自建循环
        task = _Task()
        task.wait_until = mock.Mock(return_value=(0.7, 0.4))
        task.frame = "current-frame"

        result = YingTuoTask._wait_claim_point(task, time_out=6)

        self.assertEqual(result, (0.7, 0.4))
        task.wait_until.assert_called_once()
        self.assertEqual(task.wait_until.call_args.kwargs["time_out"], 6)

    def test_claim_logs_the_verdict_once_not_per_frame(self):
        # wait_until 每帧调用一次判定；日志必须只记一次，否则失败原因被刷屏埋掉
        task = _Task()
        task.wait_until = mock.Mock(return_value=None)
        task.frame = "current-frame"

        self.assertIsNone(YingTuoTask._wait_claim_point(task, time_out=6))
        self.assertEqual(sum("不在可领取状态" in log for log in task.logs), 1)

    def test_claim_returns_to_main_before_entering_stage(self):
        # 停在活动中心时 F8 唤不出地图：领完必须先 ensure_main 再进关卡
        task = mock.Mock()
        task.wait_click_ocr = mock.Mock(return_value=True)
        task._wait_card_state = mock.Mock(return_value=CARD_UNFINISHED)

        YingTuoTask.check_activity_tasks(task)

        task.press_esc.assert_called()
        task.ensure_main.assert_called_once()

    def test_card_state_waits_for_stable_reading(self):
        # 实机：活动做完后仍判为未完成；面板绘制期间的首个读数不能直接采用
        task = _Task()
        task.frame = "frame"
        states = iter([CARD_UNFINISHED, CARD_CLAIMED, CARD_CLAIMED])
        task._find_card_state = lambda _frame: next(states)
        task.wait_until = mock.Mock(side_effect=task.wait_until)

        self.assertEqual(YingTuoTask._wait_card_state(task, time_out=6), CARD_CLAIMED)
        self.assertEqual(task.wait_until.call_args.kwargs["settle_time"], YingTuoTask.CARD_SETTLE_TIME)

    def test_settled_state_waits_for_stable_reading(self):
        # 领奖后卡片重排，中途读到的底色同样不能直接采用
        task = _Task()
        task.frame = "frame"
        states = iter([CARD_UNFINISHED, CARD_CLAIMED, CARD_CLAIMED])
        task._find_card_state = lambda _frame: next(states)
        task.wait_until = mock.Mock(side_effect=task.wait_until)

        self.assertEqual(YingTuoTask._wait_card_settled(task, time_out=6), CARD_CLAIMED)
        self.assertEqual(task.wait_until.call_args.kwargs["settle_time"], YingTuoTask.CARD_SETTLE_TIME)

    def test_settled_state_ignores_stale_red_frame(self):
        # 点完领取画面不会立刻刷新，顶格仍是红卡；必须等它变灰/黑再判定，
        # 否则会把「刚领完」误判成「还有红卡待领」。
        task = _Task()
        task.frame = "frame"
        states = iter([CARD_CLAIMABLE, CARD_CLAIMABLE, CARD_CLAIMED, CARD_CLAIMED])
        task._find_card_state = lambda _frame: next(states)

        self.assertEqual(YingTuoTask._wait_card_settled(task, time_out=6), CARD_CLAIMED)

    def test_settled_state_falls_back_to_unfinished_on_timeout(self):
        # 超时仍读到红卡时按未完成处理：宁可多打一轮，也不能误判成已全部完成
        task = _Task()
        task.frame = "frame"
        task._find_card_state = lambda _frame: CARD_CLAIMABLE

        self.assertEqual(YingTuoTask._wait_card_settled(task, time_out=1), CARD_UNFINISHED)

    def test_settled_state_keeps_waiting_while_card_is_unreadable(self):
        # 转场瞬间 OCR 找不到任务格，返回 None 也必须继续等，不能当成「已领完」
        task = _Task()
        task.frame = "frame"
        states = iter([None, CARD_CLAIMABLE, CARD_UNFINISHED, CARD_UNFINISHED])
        task._find_card_state = lambda _frame: next(states)

        self.assertEqual(YingTuoTask._wait_card_settled(task, time_out=6), CARD_UNFINISHED)

    def test_claim_clicks_once_for_all_red_cards(self):
        # 游戏里是一键收取全部红卡，点第二次没有意义；领完直接按重排后的底色判定
        task = mock.Mock()
        task._wait_card_state = mock.Mock(return_value=CARD_CLAIMABLE)
        task._wait_claim_point = mock.Mock(return_value=(0.7, 0.4))
        task._wait_card_settled = mock.Mock(return_value=CARD_UNFINISHED)
        task.wait_click_ocr = mock.Mock(return_value=True)

        state = YingTuoTask._visit_activity_center(task, True)

        self.assertEqual(task.click_relative.call_count, 1)
        self.assertEqual(state, CARD_UNFINISHED)

    def test_read_only_visit_never_clicks_claim(self):
        # 领奖开关关闭时只读顶格和苦难任务，红卡也不点
        task = mock.Mock()
        task._wait_card_state = mock.Mock(return_value=CARD_CLAIMABLE)
        task.wait_click_ocr = mock.Mock(return_value=True)

        state = YingTuoTask._visit_activity_center(task, False)

        task.click_relative.assert_not_called()
        task._wait_claim_point.assert_not_called()
        self.assertEqual(state, CARD_CLAIMABLE)

    def test_claim_skips_click_when_top_card_is_not_red(self):
        # 顶格不是红卡就没有可领的，不该点
        task = mock.Mock()
        task._wait_card_state = mock.Mock(return_value=CARD_CLAIMED)
        task.wait_click_ocr = mock.Mock(return_value=True)

        state = YingTuoTask._visit_activity_center(task, True)

        task.click_relative.assert_not_called()
        self.assertEqual(state, CARD_CLAIMED)

    def test_missing_activity_entrance_is_treated_as_unfinished(self):
        # 打不开活动页就判断不了进度，按未完成处理继续挑战；仍要回主界面
        task = mock.Mock()
        task.wait_click_ocr = mock.Mock(return_value=False)
        task._visit_activity_center = mock.Mock(return_value=None)

        result = YingTuoTask.check_activity_tasks(task)

        self.assertEqual(result, (CARD_UNFINISHED, False))
        task.ensure_main.assert_called()

    def test_missing_activity_entrance_returns_none_from_inner(self):
        # 内层找不到入口时返回 None，由外层翻译成「未完成」
        task = mock.Mock()
        task.wait_click_ocr = mock.Mock(return_value=False)

        self.assertIsNone(YingTuoTask._visit_activity_center(task, True))

    def test_claimed_top_card_skips_to_other_posters(self):
        # 第一阶段领完是灰底：活动任务已全部完成，跳过活动阶段，直接打其余海报
        task = _run_task(claims=[CARD_CLAIMED])

        task.challenge_normal_stages.assert_called_once_with()
        task.challenge_hardship.assert_not_called()
        task.check_activity_tasks.assert_called_once()
        self.assertEqual(_last_log(task), "影拓丰碑任务完成")

    def test_activity_stages_come_before_other_posters(self):
        # 活动任务只针对最左侧海报：先打它并领奖，再重新进入影拓丰碑打其余海报
        task = _run_task(claims=[CARD_UNFINISHED, CARD_CLAIMED])

        steps = [
            name
            for name, _args, _kwargs in task.mock_calls
            if name in ("check_activity_tasks", "enter_yingtuo", "challenge_normal_stages")
        ]
        self.assertEqual(
            steps,
            [
                "check_activity_tasks",
                "enter_yingtuo",
                "challenge_normal_stages",
                "check_activity_tasks",
                "enter_yingtuo",
                "challenge_normal_stages",
            ],
        )
        self.assertEqual(task.challenge_normal_stages.call_args_list, [mock.call(leftmost_only=True), mock.call()])

    def test_reentry_failure_after_claim_skips_other_posters(self):
        task = _mock_task()
        task.check_activity_tasks.side_effect = [(CARD_UNFINISHED, None), (CARD_CLAIMED, None)]
        task.enter_yingtuo.side_effect = [True, False]
        task.challenge_normal_stages.return_value = True

        YingTuoTask.run.__wrapped__(task)

        task.challenge_normal_stages.assert_called_once_with(leftmost_only=True)

    def test_challenge_completion_claims_rewards_again(self):
        # 打完关卡活动任务才转为已完成，收尾要再领一次，把阶段 A 领不到的奖励补上
        task = _run_task(claims=[CARD_UNFINISHED, CARD_CLAIMED])

        self.assertEqual(task.check_activity_tasks.call_count, 2)
        # 阶段 C 之前必须回主界面，否则 F7 唤不出活动中心
        task.ensure_main.assert_called()

    def test_stage_c_unfinished_reports_manual_handling(self):
        # 阶段 C 顶格仍是黑底时不能谎报完成，也不重打活动阶段
        task = _run_task(claims=[CARD_UNFINISHED, CARD_UNFINISHED])

        self.assertEqual(task.challenge_normal_stages.call_args_list, [mock.call(leftmost_only=True), mock.call()])
        self.assertIn("需手动处理", _last_log(task))

    def test_stage_c_gray_top_card_reports_completion(self):
        # 阶段 C 领完变灰：本次确实全部完成，日志才可以说完成
        task = _run_task(claims=[CARD_UNFINISHED, CARD_CLAIMED])

        self.assertEqual(_last_log(task), "影拓丰碑任务完成")

    def test_stage_c_claimable_is_not_reported_as_done(self):
        # 红底仍在说明奖励没领到，不能报完成
        task = _run_task(claims=[CARD_UNFINISHED, CARD_CLAIMABLE])

        self.assertIn("需手动领取", _last_log(task))

    def test_stage_c_still_runs_when_challenge_stops_early(self):
        # 回不到海报页时已打关卡的进度已写入，奖励必须照领
        task = _run_task(claims=[CARD_UNFINISHED, CARD_CLAIMED], challenge=False)

        self.assertEqual(task.check_activity_tasks.call_count, 2)
        # 活动阶段没跑完时不再去打其余海报
        task.challenge_normal_stages.assert_called_once_with(leftmost_only=True)
        self.assertIn("未跑完", _last_log(task))

    def test_stage_c_still_runs_when_challenge_raises(self):
        # 挑战中途报错同样要先领奖，再把错误抛给框架
        task = _mock_task()
        task.check_activity_tasks.side_effect = [(CARD_UNFINISHED, None), (CARD_CLAIMED, None)]
        task.challenge_normal_stages.side_effect = Exception("未能进入战斗")

        with self.assertRaisesRegex(Exception, "未能进入战斗"):
            YingTuoTask.run.__wrapped__(task)

        self.assertEqual(task.check_activity_tasks.call_count, 2)

    def test_task_stop_skips_stage_c(self):
        # 用户停止任务时不再领奖，直接退出
        task = _mock_task()
        task.check_activity_tasks.return_value = (CARD_UNFINISHED, None)
        task.challenge_normal_stages.side_effect = TaskDisabledException()

        with self.assertRaises(TaskDisabledException):
            YingTuoTask.run.__wrapped__(task)

        task.check_activity_tasks.assert_called_once()

    def test_reward_switch_off_reads_tasks_without_claiming(self):
        # 关闭领奖只读一次活动任务，不提前结束，也不做收尾领奖
        task = _run_task(claim_enabled=False, claims=[CARD_CLAIMED])

        task.check_activity_tasks.assert_called_once_with(claim=False)
        task.enter_yingtuo.assert_called_once()
        self.assertEqual(_last_log(task), "影拓丰碑任务完成")

    def test_hardship_runs_with_reward_switch_off(self):
        # 苦难任务属于影拓丰碑本身，不受领奖开关控制
        task = _run_task(claim_enabled=False, claims=[CARD_UNFINISHED], hardship=True)

        task.challenge_hardship.assert_called_once_with()
        # 不领奖就不必回主界面，留在海报页接着打其余海报
        task.enter_yingtuo.assert_called_once()
        self.assertEqual(task.challenge_normal_stages.call_args_list, [mock.call(leftmost_only=True), mock.call()])

    def test_hardship_task_read_across_line_break(self):
        # 任务卡折行可能落在「任意苦」与「难」之间，两行拼起来才能认出苦难任务
        lines = [
            SimpleNamespace(name="活动期间，挑战“深林呓语”任意苦", x=1325, y=364, width=283, height=30),
            SimpleNamespace(name="难模式关卡1次", x=1325, y=400, width=150, height=30),
        ]

        self.assertTrue(YingTuoTask._hardship_task_pending(_Task(lines), _frame(UNFINISHED_CARD_BGR)))

    def test_finished_hardship_task_is_ignored(self):
        lines = [SimpleNamespace(name="活动期间，挑战“深林呓语”任意苦难", x=1325, y=364, width=283, height=30)]

        self.assertFalse(YingTuoTask._hardship_task_pending(_Task(lines), _frame(CLAIMED_CARD_BGR)))

    def test_normal_stage_task_is_not_hardship(self):
        lines = [
            SimpleNamespace(name="在「影拓丰碑」中，通关普通模式", x=1325, y=364, width=283, height=30),
            SimpleNamespace(name="“怨怒延展”", x=1325, y=400, width=120, height=30),
        ]

        self.assertFalse(YingTuoTask._hardship_task_pending(_Task(lines), _frame(UNFINISHED_CARD_BGR)))

    def test_hardship_runs_after_normal_stages(self):
        task = _run_task(claims=[CARD_UNFINISHED, CARD_CLAIMED], hardship=True)

        task.challenge_hardship.assert_called_once_with()
        self.assertEqual(_last_log(task), "影拓丰碑任务完成")

    def test_hardship_skipped_when_normal_stages_stop_early(self):
        task = _run_task(claims=[CARD_UNFINISHED, CARD_UNFINISHED], challenge=False, hardship=True)

        task.challenge_hardship.assert_not_called()

    def test_daily_variant_keeps_battle_group_and_own_name(self):
        # 界面按类名把任务分到页签；日常版必须单独成类，才能落在「战斗」分栏
        from src.tasks.onetime.DailyYingTuoTask import DailyYingTuoTask

        self.assertTrue(issubclass(DailyYingTuoTask, YingTuoTask))
        self.assertEqual(DailyYingTuoTask.run, YingTuoTask.run)
        self.assertEqual(DailyYingTuoTask.CFG_CLAIM_REWARD, YingTuoTask.CFG_CLAIM_REWARD)


if __name__ == "__main__":
    unittest.main()
