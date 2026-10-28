from qfluentwidgets import FluentIcon

from src.data.FeatureList import FeatureList as fL
from src.data.world_map import areas_list
from src.icons import Icons
from src.tasks.mixin.common import Common


class DeliverySendTask(Common):
    """转交运送委托子任务，日常任务经 DailyFeature 接入。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "转交运送委托"
        self.icon = Icons.Deliver
        self.group_name = "日常任务"
        self.group_icon = FluentIcon.CALENDAR
        self.description = "在「地区建设/仓储结点」中转交全部运送委托，并领取转交委托奖励。"

    def blind_spot_speed_up(self, feature, box=None, time_out=12, click_interval=0.05):
        """装货过场动画期间快速连点屏幕中心，直到目标按钮出现。

        Args:
            feature: 目标按钮特征名。
            box: 特征识别区域。
            time_out: 总超时时间。
            click_interval: 每次连点后的等待间隔。

        Returns:
            bool: 找到目标按钮返回 True，超时返回 False。
        """
        start_time = self.active_time()
        while True:
            if self.active_time() - start_time > time_out:
                self.log_info(self.tr("盲点加速超时，未找到 {feature}").format(feature=feature))
                return False
            if self.find_feature(feature=feature, box=box):
                return True
            self.click(x=0.5, y=0.5, down_time=0.01)
            self.sleep(click_interval)

    def _claim_delivery_rewards_in_current_node(self):
        """在已进入的仓储节点中领取一次转交委托奖励。

        仓储节点内的「我转交的委托」「一键领取」「本地仓储节点」等
        均为同一级页面（上有分类、下有页面的界面），无需返回总览。

        Returns:
            bool: 领取流程到达完成状态返回 True（已领取或确认无奖励）；
                  未找到节点或确认失败返回 False，
                  便于调用方在后续地区重试。
        """
        self.log_info("开始领取转交委托奖励")
        if not self.wait_click_ocr(match=self.lang.daily_routine_mixin.k_41a9fd98, box=self.box.top_left, time_out=5):
            self.log_info("未找到「我转交的委托」节点，跳过领取")
            return False

        results = self.wait_ocr(
            match=self.lang.daily_routine_mixin.k_bf856c96,
            box=self.box.bottom_right,
            time_out=5,
        )

        if not results:
            # 已进入节点且确认无奖励，视为该节点的领取流程完成。
            self.log_info("当前没有可领取的转交委托奖励")
            return True

        self.click(results)
        if not self.wait_pop_up():
            self.log_info("未找到 '确认' 按钮，可能未成功领取奖励")
            return False

        # 停在「我转交的委托」页，后续可直接切换到本地仓储节点。
        self.log_info("转交委托奖励领取完成")
        return True

    def delivery_send_others(self):
        self.info_set("current_task", "delivery_send_others")

        send_count = 0
        claim_rewards_done = False

        for area in areas_list:
            activity_num = 0
            count = 0
            self.log_info(self.tr("开始处理区域: {area}").format(area=self.tr(area)))

            if not self.to_model_area(area, "仓储节点"):  # 每个地区只导航一次
                self.ensure_main()  # 导航失败后恢复，再尝试下一地区
                continue  # 不在未知页面继续转交

            # 仅在领取流程真正完成时才置位，失败留到后续地区重试。
            if not claim_rewards_done and self._claim_delivery_rewards_in_current_node():
                claim_rewards_done = True

            while True:
                if 0 < activity_num <= count:
                    self.log_info(
                        self.tr("{area}仓储节点已完成{count}次，停止继续").format(
                            area=self.tr(area), count=activity_num
                        )
                    )
                    break

                if not self.wait_click_ocr(  # 等待本地仓储标签出现后切回列表
                    match=self.lang.daily_routine_mixin.k_298d3284, box=self.box.top_left, time_out=5
                ):
                    self.log_info(self.tr("{area}未找到本地仓储节点，返回主界面").format(area=self.tr(area)))
                    break

                results = self.wait_ocr(
                    match=[self.lang.daily_routine_mixin.k_573c7c18, self.lang.daily_routine_mixin.k_8f2058a8],
                    box=self.box.bottom,
                    time_out=5,
                )

                if not results:
                    self.log_info(self.tr("{area} 当前没有货物装箱可操作，返回主界面").format(area=self.tr(area)))
                    break

                if activity_num == 0:
                    activity_num = len(results)
                    self.log_info(
                        self.tr("{area}共有{activity_num}次可进行转交运送委托的活动").format(
                            area=self.tr(area), activity_num=activity_num
                        ),
                        notify=True,
                    )

                self.click(results[0])
                steps = [
                    (fL.give_gift, self.box_of_screen(0.945, 0.904, 0.965, 0.937), 0),
                    (fL.fill_max, None, 0),
                    (fL.give_gift, self.box_of_screen(0.945, 0.904, 0.965, 0.937), 1),
                    # 黄键出现说明已进入报价列表页，只作判据，不点击
                    (fL.skip_dialog_confirm, self.box_of_screen(0.82, 0.88, 0.98, 0.96), 0),
                    (fL.transfer_go, self.box_of_screen(0.645, 0.885, 0.815, 0.955), 0),
                ]
                # 「查看报价」已直接打开报价列表，无需再装箱或等待装货动画。
                start_index = len(steps) - 1 if self.lang.daily_routine_mixin.k_view_quote in results[0].name else 0
                blind_spot_steps = {3}

                sent = False
                for i in range(start_index, len(steps)):
                    feature, box, after_sleep = steps[i]
                    time_out = 12 if i > 2 else 5
                    if i in blind_spot_steps:
                        self.log_info("盲点加速：装货过场动画期间快速连点屏幕中心")
                        if not self.blind_spot_speed_up(feature=feature, box=box, time_out=time_out):
                            self.log_info(self.tr("步骤 {feature} 未找到，跳过本次活动").format(feature=feature))
                            break
                        continue
                    res = self.wait_click_feature(
                        feature=feature,
                        click_after_delay=0.5,
                        box=box,
                        time_out=time_out,
                        raise_if_not_found=False,
                        after_sleep=after_sleep,
                    )

                    if not res:
                        self.log_info(self.tr("步骤 {feature} 未找到，跳过本次活动").format(feature=feature))
                        break
                    if i == len(steps) - 1:
                        self.click_confirm()
                        if self.wait_pop_up():
                            sent = True
                            self.log_info("转交完成，已领取奖励弹窗")
                        else:
                            self.log_info("未等到奖励弹窗，跳过本次活动")
                if not sent:
                    break
                count += 1
                send_count += 1
                self.log_info(f"{area} 已完成 {count}/{activity_num} 次转交")
            self.ensure_main()  # 本地区结束或失败时统一恢复，兼顾地区切换与任务收尾
        if send_count == 0:
            self.mark_task_failure("未完成任何转交运送委托操作")
            return False
        return True

    def run(self):
        self.ensure_main(time_out=420)
        return self.delivery_send_others()
