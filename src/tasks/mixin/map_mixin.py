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

    def to_near_transfer_point(self):
        # 点击屏幕中心的淤积点/物资调度终端/演武平台
        self.click(0.5, 0.5, after_sleep=1)
        # 点击前往传送
        result = self.wait_feature(feature=fL.transfer_go, time_out=10, raise_if_not_found=False, horizontal_variance=0.2)
        if not result:
            return False
        self.click(result, after_sleep=1)
        # 点击传送
        result = self.wait_feature(feature=fL.transfer_go, time_out=10, raise_if_not_found=False)
        if not result:
            return False
        self.click(result, after_sleep=1)
        return True
