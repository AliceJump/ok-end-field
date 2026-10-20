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
            pre_action=lambda: self.click(1770 / 1920, 135 / 1080, interval=3),
            raise_if_not_found=False,  # 超时后由任务记录失败
        ):  # 超时仍未识别到入口则停止
            self.mark_task_failure("未识别到地区总览的帝江号入口，停止后续地图操作")  # 记录识别失败
            return False  # 结束本次任务

        self.wait_ui_stable()  # 等待地图页签切换完成
        self.log_info("切换到武陵地图")  # 记录地图区域切换步骤
        if not self.wait_click_ocr(  # 等待识别武陵页签并点击
            match=self.lang.world_map.k_a4410e3f,  # 复用武陵的多语言 OCR 词条
            box=self.box_of_screen(0.74, 0.89, 0.98, 0.95),  # 仅识别右下角地区页签区域
            time_out=5,  # 最多等待五秒
            raise_if_not_found=False,  # 未识别到时由任务记录失败
            after_sleep=2,  # 点击后等待地图切换
        ):  # 未找到武陵页签时停止
            self.mark_task_failure("未识别到武陵地图页签")  # 记录地图区域识别失败
            return False  # 结束本次任务
        self.wait_ui_stable()  # 等待地图页签切换完成

        self.log_info("进入祀的画地图")  # 记录目标地图步骤
        if not self.wait_feature(  # 确认地图筛选入口已出现
            feature=fL.map_filter_icon,  # 使用地图筛选入口模板
            box=self.box_of_screen(0.10, 0.90, 0.14, 0.94),  # 限定在地图左下角的入口区域
            time_out=10,  # 最多等待十秒
            pre_action=lambda: self.click(1668 / 1920, 250 / 1080, after_sleep=2, interval=3),
            raise_if_not_found=False,  # 未出现时由任务自行记录失败
        ):
            self.mark_task_failure("未进入祀的画地图，未找到标记筛选入口")  # 记录地图加载失败
            return False  # 结束本次任务

        if not self._click_farm_related():  # 清空其他标记并只选农牧相关
            self.mark_task_failure("筛选农牧相关标记失败")  # 记录标记筛选失败
            return False  # 结束本次任务

        self.log_info("农牧相关标记筛选完成，点击牧场图标")  # 记录筛选后的牧场点击步骤
        if not self.wait_click_feature(  # 等待并点击牧场地图模板
            feature=fL.ranch,  # 使用新增的牧场图标模板
            box=self.box_of_screen(0.30, 0.20, 0.65, 0.65),  # 在地图中央搜索，覆盖选中前后的图标位置
            time_out=5,  # 最多等待五秒
            raise_if_not_found=False,  # 未识别到时由任务记录失败
        ):  # 未识别到牧场时停止后续操作
            self.mark_task_failure("未找到牧场地图图标")  # 记录牧场图标识别失败
            return False  # 结束本次任务
        self.wait_ui_stable()  # 等待点击牧场后的界面稳定
        if not self._collect_to_wuling_warehouse("牧场"):  # 复用内部方法收取牧场产出
            return False  # 收取失败时结束本次任务
        if not self.wait_click_feature(  # 等待并点击自动农植机地图图标
            feature=fL.auto_farmer_on_map,  # 直接使用专用地图模板
            use_gray_scale=True,  # 以灰度匹配白色图标
            box=self.box_of_screen(0.30, 0.20, 0.65, 0.65),  # 仅搜索地图中央的图标
            time_out=5,  # 最多等待五秒
            raise_if_not_found=False,  # 未识别到时由任务记录失败
            after_sleep=1,  # 点击后等待界面响应
        ):  # 未找到地图图标时停止
            self.mark_task_failure("未找到自动农植机地图图标")  # 记录模板识别失败
            return False  # 结束本次任务
        self.log_info("点击自动农植机地图图标")  # 记录下一个农牧入口的点击步骤
        self.wait_ui_stable()  # 等待地图详情切换完成
        self.log_info("选择自动农植机")  # 记录弹出列表中的选择步骤
        if not self.wait_click_feature(  # 等待并点击列表中的自动农植机
            feature=fL.auto_farmer,  # 使用列表按钮模板
            vertical_variance=1,  # 沿模板所在列搜索整个屏幕高度，允许纵坐标变化
            use_gray_scale=True,  # 使用灰度匹配图标
            time_out=5,  # 最多等待五秒
            raise_if_not_found=False,  # 未识别到时由任务记录失败
            after_sleep=1,  # 点击后等待界面响应
        ):  # 未找到列表按钮时停止
            self.mark_task_failure("未找到自动农植机列表按钮")  # 记录按钮识别失败
            return False  # 结束本次任务
        self.wait_ui_stable()  # 等待自动农植机界面稳定
        return self._collect_to_wuling_warehouse("自动农植机")  # 复用相同流程收取自动农植机产出

    def _collect_to_wuling_warehouse(self, source_name):  # 统一处理牧场和自动农植机的收取
        self.log_info(f"开始点击{source_name}的存至武陵仓库按钮")  # 记录当前收取对象
        for _ in range(5):  # 沿用原有的五次收取点击
            if not self.wait_click_feature(  # 识别仓库存放按钮右端圆角并点击
                feature=fL.sentence_part_feature,  # 复用白色按钮边缘模板
                box=self.box_of_screen(0.93, 0.76, 0.98, 0.84),  # 限定存至武陵仓库按钮的右端区域
                time_out=5,  # 最多等待五秒让收取按钮出现
                raise_if_not_found=False,  # 未出现时由任务记录失败
                after_sleep=0.5,  # 点击后等待界面响应
            ):  # 未识别到仓库存放按钮时停止
                self.mark_task_failure(f"未找到{source_name}的存至武陵仓库按钮")  # 记录对应对象的识别失败
                return False  # 将收取失败返回给调用方
            elif not self.wait_pop_up(time_out=5):
                self.log_info(f"{source_name}未找到确认按钮，收取结束")
                break

        self.log_info(f"已点击{source_name}的存至武陵仓库按钮")  # 记录按钮点击完成
        return True  # 将收取成功返回给调用方

    def _click_farm_related(self):
        """
        清理地图标记筛选，保留农牧相关。
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
            self.scroll_relative(0.1, 0.5, -1)
            if self.wait_click_feature(  # 等待并点击农牧相关图标
                feature=fL.farm_related_icon,  # 使用子仓库中已有的农牧筛选模板
                box=self.box_of_screen(0.025, 0.065, 0.145, 0.88),  # 限定左侧第一列列表，避开右列相似图标和底部按钮
                use_gray_scale=True,  # 使用灰度匹配筛选图标
                time_out=2,  # 每次最多等待两秒让列表显示完成
                raise_if_not_found=False,  # 未找到时继续滚动查找
                after_sleep=0.5,  # 点击后等待筛选状态更新
            ):  # 找到并点击后结束查找
                # 退出标记管理界面
                self.back()
                self.wait_ui_stable()
                return True

        return False
