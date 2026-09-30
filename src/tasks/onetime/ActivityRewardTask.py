from qfluentwidgets import FluentIcon

from src.data.FeatureList import FeatureList as fL
from src.icons import Icons
from src.tasks.mixin.common import Common
from src.tasks.mixin.mouse_scan_mixin import MouseScanMixin


class ActivityRewardTask(Common, MouseScanMixin):
    """活动奖励子任务：每周事务/理智补给/刮刮乐，日常任务经 DailyFeature 接入。"""

    ACTIVITY_REWARDS = ["周常奖励", "理智补给", "刮刮乐"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "活动奖励"
        self.icon = Icons.Collect
        self.group_name = "日常任务"
        self.group_icon = FluentIcon.CALENDAR
        self.description = "领取活动中心奖励：每周事务、理智补给与刮刮乐。"
        self.support_multi_account = True
        self.default_config.update(
            {
                "⭐活动奖励": self.ACTIVITY_REWARDS,
            }
        )
        self.config_type["⭐活动奖励"] = {
            "type": "multi_selection",
            "options": self.ACTIVITY_REWARDS,
        }
        self.config_description.update(
            {
                "⭐活动奖励": (
                    "选择要领取的活动奖励：\n"
                    "周常奖励：领取每周事务奖励。\n"
                    "理智补给：领取理智补给。\n"
                    "刮刮乐：执行刮刮乐。"
                ),
            }
        )

    def claim_weekly_rewards(self):
        self.log_info("开始领取每周事务")

        if self.wait_click_ocr(match=self.lang.daily_routine_mixin.k_13eea5dd, box=self.box.left, time_out=5):
            self.log_info("进入『每周事务』页面")
            if self.wait_click_feature(
                feature=fL.week_task_collect, box=self.box.top_right, time_out=5, raise_if_not_found=False
            ):
                if self.wait_click_feature(
                    feature=fL.week_reward_collect, box=self.box.bottom_right, time_out=5, raise_if_not_found=False
                ):
                    self.wait_pop_up()
                    self.log_info("已领取『每周事务』奖励")
                else:
                    self.log_info("未找到『每周事务/一键领取』按钮")
            else:
                self.log_info("未找到『每周事务/领取』按钮")
        else:
            self.log_info("未找到『活动中心/每周事务』入口")

        return True

    def claim_sanity_supply(self):
        self.log_info("开始领取理智补给")

        if not self.wait_click_ocr(match=self.lang.daily_routine_mixin.k_059a808c, box=self.box.left, time_out=2):
            self.log_info("未找到『活动中心/理智补给』入口")
            return False

        self.log_info("进入『理智补给』页面")
        if self.wait_click_ocr(
            match=self.lang.daily_routine_mixin.k_39d12e73_1,
            box=self.box_of_screen(0.894, 0.648, 0.995, 0.991),
            time_out=5,
        ):
            self.wait_pop_up()
            self.log_info("已领取『理智补给』奖励")
            return True

        self.log_info("未找到『理智补给/领取』按钮")
        return False

    def scratch_reward(self):
        start_time = self.active_time()

        while True:
            # 页面判断模板允许横向偏移，纵向不需要放宽（vertical_variance 保持默认 0）
            if self.wait_feature(
                feature=fL.in_scratch_card_page, horizontal_variance=0.1, raise_if_not_found=False, time_out=2
            ):
                break

            if self.active_time() - start_time > 20:
                self.log_info("进入刮刮卡页面超时")
                return False

            if not self.wait_click_feature(
                feature=fL.scratch_card_icon,
                box=self.box_of_screen(0.089, 0.130, 0.176, 0.983),
                raise_if_not_found=False,
                time_out=2,
            ):
                return False

        if not self.wait_click_feature(feature=fL.start_scratch_card, raise_if_not_found=False, time_out=5):
            self.log_info("没找到开始刮刮乐的按钮")
            return False
        if not self.wait_feature(feature=fL.scratching_icon, raise_if_not_found=False, time_out=5):
            self.log_info("没点到刮刮乐")
            return False
        self.wait_ui_stable(refresh_interval=1)
        self.log_info("开始刮")
        self.drag_scan_area((0.218, 0.376), (0.787, 0.700))
        self.wait_pop_up()

    def claim_activity_rewards(self):
        self.info_set("current_task", "claim_activity_rewards")
        self.log_info("开始领取活动页奖励")

        self.press_key("f7")
        self.log_info("按下 F7 打开活动中心")

        enabled_rewards = self.config.get("⭐活动奖励", [])
        # 迁移后 ⭐活动奖励 恒为列表；非列表时按未启用处理，不再应用旧布尔开关值。
        if not isinstance(enabled_rewards, list):
            enabled_rewards = []
        enabled_rewards = set(enabled_rewards)
        weekly_enabled = "周常奖励" in enabled_rewards
        sanity_enabled = "理智补给" in enabled_rewards
        scratch_enabled = "刮刮乐" in enabled_rewards

        if weekly_enabled:
            self.claim_weekly_rewards()
        else:
            self.log_info("已关闭『周常奖励』，跳过")

        if scratch_enabled:
            self.scratch_reward()
        else:
            self.log_info("已关闭『刮刮乐』，跳过")

        if sanity_enabled:
            self.claim_sanity_supply()
        else:
            self.log_info("已关闭『理智补给』，跳过")

        return True

    def run(self):
        self.ensure_main(time_out=420)
        return self.claim_activity_rewards()
