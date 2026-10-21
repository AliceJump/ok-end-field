from qfluentwidgets import FluentIcon

from src.data.FeatureList import FeatureList as fL
from src.data.world_map_utils import get_world_map_matcher
from src.icons import Icons
from src.tasks.mixin.common import Common
from src.tasks.mixin.liaison_mixin import LiaisonMixin
from src.tasks.navigation.mixin.map_mixin import MapMixin


class FarmRelatedTask(Common, LiaisonMixin, MapMixin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "农牧相关"
        self.icon = Icons.Collect
        self.group_name = "日常任务"
        self.group_icon = FluentIcon.CALENDAR
        self.description = "『祀的画』中农牧领取。"
        self.support_multi_account = True

    def run(self):  # 执行农牧标记筛选
        if not self.transfer_to_home_point():  # 进入帝江号
            self.mark_task_failure("进入帝江号失败，无法筛选农牧标记")
            return False

        self.ensure_map()  # 打开地图界面
        self.log_info("从帝江号地图打开地区总览")
        if not self.wait_feature(
            feature=fL.boat_entrance_icon,
            threshold=0.6,  # 在帝江号时，需降低帝江号的匹配阈值
            time_out=10,
            pre_action=lambda: self.click(1770 / 1920, 135 / 1080, interval=3),
            raise_if_not_found=False,
        ):
            self.mark_task_failure("未识别到地区总览的帝江号入口，停止后续地图操作")
            return False

        self.wait_ui_stable()
        self.log_info("切换到武陵地图")
        # 右下角点击武陵切换至武陵地图
        if not self.wait_click_ocr(
            match=get_world_map_matcher(self.lang, "武陵"),
            box=self.box_of_screen(0.74, 0.89, 0.98, 0.95),
            time_out=5,
            raise_if_not_found=False,
            after_sleep=2,
        ):
            self.mark_task_failure("未识别到武陵地图页签")
            return False
        self.wait_ui_stable()

        self.log_info("进入『祀的画』")
        # 点击『祀的画』，直到确认左下角地图筛选入口出现
        if not self.wait_feature(
            feature=fL.map_filter_icon,
            box=self.box_of_screen(0.10, 0.90, 0.14, 0.94),
            time_out=10,
            pre_action=lambda: self.click(1668 / 1920, 250 / 1080, after_sleep=2, interval=3),
            raise_if_not_found=False,
        ):
            self.mark_task_failure("未进入祀的画地图，未找到标记筛选入口")
            return False

        if not self._click_farm_related():  # 清空其他标记并只选农牧相关
            self.mark_task_failure("筛选农牧相关标记失败")
            return False

        self.log_info("农牧相关标记筛选完成，点击牧场图标")
        # 地图中间等待并点击牧场地图模板
        if not self.wait_click_feature(
            feature=fL.ranch,
            box=self.box_of_screen(0.30, 0.20, 0.65, 0.65),
            time_out=5,
            raise_if_not_found=False,
        ):
            self.mark_task_failure("未找到牧场地图图标")
            return False
        self.wait_ui_stable()
        if not self._collect_to_wuling_warehouse("牧场"):
            return False
        # 地图中间等待并点击自动农植机地图图标
        if not self.wait_click_feature(
            feature=fL.auto_farmer_on_map,
            box=self.box_of_screen(0.30, 0.20, 0.65, 0.65),
            time_out=5,
            raise_if_not_found=False,
            after_sleep=1,
        ):
            self.mark_task_failure("未找到自动农植机地图图标")
            return False
        self.log_info("点击自动农植机地图图标")
        self.wait_ui_stable()
        self.log_info("选择自动农植机")
        # 等待并点击列表中的自动农植机
        if not self.wait_click_feature(
            feature=fL.auto_farmer,
            vertical_variance=1,
            time_out=5,
            raise_if_not_found=False,
            after_sleep=1,
        ):
            self.mark_task_failure("未找到自动农植机列表按钮")
            return False
        self.wait_ui_stable()
        return self._collect_to_wuling_warehouse("自动农植机")

    def _collect_to_wuling_warehouse(self, source_name):
        """
        统一处理牧场和自动农植机的收取
        """
        self.log_info(f"开始点击{source_name}的存至武陵仓库按钮")
        # 五次收取点击
        for _ in range(5):
            # 识别仓库存放按钮右端圆角并点击
            if not self.wait_click_feature(
                feature=fL.sentence_part_feature,
                box=self.box_of_screen(0.93, 0.76, 0.98, 0.84),
                time_out=5,
                raise_if_not_found=False,
                after_sleep=0.5,
            ):
                self.mark_task_failure(f"未找到{source_name}的存至武陵仓库按钮")
                return False
            elif not self.wait_pop_up(time_out=5):
                self.log_info(f"{source_name}未找到确认按钮，收取结束")
                return True

        self.log_info(f"已点击{source_name}的存至武陵仓库按钮")
        return True

    def _click_farm_related(self):
        """
        清理地图标记筛选，保留农牧相关。参考了 map_mixin.py 中的 clear_icon_in_map 方法。
        """

        # 打开标记显示管理
        if not self.wait_click_feature(
            feature=fL.map_filter_icon,
            time_out=10,
            raise_if_not_found=False,
        ):
            return False

        # 先清空选中，确保随后点击农牧相关会将其勾选
        if not self.wait_click_feature(
            feature=fL.to_max_produce_num,
            box=self.box_of_screen(0.117, 0.902, 0.141, 0.941),
            time_out=10,
            raise_if_not_found=False,
        ):
            return False

        for _ in range(5):  # 向下滑动 5 次
            # 农牧相关在最下方，先向下滑动再查找
            self.scroll_relative(0.1, 0.5, -1)
            # 左侧列表寻找农牧相关图标
            if self.wait_click_feature(
                feature=fL.farm_related_icon,
                box=self.box_of_screen(0.025, 0.065, 0.145, 0.88),
                time_out=2,
                raise_if_not_found=False,
                after_sleep=0.5,
            ):
                # 退出标记管理界面
                self.back()
                self.wait_ui_stable()
                return True

        return False
