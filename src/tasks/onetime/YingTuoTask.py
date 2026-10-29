from typing import ClassVar

from ok import Box, TaskDisabledException

from src.data.FeatureList import FeatureList as fL
from src.icons import Icons
from src.image.gray_bar_detector import detect_gray_bars
from src.image.yingtuo_poster_template import crop_poster_template, matches_entered_template, nearby_poster_templates, poster_view_changed
from src.image.reward_card_probe import CARD_CLAIMABLE, CARD_CLAIMED, CARD_UNFINISHED, card_state
from src.tasks.mixin.battle_mixin import BattleMixin


def _settled(read):
    """把读数函数包成 wait_until 的条件：读数与上一次不同时返回 None。

    wait_until 只在条件为假时重新计稳定时间；"monument" 与 "handbook"、"on" 与 "off" 都是真值，
    不包这一层，读数在稳定期内变了也照样返回。
    """
    last = None

    def condition():
        nonlocal last
        value, previous = read(), last
        last = value
        return value if value == previous else None

    return condition


class YingTuoError(Exception):
    """画面没有按预期切换。run() 捕获它、记失败并返回 False，一键日常的后续任务照常执行。"""


class YingTuoTask(BattleMixin):
    CFG_CLAIM_REWARD: ClassVar[str] = "⭐领取影拓丰碑奖励"
    # 领奖开启且活动任务已全部完成时跳过整个任务；独立任务仍挑战其余海报，一键日常版跳过。
    SKIP_WHEN_ACTIVITY_DONE: ClassVar[bool] = False

    # 任务卡文本所在的右侧面板，限定 OCR 与红底检测范围。
    REWARD_PANEL_BOX: ClassVar = (0.62, 0.30, 0.99, 0.85)
    # 点击活动入口后，任务面板实测约 0.55 秒绘制完成，留足转场重试窗口。
    REWARD_PANEL_TIMEOUT: ClassVar = 6
    # 绘制期间读到的底色不可信；同一底色连续保持这段时间才采用。
    CARD_SETTLE_TIME: ClassVar = 1.0
    # 打完关卡退出副本要加载，实测 22.7 秒，留足余量。
    RETURN_LOADING_TIMEOUT: ClassVar = 90
    # 点击灰条后等关卡详情页出现的时间与重试次数。
    DETAIL_PAGE_TIMEOUT: ClassVar = 5
    CHALLENGE_CLICK_ATTEMPTS: ClassVar = 3
    # 关卡段淡入约 0.55 秒，淡入前也会连续检测为空；空结果连续保持这段时间才采用。
    EMPTY_BARS_SETTLE_TIME: ClassVar = 0.6
    # 以下坐标取自 1920x1080 实录。活动任务只针对海报页最左侧的海报：
    # 它的海报图中部，与它下方灰条所在区域的右边界（第二张海报的灰条从 0.37 起）。
    LEFTMOST_POSTER_POINT: ClassVar = (0.15, 0.45)
    LEFTMOST_POSTER_MAX_X: ClassVar = 0.31
    # 关卡详情页右下角苦难模式开关的「开」「关」两半，与左上方的关卡标题。
    HARDSHIP_ON_POINT: ClassVar = (0.729, 0.918)
    HARDSHIP_OFF_POINT: ClassVar = (0.776, 0.918)
    DETAIL_TITLE_BOX: ClassVar = (0.60, 0.08, 0.85, 0.14)
    # 撤离确认框实测按 ESC 后 0.25 秒出现；等不到就重按 ESC 的次数与每次等待时间。
    EVACUATE_ATTEMPTS: ClassVar = 3
    EVACUATE_DIALOG_TIMEOUT: ClassVar = 3

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "影拓丰碑"
        self.icon = Icons.YingTuo
        self.group_name = "战斗"
        self.description = "先完成最左侧海报（当期活动）的普通关卡和苦难任务，再挑战其余海报的普通关卡"

        self.support_schedule_task = True
        self._entered_poster_templates = []
        self._last_normal_attempt_recorded = False
        self._abandoned_normal_challenges = 0
        self.default_config.update({self.CFG_CLAIM_REWARD: True})
        self.config_description[self.CFG_CLAIM_REWARD] = (
            "在「活动中心」领取影拓丰碑的活动奖励。\n关闭后只跳过领奖，关卡和苦难任务照常完成。"
        )

    def run(self):
        try:
            return self.run_yingtuo()
        except YingTuoError as e:
            # 直接抛出会中断整个一键日常。
            self.mark_task_failure(str(e))
            return False

    def run_yingtuo(self):
        # 仅在新任务开始时清空，活动与其余海报、退出重进期间保持本轮记录。
        self._entered_poster_templates.clear()
        self._last_normal_attempt_recorded = False
        self._abandoned_normal_challenges = 0
        self.ensure_main(time_out=400)
        claim_enabled = self.config.get(self.CFG_CLAIM_REWARD, True)
        # 苦难任务属于影拓丰碑本身，领奖开关关闭时也要读出它是否完成。
        state, hardship_pending = self.check_activity_tasks(claim=claim_enabled)
        if not claim_enabled:
            self.log_info("已关闭『领取影拓丰碑奖励』，只读取活动任务，不领奖")
        elif state == CARD_CLAIMED and self.SKIP_WHEN_ACTIVITY_DONE:
            self.log_info("活动任务已全部完成，跳过影拓丰碑", notify=True)
            return
        if not self.enter_yingtuo():
            return
        finished = True
        if state == CARD_CLAIMED:
            self.log_info("活动任务已全部完成，直接挑战其余海报的普通关卡")
        else:
            # 先把活动任务做完并领奖，再打与活动无关的其余海报。
            finished, state = self.run_activity_stages(hardship_pending, claim_enabled)
            if finished and claim_enabled and not self.enter_yingtuo():
                return
        if finished:
            finished = self.challenge_normal_stages()
        if not finished:
            self.log_info("影拓丰碑挑战未跑完，剩余关卡留待下次", notify=True)
        if self._abandoned_normal_challenges:
            self.mark_task_failure(
                f"本轮 {self._abandoned_normal_challenges} 个已进入组合未正常完成，已跳过并尝试其他组合"
            )
        if not finished or self._abandoned_normal_challenges:
            return False
        if state == CARD_UNFINISHED:
            self.log_info("影拓丰碑关卡已打完，活动任务仍有未完成项，需手动处理", notify=True)
        elif state == CARD_CLAIMABLE:
            self.log_info("影拓丰碑活动奖励未领完，需手动领取", notify=True)
        else:
            self.log_info("影拓丰碑任务完成", notify=True)

    def run_activity_stages(self, hardship_pending, claim_enabled):
        """活动阶段：打最左侧海报的普通关卡，再做苦难任务，开关开启时回主界面领奖。

        返回 ``(是否跑完, 领奖后的顶格底色)``；未领奖时底色为 None。
        """
        self.log_info("活动任务仍有未完成项，先挑战最左侧海报")
        error = None
        try:
            finished = self.challenge_normal_stages(leftmost_only=True)
            if finished and hardship_pending:
                self.challenge_hardship()
        except TaskDisabledException:
            raise
        except Exception as e:
            # 已打关卡的进度已经写入，先领奖再上报错误。
            self.log_info(f"影拓丰碑挑战出错：{e}")
            error = e
            finished = False
        state = None
        if claim_enabled:
            self.ensure_main()
            state, _ = self.check_activity_tasks()
        if error is not None:
            raise error
        return finished, state

    def challenge_normal_stages(self, leftmost_only=False):
        """逐个挑战未通关的普通关卡；全部打完返回 True，中途结束返回 False。

        ``leftmost_only`` 为真时只挑战最左侧海报的关卡。
        """
        empty_visits = 0
        while self.find_normal_challenge(leftmost_only):
            self.log_info("检测到普通关卡灰条，处理本屏未通关关卡")
            results = (
                self.wait_until(
                    lambda: self.find_feature(
                        feature=fL.yingtuo_not_cleared_icon,
                        box=self.box_of_screen(0.033, 0.133, 0.058, 0.778),
                    ),
                    time_out=10,
                    settle_time=0.5,
                    raise_if_not_found=False,
                )
                or []
            )
            if results:
                empty_visits = 0
            else:
                # 返回后仍会检测到同一组灰条，不设上限就会反复点同一张海报。
                empty_visits += 1
                if empty_visits >= 2:
                    self.log_info("关卡详情页连续两次没有未通关图标，结束挑战")
                    return False
                self.log_info("关卡详情页没有未通关图标，返回海报页")
            abandoned_poster = False
            for result in results:
                self.click(result, after_sleep=0.25)
                # 实录中打完普通关卡后，游戏把详情页切到了苦难模式；苦难任务撤离后也可能停在苦难模式。
                if not self._set_hardship_mode(False):
                    self.log_info("未能关闭苦难模式，结束挑战")
                    raise YingTuoError("未能关闭苦难模式")
                self.log_info("进入挑战页面，开始挑战")
                self._click_challenge_and_enter()
                if not self.battle_and_exit(transition_recovery=self._retry_entry):
                    self._recover_entered_normal_challenge("战斗未正常完成")
                    abandoned_poster = True
                    break
                if not self.wait_feature(
                    feature=fL.to_max_produce_num,
                    box=self.box_of_screen(0.934, 0.881, 0.977, 0.965),
                    time_out=60,
                    settle_time=0.5,
                    raise_if_not_found=False,
                ):
                    self._recover_entered_normal_challenge("未能返回关卡详情页")
                    abandoned_poster = True
                    break
                self.log_info("挑战完成，继续寻找下一个普通关卡")
            if not abandoned_poster and not self.return_to_monument():
                self._recover_entered_normal_challenge("未能返回海报页")
        self.log_info("最左侧海报的普通关卡已全部打完" if leftmost_only else "影拓丰碑普通关卡已全部打完")
        return True

    def _recover_entered_normal_challenge(self, reason):
        """失败后恢复海报页，跳过本轮已选择过的灰条组合。

        如果还没记录选中的灰条，则无法确认本轮尝试范围，仍按异常终止。
        """
        if not self._last_normal_attempt_recorded:
            raise YingTuoError(reason)
        self._abandoned_normal_challenges += 1
        self.log_info(f"{reason}；当前组合已进入过，恢复后跳过并继续扫描")
        if self._wait_monument(time_out=3):
            return
        self.ensure_main(time_out=180)
        if not self.enter_yingtuo():
            raise YingTuoError("退出已进入的关卡后无法恢复影拓丰碑海报页")

    def _click_challenge_and_enter(self):
        """在关卡详情页点「挑战影拓丰碑」，再在编队页点入场；任一按钮找不到时抛出 YingTuoError。"""
        if not self.wait_click_feature(
            feature=fL.to_max_produce_num,
            box=self.box_of_screen(0.934, 0.881, 0.977, 0.965),
            time_out=10,
            settle_time=0.5,
            raise_if_not_found=False,
        ):
            self.log_info("未能找到挑战开始按钮")
            raise YingTuoError("未能找到挑战开始按钮")
        if not self.wait_click_feature(
            feature=fL.give_gift,
            box=self.box_of_screen(0.934, 0.881, 0.977, 0.965),
            time_out=10,
            settle_time=0.5,
            raise_if_not_found=False,
        ):
            self.log_info("未能进入战斗")
            raise YingTuoError("未能进入战斗")

    def _retry_entry(self):
        """等待入场期间，编队页的入场按钮仍在时再点一次。"""
        button = self.find_one(
            feature=fL.give_gift,
            box=self.box_of_screen(0.934, 0.881, 0.977, 0.965),
        )
        if button:
            self.log_info("挑战入场按钮仍在，重试入场")
            self.click(button, after_sleep=0.5)

    def challenge_hardship(self):
        """完成「挑战指定海报任意苦难模式关卡 1 次」：进入最左侧海报的苦难关卡后立即撤离。

        进入关卡即计入挑战次数，不必打完。撤离后回到详情页时关掉苦难模式，免得下次进详情页仍停在苦难；
        回到海报页时不再回详情页关，普通关卡进场前会再确认一次。
        """
        self.log_info("开始苦难任务：进入最左侧海报的苦难关卡后撤离")
        if not self._open_leftmost_poster():
            self.log_info("未能打开最左侧海报的关卡详情页，跳过苦难任务")
            return
        if not self._set_hardship_mode(True):
            self.log_info("未能开启苦难模式，跳过苦难任务")
            self.return_to_monument()
            return
        self._click_challenge_and_enter()
        if not self.wait_battle_space(transition_recovery=self._retry_entry):
            raise YingTuoError("苦难关卡未能进入协议空间")
        self.log_info("已进入苦难关卡，撤离")
        if not self._evacuate():
            raise YingTuoError("按 ESC 后未出现撤离确认框")
        page = self.wait_until(
            _settled(self._detail_or_monument),
            time_out=self.RETURN_LOADING_TIMEOUT,
            settle_time=0.5,
            raise_if_not_found=False,
        )
        if page is None:
            raise YingTuoError("撤离后未回到关卡详情页或海报页")
        if page == "detail":
            if not self._set_hardship_mode(False):
                raise YingTuoError("撤离后未能关闭苦难模式")
            if not self.return_to_monument():
                raise YingTuoError("撤离后未能返回海报页")
        self.log_info("苦难任务已完成")

    def _evacuate(self):
        """按 ESC 呼出「是否确认退出当前影拓丰碑？」并点确认；返回是否点到确认。

        实测战斗中点左上角的「撤离」按钮不会弹出确认框；按钮旁标的快捷键是 ESC。
        入场后约 2 秒内撤离按钮是灰的，这时按 ESC 可能没反应，所以等不到确认框就重按。
        确认框出现后不再重按，以免 ESC 把它关掉。
        """
        for attempt in range(1, self.EVACUATE_ATTEMPTS + 1):
            self.back()
            if self.wait_until(self.find_confirm, time_out=self.EVACUATE_DIALOG_TIMEOUT, raise_if_not_found=False):
                return self.click_confirm(time_out=self.EVACUATE_DIALOG_TIMEOUT)
            self.log_info(f"按 ESC 后未出现撤离确认框 ({attempt}/{self.EVACUATE_ATTEMPTS})")
        return False

    def _open_leftmost_poster(self):
        """打开最左侧海报的关卡详情页；返回是否打开。

        活动阶段只点过最左侧海报，海报页一直停在起始位置。不按海报名找，
        因为 OCR 会把「深林呓语」读成「深林吃语」。
        """
        self.click_relative(*self.LEFTMOST_POSTER_POINT)
        return bool(self.wait_until(self._on_detail_page, time_out=self.DETAIL_PAGE_TIMEOUT, raise_if_not_found=False))

    def _set_hardship_mode(self, on):
        """把苦难模式切到「开」或「关」，按标题是否带「苦难」确认；返回是否切换成功。

        详情页沿用上次选的模式，点已选中的那一半也会把开关切走，所以先读当前模式，不一致才点。
        """
        target = "on" if on else "off"
        # 详情页淡入约 0.4 秒，同一模式连续读到 0.5 秒后再判断。
        mode = self.wait_until(_settled(self._hardship_mode), time_out=3, settle_time=0.5, raise_if_not_found=False)
        if mode == target:
            return True
        self.click_relative(*(self.HARDSHIP_ON_POINT if on else self.HARDSHIP_OFF_POINT), after_sleep=0.5)
        return bool(self.wait_until(lambda: self._hardship_mode() == target, time_out=3, raise_if_not_found=False))

    def _hardship_mode(self):
        """按关卡标题读苦难模式：带「苦难」返回 "on"，是别的标题返回 "off"，没读到标题返回 None。

        苦难模式下关卡标题写作「虫傀呼应·苦难」。
        """
        boxes = self.ocr(box=self.box_of_screen(*self.DETAIL_TITLE_BOX))
        if not boxes:
            return None
        pattern = self.lang.daily_routine_mixin.k_ebbfb983
        return "on" if any(pattern.search(box.name) for box in boxes) else "off"

    def _detail_or_monument(self):
        """在关卡详情页返回 "detail"，否则按 _monument_or_handbook 判断。"""
        if self._on_detail_page():
            return "detail"
        return self._monument_or_handbook()

    def check_activity_tasks(self, claim=True):
        """查看活动中心里影拓丰碑的活动任务，返回 ``(顶格底色, 苦难任务是否未完成)``。

        流程：按 F7 打开活动中心 → OCR 定位第一格任务 → ``claim`` 为真且红底就点一下
        （一键收取全部红卡）→ 读未完成的苦难任务 → 退回主界面。底色为 ``CARD_CLAIMED``（灰底，
        活动任务已全部完成）、``CARD_UNFINISHED``（黑底，还有没做完的任务）或
        ``CARD_CLAIMABLE``（红底但找不到可点位置，奖励未领）。

        领完必须退回主界面再交给 enter_yingtuo：停在活动中心时按 F8 唤不出地图。
        """
        self.log_info("开始领取影拓丰碑活动奖励" if claim else "开始读取影拓丰碑活动任务")

        state = self._visit_activity_center(claim)
        if state is None:
            # 打不开活动页时无法判断进度，按未完成处理，宁可多打一轮
            self.ensure_main()
            return CARD_UNFINISHED, False
        hardship_pending = self._hardship_task_pending(self.frame)
        if hardship_pending:
            self.log_info("苦难任务未完成")

        # 活动中心可能叠了多层弹窗，先按一次 Esc 再交给 ensure_main 的两段稳定检查；
        # 停在活动页时 F8 唤不出地图，enter_yingtuo 会直接找不到入口。
        self.press_esc()
        self.ensure_main()
        return state, hardship_pending

    def _hardship_task_pending(self, frame):
        """有未完成（黑底）的苦难任务时返回 True。

        任务卡写作「活动期间，挑战“深林呓语”任意苦难模式关卡1次」，会折成两行。
        折行可能落在「苦」「难」之间，因此把与该行左对齐、紧接其下的一行拼起来再找「苦难」。
        任务指定的海报就是最左侧海报，challenge_hardship 直接打开它，不必读海报名。
        """
        panel = self.box_of_screen(*self.REWARD_PANEL_BOX)
        boxes = self.ocr(box=panel, frame=frame)
        pattern = self.lang.daily_routine_mixin.k_ebbfb983
        for box in boxes:
            text = "".join(
                b.name for b in boxes if abs(b.x - box.x) <= box.height and box.y <= b.y <= box.y + 2 * box.height
            )
            if pattern.search(text) and card_state(frame, box) == CARD_UNFINISHED:
                return True
        return False

    def _visit_activity_center(self, claim):
        """打开活动中心，``claim`` 为真时领取影拓丰碑奖励；返回顶格底色，打不开活动页时返回 None。"""
        self.press_key("f7")
        if not self.wait_click_ocr(match=self.lang.daily_routine_mixin.k_ed7e11a1, box=self.box.left, time_out=5):
            self.log_info("未找到含『丰碑』的活动入口，跳过")
            return None
        self.log_info("进入影拓丰碑活动页")

        state = self._wait_card_state(time_out=self.REWARD_PANEL_TIMEOUT)
        # 点一下是「一键收取」全部红卡，所以最多只领一次；领完红卡全部变灰沉底，
        # 露出来的顶格就是决定后续流程的底色。
        if claim and state == CARD_CLAIMABLE:
            point = self._wait_claim_point(time_out=self.REWARD_PANEL_TIMEOUT)
            if point is not None:
                self.click_relative(point[0], point[1], after_sleep=0.5)
                self.wait_pop_up()
                self.log_info("已领取影拓丰碑活动奖励")
                state = self._wait_card_settled(time_out=self.REWARD_PANEL_TIMEOUT)
        return state

    def _wait_card_state(self, time_out=6):
        """等到任务面板绘制完成，返回第一格卡片的底色；识别不到时按未完成处理。

        点击活动入口后右侧面板由游戏动画换页，刚点完取到的仍是上一页画面，
        OCR 找不到任务格。项目的 ``wait_until`` 默认取首个读数，实机曾把已领取的灰底读成未完成。
        因此要求同一底色连续保持 ``CARD_SETTLE_TIME`` 秒。
        """
        state = self.wait_until(
            _settled(lambda: self._find_card_state(self.frame)),
            time_out=time_out,
            settle_time=self.CARD_SETTLE_TIME,
        )
        return state or CARD_UNFINISHED

    def _wait_card_settled(self, time_out=6):
        """等领奖后的重排落定，返回顶格新底色。

        点完领取画面不会立刻刷新，直接取帧读到的仍是旧的红卡；这里等到顶格
        不再是红底为止，避免把「刚领完」误判成「还有红卡待领」。
        """
        state = self.wait_until(_settled(self._settled_state), time_out=time_out, settle_time=self.CARD_SETTLE_TIME)
        return state or CARD_UNFINISHED

    def _settled_state(self):
        """顶格已不是红底时返回它的底色，否则返回 None 让 ``wait_until`` 继续等。"""
        state = self._find_card_state(self.frame)
        return state if state not in (None, CARD_CLAIMABLE) else None

    def _wait_claim_point(self, time_out=6):
        """返回第一格任务的可点击位置；不可领取或识别不到时返回 None。"""
        point = self.wait_until(lambda: self._find_claim_point(self.frame), time_out=time_out)
        if point is None:
            self.log_info("第一格任务不在可领取状态，无需领取")
        return point

    def _top_card_box(self, frame):
        """取活动页右侧任务列表最靠上的任务格文本框。

        任务卡首行写法不一：「在「影拓丰碑」中，通关普通模式…」「活动期间，挑战…」，
        锚词并列匹配「影拓」「活动期间」「普通模式」，命中任一即算任务格。不用「丰碑」：
        右上角标题「丰碑留名·魔林」含该词，会污染第一格判断。
        本函数在 ``wait_until`` 里逐帧执行，不写日志。
        """
        panel = self.box_of_screen(*self.REWARD_PANEL_BOX)
        boxes = self.ocr(match=self.lang.daily_routine_mixin.k_2da75f4f, box=panel, frame=frame)
        return boxes[0] if boxes else None

    def _find_card_state(self, frame):
        """返回第一格卡片的底色；找不到任务格时返回 None 交给上层重试。"""
        box = self._top_card_box(frame)
        if box is None:
            return None
        return card_state(frame, box)

    def _find_claim_point(self, frame):
        """第一格可领取时返回其文本框中心的归一化坐标，否则返回 None。"""
        box = self._top_card_box(frame)
        if box is None or card_state(frame, box) != CARD_CLAIMABLE:
            return None
        return (box.x + box.width / 2) / self.width, (box.y + box.height / 2) / self.height

    def return_to_monument(self):
        """从关卡详情页返回海报页，返回是否已回到海报页。

        打完关卡后按返回会退出副本并加载。加载期间再按返回，按键会在加载结束后生效，
        把刚出现的海报页关回行动手册，所以离开详情页后只等待、不再按键。
        """
        for _ in range(3):
            self.back()
            if self.wait_until(lambda: not self._on_detail_page(), time_out=5, raise_if_not_found=False):
                break
        else:
            self.log_info("按返回后仍停在关卡详情页")
            return False
        # 页面须稳定 0.5 秒：加载结束时海报页可能只闪一帧，随后落到行动手册。
        page = self.wait_until(
            _settled(self._monument_or_handbook),
            time_out=self.RETURN_LOADING_TIMEOUT,
            settle_time=0.5,
            raise_if_not_found=False,
        )
        if page == "monument":
            return True
        if (
            page == "handbook"
            and self.wait_click_feature(feature=fL.yingtuo_entrance, time_out=2, raise_if_not_found=False)
            and self._wait_monument(time_out=10)
        ):
            self.log_info("从行动手册重新进入影拓丰碑页面")
            return True
        return False

    def _monument_or_handbook(self):
        """当前在海报页返回 "monument"，在行动手册常驻页返回 "handbook"，都不是返回 None。"""
        if self.find_one(
            feature=fL.yingtuo_monument,
            vertical_variance=0.05,
            horizontal_variance=0.05,
            box=self.box_of_screen(0.002, 0.750, 0.990, 0.783),
        ):
            return "monument"
        if self.find_one(feature=fL.yingtuo_entrance):
            return "handbook"
        return None

    def _wait_monument(self, time_out):
        """等海报页标志连续 0.5 秒可见。"""
        return self.wait_until(
            lambda: self.find_one(
                feature=fL.yingtuo_monument,
                vertical_variance=0.05,
                horizontal_variance=0.05,
                box=self.box_of_screen(0.002, 0.750, 0.990, 0.783),
            ),
            time_out=time_out,
            settle_time=0.5,
            raise_if_not_found=False,
        )

    def _on_detail_page(self):
        """关卡详情页右下角的「挑战影拓丰碑」按钮可见时，说明仍停在详情页。"""
        return self.find_one(feature=fL.to_max_produce_num, box=self.box_of_screen(0.934, 0.881, 0.977, 0.965))

    def enter_yingtuo(self):
        self.log_info("开始影拓丰碑任务", notify=True)
        self.press_key("f8")
        find_yingtuo_entrance = False
        for _ in range(6):
            if self.wait_click_feature(feature=fL.yingtuo_entrance, time_out=2, raise_if_not_found=False):
                self.log_info("找到影拓入口", notify=True)
                find_yingtuo_entrance = True
                break
            self.send_key("e", after_sleep=0)
        if not find_yingtuo_entrance:
            self.log_info("未能找到影拓入口，任务结束", notify=True)
            return False
        if not self.wait_feature(feature=fL.yingtuo_monument, time_out=10, raise_if_not_found=False):
            self.log_info("未能找到影拓丰碑活动页标志，任务结束", notify=True)
            return False
        self.log_info("成功进入影拓丰碑页面")
        return True

    def find_normal_challenge(self, leftmost_only=False):
        """本屏任意模板已记录就滚动，否则进入首条；无灰条累计四次后结束。

        ``leftmost_only`` 为真时只看最左侧海报下方的灰条，没有就直接结束，不滚动。
        确认滚动到末尾后，改为逐组选择本屏未记录模板，直到本页全部尝试过。
        """
        empty_scrolls = 0
        clipped_stalls = 0
        previous_clipped_bars = None
        matched_stalls = 0
        previous_matched_bars = None
        at_end = False
        self._last_normal_attempt_recorded = False
        while empty_scrolls < 4:
            bars, frame = self._detect_stable_bars()
            if leftmost_only:
                bars = [bar for bar in bars if bar.center_x / frame.shape[1] < self.LEFTMOST_POSTER_MAX_X]
                if not bars:
                    self.log_info("最左侧海报没有未通关的普通关卡")
                    return None
            if not bars:
                if at_end:
                    return None
                clipped_stalls = 0
                previous_clipped_bars = None
                matched_stalls = 0
                previous_matched_bars = None
                empty_scrolls += 1
                self.log_info(f"未检测到普通关卡灰条，继续向下滚动 ({empty_scrolls}/4)")
                self.scroll_relative(0.5, 0.5, -5)
                continue
            candidates = []
            has_entered_template = False
            for bar in bars:
                template = crop_poster_template(frame, bar.center_x, bar.center_y)
                if template is None:
                    # 屏幕边缘可能只露出半张海报，不能阻断其他完整海报的扫描。
                    self.log_info("跳过无法完整截取模板的边缘海报灰条")
                    continue
                if matches_entered_template(template, self._entered_poster_templates):
                    has_entered_template = True
                    continue
                candidates.append((bar, template))
            # 正常扫描命中任意记录就跳屏；确认末尾后才允许补打同屏未尝试组。
            if has_entered_template and not at_end:
                clipped_stalls = 0
                previous_clipped_bars = None
                if leftmost_only:
                    self.log_info("最左侧活动海报本轮已尝试，不再挑战")
                    return None
                if previous_matched_bars is not None and self._same_bars(previous_matched_bars, bars, frame.shape[1]):
                    matched_stalls += 1
                else:
                    matched_stalls = 0
                previous_matched_bars = bars
                if matched_stalls >= 6:
                    self.log_info("灰条连续六次滚动未推进，按页尾重新检测剩余组合")
                    at_end = True
                    continue
                self.log_info("本屏有本轮已尝试的灰条组合，直接滚动并确认是否推进")
                if not self._scroll_poster_after_matching(frame):
                    self.log_info("滚动后海报没有变化，按页尾重新检测剩余组合")
                    at_end = True
                continue
            if at_end and not candidates and has_entered_template:
                self.log_info("页尾可完整识别的灰条组合本轮均已尝试")
                return None
            if not candidates:
                matched_stalls = 0
                previous_matched_bars = None
                # 背景动画可能让画面差异一直为真，另用灰条位置约束连续无进展。
                if previous_clipped_bars is not None and self._same_bars(previous_clipped_bars, bars, frame.shape[1]):
                    clipped_stalls += 1
                else:
                    clipped_stalls = 0
                previous_clipped_bars = bars
                if clipped_stalls >= 3:
                    raise YingTuoError("残缺海报灰条连续三次滚动未推进")
                if not leftmost_only and self._scroll_poster_after_matching(frame):
                    continue
                raise YingTuoError("灰条对应的海报模板均无法完整截取，页面状态不明")
            leftmost_bar, _ = candidates[0]
            frame_height, frame_width = frame.shape[:2]
            # 选择本屏第一条灰条时立即把整组近邻模板记为本轮已尝试：
            # 不等待详情页、协议空间和战斗结果。点击失败也不重复选这组。
            group = nearby_poster_templates(candidates, frame_width)
            self._entered_poster_templates.extend(group)
            self._last_normal_attempt_recorded = True
            self.log_info(f"选中第一条灰条，立即记录 {len(group)} 张相邻海报模板（本轮累计 {len(self._entered_poster_templates)} 张）")
            self._click_bar_until_detail(leftmost_bar, frame_height, frame_width)
            return True
        self.log_info("连续 4 次滚动未发现本轮可进入的灰条组合")
        return None

    def _scroll_poster_after_matching(self, before_frame):
        """已尝试或残缺海报向下滚动；是否到页尾根据真实画面变化判断。

        不消耗没有灰条的四次滚动预算。连续两次向下滚动后海报区域仍
        没有明显变化，视为到达末尾/卡住，不无限循环。
        """
        previous = before_frame
        for _ in range(2):
            self.scroll_relative(0.5, 0.5, -5)
            _, current = self._detect_stable_bars()
            if poster_view_changed(previous, current):
                return True
            previous = current
        return False

    def _click_bar_until_detail(self, bar, frame_height, frame_width):
        """仅重试当前灰条，不重新选择其他海报或重复添加模板。"""
        for attempt in range(1, self.CHALLENGE_CLICK_ATTEMPTS + 1):
            self.click_relative(
                bar.center_x / frame_width,
                bar.center_y / frame_height,
                after_sleep=0.25,
            )
            if self.wait_until(self._on_detail_page, time_out=self.DETAIL_PAGE_TIMEOUT, raise_if_not_found=False):
                return
            if attempt == self.CHALLENGE_CLICK_ATTEMPTS:
                break
            self.log_info(f"点击灰条后未进入详情页，重试同一灰条 ({attempt}/{self.CHALLENGE_CLICK_ATTEMPTS})")
            retry_bars, retry_frame = self._detect_stable_bars()
            if not retry_bars:
                raise YingTuoError("重试时原灰条已消失，页面状态不明")
            nearest = min(
                retry_bars,
                key=lambda item: abs(item.center_x - bar.center_x) + abs(item.center_y - bar.center_y),
            )
            if (
                abs(nearest.center_x - bar.center_x) > retry_frame.shape[1] * 0.01
                or abs(nearest.center_y - bar.center_y) > retry_frame.shape[0] * 0.01
            ):
                raise YingTuoError("重试时无法定位原灰条，防止点到其他海报")
            bar = nearest
            frame_height, frame_width = retry_frame.shape[:2]
        self.log_info("点击灰条后未进入关卡详情页")
        raise YingTuoError("点击灰条后未进入关卡详情页")

    def _detect_stable_bars(self, time_out=3.0):
        """间隔 0.2 秒两次检测结果一致才采用，返回灰条列表与所用帧。

        海报页出现后，下方关卡段约 0.55 秒才淡入完成，期间命中数在 0~5 条之间跳变；
        滚动后的动画同理。结果为空时，须从首次为空起连续为空满淡入时间才采用。
        超时仍未稳定时报错：此时画面状态不明，按最后一次结果点击可能点错，判为空可能误报没有关卡。
        """
        start = self.active_time()
        previous = None
        empty_since = None
        while True:
            frame = self.next_frame()
            bars = self.detect_normal_challenge_bars(frame)
            if bars:
                empty_since = None
            elif empty_since is None:
                empty_since = self.active_time()
            if (
                previous is not None
                and self._same_bars(previous, bars, frame.shape[1])
                and (bars or self.active_time() - empty_since >= self.EMPTY_BARS_SETTLE_TIME)
            ):
                return bars, frame
            if self.active_time() - start >= time_out:
                self.log_info(f"灰条检测 {time_out:g} 秒内未稳定，结束挑战")
                raise YingTuoError(f"灰条检测 {time_out:g} 秒内未稳定")
            previous = bars
            self.sleep(0.2)

    @staticmethod
    def _same_bars(first, second, frame_width):
        """两组灰条数量相同、中心位置相差不超过画面宽度的 1% 时视为一致。"""
        tolerance = frame_width * 0.01
        return len(first) == len(second) and all(
            abs(a.center_x - b.center_x) <= tolerance and abs(a.center_y - b.center_y) <= tolerance
            for a, b in zip(first, second, strict=True)
        )

    def detect_normal_challenge_bars(self, frame):
        """检测普通关卡灰条；启用调试叠加层时按 YOLO 的方式绘制检测框。"""
        # 六段海报的灰条实测仅宽 32–33/1920，通用默认下限 34 会漏掉整组。
        bars = detect_gray_bars(frame, min_width_ratio=30 / 1920, min_aspect_ratio=3.5)
        if self._is_debug_overlay_enabled():
            debug_boxes = []
            for index, bar in enumerate(bars, start=1):
                box = Box(bar.x, bar.y, bar.width, bar.height)
                box.name = f"yingtuo_gray_bar_{index}"
                box.confidence = 1.0
                debug_boxes.append(box)
            self.draw_boxes("yingtuo_gray_bars", debug_boxes, color="green", debug=True)
        return bars

    def battle_and_exit(self, transition_recovery=None):
        if not self.wait_battle_space(transition_recovery=transition_recovery):
            return False
        # 本轮已尝试模板在点击灰条时就已记录，进入战斗不重复登记。
        def challenge_state():
            self.next_frame()
            if self.in_combat():
                return "combat"
            return self.ocr(match=self.lang.daily_battle_mixin.k_4cc61900, box=self.box.bottom_right)

        # 入场只表示加载完成；后续关卡可能还要靠近触碰点并交互才会开战。
        state = self.strafe_search(challenge_state, keys=("w",), duration=0.25, passes=None, time_out=60)
        if not state:
            self.log_info("未找到挑战触碰点")
            return False
        if state != "combat":
            self.press_key("f")

            def challenge_started():
                self.next_frame()
                return self.in_combat()

            if not self.wait_until(challenge_started, time_out=10, raise_if_not_found=False):
                self.log_info("触碰后挑战未启动")
                return False
        if not self.auto_battle():
            return False
        if not self.wait_click_feature(
            feature=fL.left_battle,
            vertical_variance=0.1,
            time_out=10,
            settle_time=0.5,
            raise_if_not_found=False,
        ):
            self.log_info("未能退出按钮")
            return False
        return True
