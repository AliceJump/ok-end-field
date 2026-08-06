import re
from src.core.BaseEfTask import BaseEfTask
from src.data.FeatureList import FeatureList as fL

class MapMixin(BaseEfTask):
    def task_to_transfer_point(self):
        """
        传送到运输委托对应的出发传送点。

        流程：
        1. 确保当前在主界面。
        2. 按 J 打开任务界面。
        3. 点击“任务定位到地图”的按钮。
        4. 等待地图稳定。
        5. 传送至附近传送点。

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
        return self.to_near_transfer_point()

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

    def to_near_transfer_point(self):
        """
        在地图上寻找最近的传送点并执行传送。

        流程：
        1. 打开“标记显示管理”。
        2. 清空当前地图选中标记。
        3. 重新点击屏幕中心（淤积点/物资调度终端/演武平台）。
        4. 在地图上搜索传送点图标。若找到则点击”前往传送“按钮。
        5. 在地图上搜索传送点图标。若找到则点击”传送“按钮。

        Returns:
            bool:
                True  - 成功执行传送
                False - 未找到传送点或传送失败
        """

        # 清空当前地图选中标记避免误触
        if not self.clear_icon_in_map():
            return False

        # 点击屏幕中心的淤积点/物资调度终端/演武平台
        self.click(0.5, 0.5, after_sleep=1)

        # 点击”前往传送“按钮
        result = self.wait_feature(
            feature=fL.transfer_go,
            time_out=10,
            raise_if_not_found=False,
            horizontal_variance=0.2,
        )
        if not result:
            return False
        self.click(result, after_sleep=1)

        # 点击”传送“按钮
        result = self.wait_feature(
            feature=fL.transfer_go,
            time_out=10,
            raise_if_not_found=False,
        )
        if not result:
            return False
        self.click(result, after_sleep=1)

        return True
