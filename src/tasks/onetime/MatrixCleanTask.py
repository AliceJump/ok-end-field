from qfluentwidgets import FluentIcon

from src.data.FeatureList import FeatureList as fL
from src.icons import Icons
from src.tasks.mixin.common import Common


class MatrixCleanTask(Common):
    """清理基质子任务：批量分解武器基质直到无可分解，日常任务经 DailyFeature 接入。

    流程（对应游戏内「贵重品库/武器基质」手动分解）：
    1. N 键直达贵重品库，确认停在武器基质页；
    2. 点击底栏左侧垃圾桶图标进入分解模式；每分解一批后游戏自动退回
       浏览模式，分解模式内的快捷放入规则不跨会话保留，每批重新设置；
    3. 点击「快捷放入」弹出筛选面板：品质三项（稳定/净态/高纯，初始为空，
       逐项点勾 = 全部可分解基质；无瑕不可分解不在选项内）与锁定状态
       「未上锁」，保存——保存即按筛选结果勾选物品并关闭面板；
    4. 点「分解」→ 确认弹窗「确认」→ 关闭「分解获得」结果弹窗，游戏
       自动退回浏览模式，循环下一批；
    5. 结束判定：批开始分解网格出现「未拥有该类型道具」、保存筛选后
       按钮为「无法分解」，均表示无可分解剩余，直接结束循环。批开始
       不能按按钮文字判断——未勾选物品（已选择 0/200）时按钮恒为
       「无法分解」。
    """

    # 单批上限 200、库存上限 2000 对应最多 10 批；翻倍余量用于拦截异常循环
    MAX_BATCHES = 20

    # 底栏控件区（快捷放入 / 分解按钮 / 退出）
    _BOTTOM_BAR = (0.0, 0.80, 0.6, 0.98)
    _BOTTOM_RIGHT = (0.55, 0.75, 1.0, 0.98)
    # 筛选面板保存按钮图标搜索区
    _FILTER_SAVE_ICON = (0.78, 0.72, 0.86, 0.8)

    # 底栏左侧分解入口（垃圾桶图标）中心：品质下拉/降序/漏斗之后
    _DISMANTLE_ENTRY = (0.259, 0.93)
    # 选项背景的无文字区域；选中为浅灰，未选中为深色。
    _FILTER_SWATCHES = ((0.27, 0.355), (0.48, 0.355), (0.69, 0.355), (0.27, 0.505))
    _FILTER_LIGHT = {"r": (140, 255), "g": (140, 255), "b": (140, 255)}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "清理基质"
        self.icon = Icons.Fetch
        self.group_name = "日常任务"
        self.group_icon = FluentIcon.CALENDAR
        self.description = "前往「贵重品库/武器基质」分解全部可分解基质，直到无可分解。\n分解产出基建材料与调度券。"
        self.support_multi_account = True

    def run(self):
        self.ensure_main(time_out=420)
        return self.clean_matrix()

    def clean_matrix(self):
        """分解主流程：进贵重品库 → 逐品质循环分解到无可分解 → 关闭贵重品库。"""
        if not self._open_vault_essence_page():
            self.ensure_main(time_out=420)
            return False
        success = self._dismantle_by_quality()
        self._close_vault()
        return success

    def _open_vault_essence_page(self):
        """打开贵重品库并确认停在武器基质页。"""
        for _ in range(2):
            # 前台按键被前台锁拒绝时 send_key 返回 False，提前失败比空等 OCR 超时更直接
            if not self.press_key("n", after_sleep=1.5):
                self.mark_task_failure("按键注入失败：无法把游戏切到前台（请从 ok-ef.exe 正常启动）")
                return False
            if self._ensure_essence_page():
                return True
        self.mark_task_failure("进入武器基质页失败")
        return False

    def _ensure_essence_page(self):
        """确认停在武器基质页；停在其他分页时点击顶部基质分页。"""
        # 基质页签选中时背景为黄色，取避开图标的区域检测和点击。
        return self.click_until_color(
            {"r": (220, 255), "g": (200, 255), "b": (0, 90)},
            self.box_of_screen(0.374, 0.01, 0.384, 0.025),
            threshold=0.8,
            time_out=4,
            after_sleep=1.5,
            raise_if_not_found=False,
        )

    def _dismantle_by_quality(self):
        """循环分解直到无可分解；返回 True 表示正常结束。"""
        for batches in range(self.MAX_BATCHES):
            if not self._enter_dismantle_mode():
                return False
            result = self._dismantle_one_batch()
            if result is None:
                self._exit_dismantle_mode()
                return False
            if not result:
                self._exit_dismantle_mode()
                self.log_info(self.tr("基质清理完成，共分解 {count} 批").format(count=batches), notify=True)
                return True
        self.mark_task_failure(self.tr("分解批数达到安全上限 {max}，停止清理").format(max=self.MAX_BATCHES))
        return False

    def _enter_dismantle_mode(self):
        """进入分解模式；已在分解模式（上批未正常自动退出）则直接继续。"""
        if self.ocr(match=self.lang.matrix_clean_task.k_83c0c24d, box=self.box.bottom):
            return True
        self.click_relative(*self._DISMANTLE_ENTRY, after_sleep=1)
        hint = self.wait_ocr(
            match=self.lang.matrix_clean_task.k_83c0c24d,
            box=self.box.bottom,
            time_out=6,
            raise_if_not_found=False,
        )
        if not hint:
            self.mark_task_failure("未进入分解模式")
            return False
        return True

    def _dismantle_one_batch(self):
        """执行一批「设置筛选 → 分解 → 确认 → 关结果」。

        首次进入分解模式无快捷放入规则，点「快捷放入」会弹出筛选面板；
        面板「保存」即按筛选结果勾选物品并关闭面板，
        保存后直接点分解，无需再点快捷放入。

        Returns:
            True: 本批分解完成。
            False: 已无可分解（正常结束信号）。
            None: 流程异常。
        """
        # 批开始先查网格空标志：无可分解时分解网格显示「未拥有该类型
        # 道具」（不先查则空库仍要走完快捷放入+筛选+保存，约六秒）。此处
        # 不能按按钮文字判断：未勾选物品（已选择 0/200）时按钮恒为
        # 「无法分解」，正常情况也一样
        if self._no_item_left():
            return False
        if not self.wait_click_ocr(
            match=self.lang.matrix_clean_task.k_52e36ffd,
            box=self.box_of_screen(*self._BOTTOM_BAR),
            time_out=6,
        ):
            self.mark_task_failure("未找到快捷放入按钮")
            return None
        # 等待筛选界面的保存图标，兼容面板渲染延迟。
        if (
            self.wait_feature(
                fL.transfer_go,
                box=self.box_of_screen(*self._FILTER_SAVE_ICON),
                threshold=0.8,
                time_out=2,
                raise_if_not_found=False,
            )
            and not self._apply_filter()
        ):
            return None
        return self._confirm_dismantle()

    def _apply_filter(self):
        """在筛选面板勾选全部三个品质与「未上锁」并保存。

        面板弹出时品质三项初始为空，需逐项点勾（对应手动操作把
        稳定/净态/高纯全部勾上 = 全部可分解基质）。
        """
        save_box = self.box_of_screen(*self._FILTER_SAVE_ICON)
        if not self.wait_feature(fL.transfer_go, box=save_box, threshold=0.8, time_out=3, raise_if_not_found=False):
            self.mark_task_failure("筛选面板未弹出")
            return False
        for x, y in self._FILTER_SWATCHES:
            # 选项空白区域用于检测背景，点击该区域直到选中。
            box = self.box_of_screen(x, y, x + 0.02, y + 0.015)
            if not self.click_until_color(self._FILTER_LIGHT, box, time_out=3, after_sleep=0.3):
                self.mark_task_failure("筛选选项未选中")
                return False
        if not self.wait_click_feature(
            fL.transfer_go, box=save_box, threshold=0.8, time_out=4, raise_if_not_found=False
        ):
            self.mark_task_failure("未找到筛选保存按钮")
            return False
        self.sleep(1)
        return True

    def _confirm_dismantle(self):
        """点击分解并走完确认与结果弹窗（完成后游戏自动退回浏览模式）。

        保存筛选后按钮状态即定：无可分解保持「无法分解」，先行识别直接
        结束循环，不空等「分解」按钮（否则空转一轮要等满 4 秒超时）。
        """
        if self._dismantle_disabled():
            return False
        # 亮起的「分解」按钮右侧图标与确认弹窗「确认」相同，复用同一模板；
        # 灰色「无法分解」不带该图标
        if not self.wait_click_feature(
            fL.skip_dialog_confirm,
            box=self.box_of_screen(*self._BOTTOM_RIGHT),
            time_out=4,
            raise_if_not_found=False,
        ):
            # 保存后按钮仍可能在刷新，复查一次再定性
            if self._dismantle_disabled():
                return False
            self.mark_task_failure("未找到分解按钮")
            return None
        # 弹窗出现后底栏「分解」按钮仍带同一图标；click_confirm 只在弹窗
        # 「确认」的标注位置附近查找，不会点回底栏
        if not self.click_confirm():
            self.mark_task_failure("未找到分解确认按钮")
            return None
        # 弹窗未关闭时不能把本批计为成功：下一批会顶在未关闭的弹窗上操作
        if not self._close_result_popup():
            return None
        return True

    def _dismantle_disabled(self):
        """无可分解判定：按钮为「无法分解」或网格「未拥有该类型道具」。

        连查两次防保存后按钮状态刷新的竞态。
        """
        right_box = self.box_of_screen(*self._BOTTOM_RIGHT)
        for _ in range(2):
            if self._no_item_left() or self.ocr(
                match=self.lang.matrix_clean_task.k_9c61417d,
                box=right_box,
            ):
                return True
            self.sleep(0.5)
        return False

    def _no_item_left(self):
        """结束标志：分解网格出现「未拥有该类型道具」。"""
        return bool(
            self.ocr(
                match=self.lang.matrix_clean_task.k_75a84eb1,
                box=self.box_of_screen(0.2, 0.2, 0.8, 0.8),
            )
        )

    def _close_result_popup(self):
        """关闭「分解获得」结果弹窗；返回是否确认已关闭。

        确认后 8 秒未见弹窗视作分解未完成（确认点击可能未落上）。
        wait_pop_up 点击对勾后不查弹窗是否关闭，对勾仍在就补点；
        共点四次后弹窗仍在同样交由上层判失败。
        """
        if not self.wait_pop_up(time_out=8, after_sleep=1):
            self.mark_task_failure("分解结果弹窗未出现")
            return False
        for _ in range(3):
            ok_button = self._result_popup_ok()
            if not ok_button:
                return True
            self.click(ok_button, after_sleep=1)
        if not self._result_popup_ok():
            return True
        self.mark_task_failure("分解结果弹窗未能关闭")
        return False

    def _result_popup_ok(self):
        """「分解获得」弹窗底部的对勾；弹窗已关闭时返回 None。"""
        return self.find_one(fL.reward_ok, box=self.box.bottom, threshold=0.8)

    def _exit_dismantle_mode(self):
        """退出分解模式（品质清空或异常中断时）；失败交由后续步骤兜底。"""
        self.wait_click_ocr(
            match=self.lang.matrix_clean_task.k_feecb1e6,
            box=self.box_of_screen(*self._BOTTOM_RIGHT),
            time_out=5,
            raise_if_not_found=False,
        )
        self.sleep(0.5)

    def _close_vault(self):
        self.press_key("esc", after_sleep=1)


class MatrixCleanOnceTask(MatrixCleanTask):
    """「业务与战斗 → 其他」的独立单次运行入口，复用清理基质全流程。

    与 MatrixCleanTask（日常一条龙勾选行）是两个实例，配置互不影响；
    框架按 group_name 单页签分组，故以子类提供第二个运行入口。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "⭐清理基质"
