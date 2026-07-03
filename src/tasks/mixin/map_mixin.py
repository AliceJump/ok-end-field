import re
from src.tasks.BaseEfTask import BaseEfTask
from src.data.FeatureList import FeatureList as fL

class MapMixin(BaseEfTask):
    def task_to_transfer_point(self, test_target_box=None, search_box_resolver=None):
        """
        传送到运输委托对应的出发传送点。

        流程：
        1. 确保当前在主界面。
        2. 按 J 打开任务界面。
        3. 点击“任务定位到地图”的按钮。
        4. 等待地图稳定。
        5. 在地图上寻找附近传送点并执行传送。

        Args:
            test_target_box (Box, optional):
                查找传送点的屏幕区域。
                如果为 None，则默认使用 self.box.top 区域。
            search_box_resolver (callable, optional):
                地图加载完成后用于重新解析传送点搜索区域的回调。
                入参为当前的 test_target_box，返回值为新的 Box 或 None。

        Returns:
            bool:
                True  - 成功执行传送
                False - 任一步骤失败
        """

        # 如果没有指定搜索区域，则默认使用屏幕上半区域
        if test_target_box is None:
            test_target_box = self.box.top

        # 确保当前处于主界面
        self.ensure_main()

        # 打开任务界面
        self.press_key("j", after_sleep=2)

        # 查找“任务定位到地图”按钮
        result = self.find_feature(
            feature="one_task_to_map",
            threshold=0.8,
            box=self.box.bottom_right
        )

        # 如果没有找到按钮，则流程失败
        if not result:
            return False

        # 点击按钮跳转到地图
        self.click(result, after_sleep=2)

        # 等待 UI 稳定（地图加载完成）
        self.wait_ui_stable(refresh_interval=1)

        if search_box_resolver is not None:
            resolved_box = search_box_resolver(test_target_box)
            if resolved_box is not None:
                test_target_box = resolved_box

        # 执行附近传送点传送
        return self.to_near_transfer_point(test_target_box)

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
                after_sleep=2,
                raise_if_not_found=False,
        ):
            return False

        # 点击清空选中，避免地图筛选导致传送点不显示
        if not self.wait_click_feature(
                feature=fL.to_max_produce_num,
                box=self.box_of_screen(0.117, 0.902, 0.141, 0.941),
                time_out=10,
                raise_if_not_found=False,
                after_sleep=0.5,
        ):
            return False

        # 如需保留特定标记，则尝试查找并勾选
        if need_reserve_icon_name:
            for _ in range(2):
                if ocr:
                    result = self.wait_click_ocr(
                        match=re.compile(need_reserve_icon_name),
                        box=self.box_of_screen(0.003, 0.993, 0.281, 0.063),
                        time_out=2,
                        log=True,
                        after_sleep=2,
                    )
                else:
                    result = self.wait_click_feature(
                        feature=need_reserve_icon_name,
                        box=self.box_of_screen(0.003, 0.993, 0.281, 0.063),
                        time_out=2,
                        raise_if_not_found=False,
                        after_sleep=2,
                    )

                if result:
                    break

                self.scroll_relative(0.1, 0.5, -1)

        # 退出标记管理界面
        self.back(after_sleep=2)

        return True

    def to_near_transfer_point(self, test_target_box):
        """
        在地图上寻找最近的传送点并执行传送。

        流程：
        1. 打开“标记显示管理”。
        2. 清空当前地图选中标记。
        3. 关闭设置界面。
        4. 在地图上搜索传送点图标。
        5. 若未找到，则滚动地图继续查找。
        6. 找到传送点后点击。
        7. 点击“传送”按钮完成传送。

        Args:
            test_target_box (Box):
                搜索传送点的地图区域。

        Returns:
            bool:
                True  - 成功执行传送
                False - 未找到传送点或传送失败
        """
        self.clear_icon_in_map()
        result = None

        # 最多尝试 8 次寻找传送点
        for _ in range(16):

            # 查找传送点图标
            result = self.find_feature(
                feature="transfer_point",
                box=test_target_box,
                threshold=0.8
            )

            # 找到则跳出循环
            if result:
                break

            # 刷新一帧（防止识别缓存）
            self.next_frame()

            # 向下滚动地图继续查找
            self.scroll_relative(0.5, 0.5, -2)
            self.sleep(0.5)

        # 如果最终仍然没有找到传送点
        if not result:
            return False

        # 点击传送点
        self.click(result, after_sleep=2)

        # 查找“传送”按钮
        result = self.wait_feature(
            feature=fL.transfer_go,
            time_out=10,
            raise_if_not_found=False
        )

        # 如果未找到传送按钮
        if not result:
            return False

        # 点击传送按钮
        self.click(result, after_sleep=2)

        return True
