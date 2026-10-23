from enum import Enum
from typing import ClassVar

from ok.util.color import calculate_color_percentage
from qfluentwidgets import FluentIcon

from src.data.FeatureList import FeatureList as fL
from src.icons import Icons
from src.tasks.mixin.common import Common
from src.tasks.mixin.liaison_mixin import LiaisonMixin
from src.tasks.navigation.mixin.map_mixin import MapMixin


class CollectResult(Enum):
    """单批收取的结果。"""

    SUCCESS = "success"
    EXHAUSTED = "exhausted"
    MISSING = "missing"


class FarmRelatedTask(Common, LiaisonMixin, MapMixin):
    # 地点页签选中时是近白底、未选中近黑，按浅色像素占比判断，不依赖 OCR
    WULING_TAB_BOX = (0.765, 0.896, 0.856, 0.939)
    WULING_TAB_CENTER = (0.810, 0.918)
    TAB_SELECTED_COLOR: ClassVar[dict] = {"r": (180, 255), "g": (180, 255), "b": (180, 255)}

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
        if not self._wuling_tab_selected():
            self.click(*self.WULING_TAB_CENTER, after_sleep=1)
            if not self.wait_until(self._wuling_tab_selected, time_out=5, raise_if_not_found=False):
                self.mark_task_failure("切换到武陵地图失败")
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

    def _wuling_tab_selected(self):
        """当前地图是否已切到武陵。

        Returns:
            bool: 武陵页签为选中态返回 True。
        """
        box = self.box_of_screen(*self.WULING_TAB_BOX)
        return calculate_color_percentage(self.frame, self.TAB_SELECTED_COLOR, box) > 0.5

    def _collect_to_wuling_warehouse(self, source_name, max_misses=2, max_batches=5):
        """反复点「存至武陵仓库」，直到确认框不再出现。

        一次点击只收一批，故按批循环；某批连续点击都不弹确认框即判定收完。

        Args:
            source_name: 日志用的来源名。
            max_misses: 每批最多点几次；点击可能没命中，故允许重试。
            max_batches: 收取批数上限，防止确认框被误识别成一直存在。

        Returns:
            bool: 正常收完为 True；找不到按钮为 False。
        """
        collected = 0
        for _ in range(max_batches):
            result = self._collect_once(source_name, max_misses)
            if result is CollectResult.MISSING:
                self.log_info(f"{source_name}收取失败，已收取{collected}批")
                return False
            if result is CollectResult.EXHAUSTED:
                break
            collected += 1

        self.log_info(f"{source_name}收取结束，共收取{collected}批")
        return True

    def _collect_once(self, source_name, max_misses=2) -> CollectResult:
        """点「存至武陵仓库」并等待确认框，收一批。

        点击后没弹确认框可能是没点中，故最多重试 max_misses 次。

        Args:
            source_name: 日志用的来源名。
            max_misses: 最多点几次。

        Returns:
            CollectResult: SUCCESS 收到一批；EXHAUSTED 点满次数仍无确认框；MISSING 找不到按钮。
        """
        for attempt in range(max_misses):
            if not self.wait_click_feature(
                feature=fL.sentence_part_feature,
                box=self.box_of_screen(0.93, 0.76, 0.98, 0.84),
                time_out=5,
                raise_if_not_found=False,
                after_sleep=0.5,
            ):
                self.mark_task_failure(f"未找到{source_name}的存至武陵仓库按钮")
                return CollectResult.MISSING

            popped = self.wait_pop_up(time_out=5)
            self.log_debug(f"{source_name}第{attempt + 1}次点击：确认框{'出现' if popped else '未出现'}")
            if popped:
                return CollectResult.SUCCESS
        return CollectResult.EXHAUSTED

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
