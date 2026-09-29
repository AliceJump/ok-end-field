from qfluentwidgets import FluentIcon

from src.data.FeatureList import FeatureList as fL
from src.icons import Icons
from src.tasks.mixin.common import Common
from src.tasks.mixin.liaison_mixin import LiaisonMixin


class BoatOrganizeTask(Common, LiaisonMixin):
    """帝江号整理子任务：一键存放与简易制作，日常任务经 DailyFeature 接入。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "帝江号整理"
        self.icon = Icons.ItemTransfer
        self.group_name = "日常任务"
        self.group_icon = FluentIcon.CALENDAR
        self.description = "在「帝江号」打开背包并执行「一键存放」与「简易制作」。"
        self.support_multi_account = True
        self.default_config.update(
            {
                "⭐帝江号一键存放": False,
                "⭐简易制作": True,
            }
        )
        self.config_description.update(
            {
                "⭐帝江号一键存放": (
                    "是否在「帝江号」打开背包并点击「一键存放」。\n"
                    "与「简易制作」合并执行，共享传送与开背包。\n"
                    "确认不会自动存可用道具导致治疗药被存入后再开启"
                ),
                "⭐简易制作": (
                    "是否前往「帝江号/简易制作」制作物品。\n与「帝江号一键存放」合并执行，共享传送与开背包。"
                ),
            }
        )

    def boat_organize(self):
        """帝江号整理：合并「一键存放」与「简易制作」，共享传送与开背包。

        传送至帝江号并打开背包后，按配置依次执行一键存放、简易制作。
        按 B 打开背包后，一键存放与简易制作入口在同一页面，点击一键存放不会跳转页面。
        """
        self.info_set("current_task", "boat_organize")
        do_store = self.config.get("⭐帝江号一键存放", False)
        do_craft = self.config.get("⭐简易制作", False)
        if not do_store and not do_craft:
            self.log_info("帝江号整理：一键存放与简易制作均未启用，跳过")
            return True

        if not self.transfer_to_home_point(should_check_out_boat=True):
            self.mark_task_failure("传送到帝江号失败，无法执行帝江号整理")
            return False

        # 打开背包：一键存放与简易制作入口在同一页面，只按一次 B
        self.press_key("b")

        ok = True
        # 一键存放：点击「存放」，不会跳转页面
        if do_store:
            store_btn = self.wait_ocr(
                box=self.box_of_screen(0.64, 0.705, 0.69, 0.735, name="onekey_store_area"),
                match=self.lang.daily_liaison_mixin.k_d661f6da,
                time_out=5,
            )
            if not store_btn:
                self.log_info("未找到“存放”按钮")
                ok = False
            else:
                self.click(store_btn[0], after_sleep=0.5)

        # 简易制作：复用已打开的背包页面，不再按 B
        if do_craft:
            if not self._make_simply_from_backpack():
                return False

        return ok

    def _make_simply_from_backpack(self):
        """在帝江号（背包已打开）执行简易制作，前置：位于帝江号且背包已打开。"""
        if not self.wait_click_feature(
            feature=fL.make_simply_entrance, settle_time=1, time_out=5, raise_if_not_found=False
        ):
            self.mark_task_failure("未能找到简易制作入口")
            return False
        if not self.wait_click_ocr(
            match=self.lang.daily_routine_mixin.k_cdb1d49b, box=self.box.left, time_out=5, log=True
        ):
            self.log_info("未能选定可制作的物品")
        if self.wait_click_feature(
            feature=fL.to_max_produce_num,
            box=self.box_of_screen(0.938, 0.902, 0.964, 0.941),
            time_out=5,
            raise_if_not_found=False,
        ):
            self.wait_pop_up()
        else:
            self.mark_task_failure("未能找到简易制作按钮")
            return False
        return True

    def run(self):
        self.ensure_main(time_out=420)
        return self.boat_organize()
