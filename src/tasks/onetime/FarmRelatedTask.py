from qfluentwidgets import FluentIcon  # 导入日常任务分组图标

from src.data.FeatureList import FeatureList as fL  # 导入地图筛选模板列表
from src.icons import Icons  # 导入应用内任务图标
from src.tasks.mixin.common import Common  # 复用一次性任务通用能力
from src.tasks.mixin.liaison_mixin import LiaisonMixin  # 复用帝江号传送流程
from src.tasks.mixin.map_mixin import MapMixin  # 复用地图筛选流程


class FarmRelatedTask(Common, LiaisonMixin, MapMixin):  # 定义农牧标记筛选任务
    def __init__(self, *args, **kwargs):  # 初始化任务
        super().__init__(*args, **kwargs)  # 初始化基类和各个 mixin
        self.name = "农牧相关"  # 设置任务名称
        self.icon = Icons.Collect  # 设置任务图标
        self.group_name = "日常任务"  # 将任务放入日常任务分组
        self.group_icon = FluentIcon.CALENDAR  # 使用日常任务分组的日历图标
        self.description = "进入帝江号的武陵地图与祀的画，并筛选农牧相关标记。"  # 设置任务说明
        self.support_multi_account = True  # 支持多账号执行

    def run(self):  # 执行农牧标记筛选
        self.ensure_main(time_out=420)  # 等待并确保游戏处于主界面
        if not self.transfer_to_home_point(should_check_out_boat=True):  # 复用传送流程进入帝江号
            self.mark_task_failure("进入帝江号失败，无法筛选农牧标记")  # 记录进入帝江号失败
            return False  # 结束本次任务

        self.ensure_map()  # 打开帝江号内的地图界面
        self.log_info("从帝江号地图打开地区总览")  # 记录打开地区总览的步骤
        if not self.wait_feature(  # 复用等待工具，先识别再决定是否点击
            feature=fL.boat_entrance_icon,  # 识别地区总览中的帝江号入口
            threshold=0.6,  # 沿用当前入口识别阈值
            time_out=10,  # 将识别和点击重试限制在十秒等待内
            post_action=lambda: self.click(1770 / 1920, 135 / 1080, interval=3),  # 识别失败才按截图坐标点击，间隔至少三秒
            raise_if_not_found=False,  # 超时后由任务记录失败
        ):  # 超时仍未识别到入口则停止
            self.mark_task_failure("未识别到地区总览的帝江号入口，停止后续地图操作")  # 记录识别失败
            return False  # 结束本次任务

        self.log_info("切换到武陵地图")  # 记录地图区域切换步骤
        self.click(1534 / 1920, 990 / 1080, after_sleep=2)  # 按截图估算坐标点击右下角武陵页签
        self.wait_ui_stable(refresh_interval=0.5)  # 等待地图页签切换完成

        self.log_info("进入祀的画地图")  # 记录目标地图步骤
        self.click(1668 / 1920, 250 / 1080, after_sleep=2)  # 按截图估算坐标点击祀的画区域
        self.wait_ui_stable(refresh_interval=0.5)  # 等待祀的画地图加载完成
        if not self.wait_feature(  # 确认地图筛选入口已出现
            feature=fL.map_filter_icon,  # 使用地图筛选入口模板
            box=self.box_of_screen(0.10, 0.90, 0.14, 0.94),  # 限定在地图左下角的入口区域
            time_out=10,  # 最多等待十秒
            raise_if_not_found=False,  # 未出现时由任务自行记录失败
        ):
            self.mark_task_failure("未进入祀的画地图，未找到标记筛选入口")  # 记录地图加载失败
            return False  # 结束本次任务

        if not self.clear_icon_in_map(need_reserve_icon_name="农牧相关", ocr=True):  # 清空其他标记并只选农牧相关
            self.mark_task_failure("筛选农牧相关标记失败")  # 记录标记筛选失败
            return False  # 结束本次任务

        self.log_info("农牧相关标记筛选完成，点击牧场图标")  # 记录筛选后的牧场点击步骤
        self.click(875 / 1920, 354 / 1080, after_sleep=1)  # 按截图估算坐标点击缘缘庄旁的黄色牧场栅栏图标
        self.wait_ui_stable(refresh_interval=0.2)  # 等待点击牧场后的界面稳定
        for _ in range(5):
            if not self.wait_click_feature(  # 识别仓库存放按钮右端圆角并点击
                feature=fL.sentence_part_feature,  # 复用白色按钮边缘模板
                box=self.box_of_screen(0.93, 0.76, 0.98, 0.84),  # 按截图限定存至武陵仓库按钮的右端区域
                time_out=5,  # 最多等待五秒让牧场详情出现
                raise_if_not_found=False,  # 未出现时由任务记录失败
                after_sleep=0.5,  # 点击后等待界面响应
            ):  # 未识别到仓库存放按钮时停止
                self.mark_task_failure("未找到牧场的存至武陵仓库按钮")  # 记录按钮识别失败
                return False  # 结束本次任务
        self.log_info("已点击牧场的存至武陵仓库按钮")  # 记录按钮点击完成
        return True  # 返回任务成功
