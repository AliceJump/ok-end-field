import re
from src.core.BaseEfTask import BaseEfTask
from src.data.FeatureList import FeatureList as fL

class MapMixin(BaseEfTask):
    def task_to_transfer_point(self, need_location_list=None):
        """
        传送到运输委托对应的出发传送点。

        流程：
        1. 确保当前在主界面。
        2. 按 J 打开任务界面。
        3. 点击“任务定位到地图”的按钮。
        4. 等待地图稳定。
        5. 传送至附近传送点。

        Args:
            need_location_list: 需要记录的候选地名列表（本地化名称）；
                地图稳定后 OCR 右上角，命中则把当前地区名记录到 self.location

        Returns:
            bool:
                True  - 成功执行传送
                False - 任一步骤失败
        """
        # 确保当前处于主界面
        self.ensure_main()

        # 打开任务界面
        self.press_key("j")

        # 查找“任务定位到地图”按钮
        result = self.wait_feature(
            feature="one_task_to_map",
            threshold=0.8,
            box=self.box.bottom_right,
            time_out=4,
            raise_if_not_found=False,
        )

        # 如果没有找到按钮，则流程失败
        if not result:
            return False

        # 点击按钮跳转到地图
        self.click(result)

        map_loaded = self.wait_until(
            lambda: (
                self.find_one(fL.in_map, box=self.box_of_screen(0.027, 0.531, 0.051, 0.896))
                or self.find_one(fL.transaction_icon)
                or self.find_one(fL.main_centre_icon)
            ),
            time_out=10,
            raise_if_not_found=False,
        )
        if not map_loaded:
            return False

        # 地图状态出现后，再等待本帧 UI 稳定。
        self.wait_ui_stable(refresh_interval=1)

        # 执行附近传送点传送
        return self.to_near_transfer_point(after_track=False, need_location_list=need_location_list)

    def clear_icon_in_map(self, need_reserve_icon_name=None, ocr=False):
        """
        清理地图标记筛选，并可选择保留指定标记类型。

        Args:
            need_reserve_icon_name: 需要保留的标记名称(OCR文本或Feature名称)
            ocr: True使用OCR查找标记，False使用Feature匹配

        Returns:
            bool: 操作成功返回True，否则返回False
        """

        # 打开标记显示管理
        if not self.wait_click_feature(
                feature=fL.map_filter_icon,
                time_out=10,
                raise_if_not_found=False,
        ):
            return False

        # 点击清空选中，避免地图筛选导致传送点不显示
        if not self.wait_click_feature(
                feature=fL.to_max_produce_num,
                box=self.box_of_screen(0.117, 0.902, 0.141, 0.941),
                time_out=10,
                raise_if_not_found=False,
        ):
            return False
        self.wait_ui_stable(refresh_interval=0.2)

        # 如需保留特定标记，则尝试查找并勾选
        if need_reserve_icon_name:
            for _ in range(2):
                if ocr:
                    result = self.wait_click_ocr(
                        match=re.compile(need_reserve_icon_name),
                        box=self.box_of_screen(0.003, 0.993, 0.281, 0.063),
                        time_out=2,
                        log=True,
                    )
                else:
                    result = self.wait_click_feature(
                        feature=need_reserve_icon_name,
                        box=self.box_of_screen(0.003, 0.993, 0.281, 0.063),
                        time_out=2,
                        raise_if_not_found=False,
                    )

                if result:
                    break

                self.scroll_relative(0.1, 0.5, -1)

        # 退出标记管理界面
        self.back()
        self.wait_ui_stable(refresh_interval=0.2)

        return True

    def to_near_transfer_point(self, after_track, need_location_list=None):
        """
        在地图上寻找最近的传送点并执行传送。

        流程：
        1. 打开“标记显示管理”。
        2. 清空当前地图选中标记。
        3. 重新点击屏幕中心（淤积点/演武平台需要点击，物资调度终端不需要）。
        4. 在地图上搜索传送点图标。若找到则点击”前往传送“按钮。
        5. 在地图上搜索传送点图标。若找到则点击”传送“按钮。

        Args:
            after_track: 调用函数前是否需要点击”追踪“按钮。
            need_location_list: 需要记录的候选地名列表（本地化名称）；
                命中则把地图右上角显示的当前地区名记录到 self.location，供调用方回填缓存

        Returns:
            bool:
                True  - 成功执行传送
                False - 未找到传送点或传送失败
        """

        # 地图右上角显示当前地区名，命中候选地名则记录到 self.location
        if need_location_list:
            if location := self.wait_ocr(
                match=need_location_list,
                box=self.box.top_right,
                time_out=4,
                log=True,
            ):
                self.location = location[0].name

        # 调用函数前如果点击”追踪“按钮，需要再次点击屏幕中心图标。
        if after_track:
            self.click(0.5, 0.5, after_sleep=1)

        # 查找“前往传送”按钮
        result = self.wait_feature(
            feature=fL.transfer_go,
            time_out=10,
            raise_if_not_found=False,
            horizontal_variance=0.2,
        )

        # 如果未找到“前往传送”按钮
        if not result:
            return False

        # 点击”前往传送“按钮
        self.click(result, after_sleep=1)

        # 查找“传送”按钮
        result = self.wait_feature(
            feature=fL.transfer_go,
            time_out=10,
            raise_if_not_found=False,
        )

        # 如果未找到传送按钮
        if not result:
            return False

        # 点击”传送“按钮
        self.click(result, after_sleep=1)

        return True
