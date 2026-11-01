import re  # 将观察点名称作为字面文本匹配

from ok import Box  # 保留选中背景检测的像素边界
from qfluentwidgets import FluentIcon  # 复用调试任务图标

from src.data.FeatureList import FeatureList as fL  # 复用任务定位和地图模板
from src.tasks.navigation.mixin.map_mixin import MapMixin  # 复用传送及到达确认


class EnvironmentMonitoringEntryTask(MapMixin):  # 环境监测的传送前流程
    def __init__(self, *args, **kwargs):  # 构造阶段不操作游戏
        super().__init__(*args, **kwargs)  # 初始化项目任务
        self.name = "环境监测：前往观察点"  # 明确当前只完成前半流程
        self.description = "接取或继续一个环境监测委托，传送到观察点后停止，暂不移动或拍照。"  # 保留后半流程供后续实现
        self.group_name = "工具与调试"  # 暂不接入一键日常
        self.group_icon = FluentIcon.DEVELOPER_TOOLS  # 复用工具分组
        self.icon = FluentIcon.SEARCH  # 使用查找图标
        self.default_config = {"观察点名称": ""}  # 可选精确名称，避免误接其他委托
        self.config_description = {
            "观察点名称": "留空时选择第一个可接取或可前往的委托；填写时只选择名称完全匹配的观察点。"
        }  # 说明选择规则
        self.selected_observation = None  # 每次执行重新建立交接信息

    @staticmethod  # 标题引号不是观察点身份的一部分
    def normalize_title(text):  # 兼容 OCR 的引号和空白差异
        return re.sub(r'[\s“”「」『』"«»]', "", text)  # 不模糊匹配不同观察点

    def open_monitoring(self):  # 从任意可恢复页面进入环境监测
        self.ensure_main()  # 复用主界面恢复
        if not self.to_model_area("武陵"):  # 复用地区建设区域选择
            raise RuntimeError("无法进入武陵地区建设")  # 明确入口失败
        if not self.wait_ui_stable():  # 固定入口坐标只在概览入场结束后使用
            raise RuntimeError("地区建设页面未稳定")  # 不点击过渡画面
        if not self.find_one(fL.transaction_overview):  # 稳定等待期间页面可能变化，首点前也重新核对概览
            raise RuntimeError("未处于地区建设概览")  # 不在未知页面点击固定入口
        if not self.wait_until(  # 等待地区建设模块入场
            lambda: (
                self.find_one(fL.material_dispatch_enter, box=self.box.right)  # 匹配物资调度入口图标
                and self.find_one(fL.warehouse_node_enter, box=self.box.right)
            ),  # 同帧匹配仓储节点入口图标
            time_out=5,  # 限制等待时间
            raise_if_not_found=False,  # 缺少任一图标时交由下面停止
        ):  # 同一帧确认物资调度和仓储节点两个已有模板
            raise RuntimeError("未找到地区建设模块入口")  # 缺少布局依据时不点击固定位置
        self.click_relative(1252 / 1920, 673 / 1080)  # 通过两个入口模板确认布局后，点击物资调度下方的环境监测位置
        if not self.wait_until(self._monitoring_page, time_out=5, raise_if_not_found=False):  # 验证确实进入终端页
            raise RuntimeError("未进入环境监测终端列表")  # 未进入时停止，不再识别入口文字
        if not self.wait_ui_stable():  # 等待卡片入场完成
            raise RuntimeError("环境监测页面未稳定")  # 超时不扫描过渡画面

    def _terminal_area(self, matcher):  # 两个终端顺序固定，空白区兼作点击和选中检测区域
        left = (
            0.27 if matcher.pattern == self.lang.environment_monitoring.marker_stone.pattern else 0.06
        )  # 实测两标签的空白区
        height, width = self.frame.shape[:2]  # 使用当前帧尺寸
        x, y = int(width * left), int(height * 0.145)  # 保持原裁剪的向下取整边界
        return Box(x, y, int(width * (left + 0.07)) - x, int(height * 0.16) - y)  # 避开文字与等级数字

    def _terminal_selected(self, matcher):  # 验证终端标签的白色选中背景
        white = dict.fromkeys(("r", "g", "b"), (186, 255))  # 严格保留各通道大于 185 的边界
        return self.calculate_color_percentage(white, self._terminal_area(matcher)) > 0.7  # 只有当前选中标签呈白底

    def _monitoring_page(self):  # 组合控件位置和终端配色，不能只用通用按键模板认定页面
        if not self.find_one(fL.q, box=self.box_of_screen(0.025, 0.12, 0.046, 0.155), use_gray_scale=True):
            return False  # 左侧终端切换键必须存在
        if not self.find_one(fL.e, box=self.box_of_screen(0.45, 0.12, 0.47, 0.155), use_gray_scale=True):
            return False  # 右侧终端切换键必须存在
        cyan = {"r": (0, 90), "g": (130, 240), "b": (130, 240)}  # 两个终端均有青色侧条
        for left in (0.046, 0.25):  # 限定已验证的两条竖线位置
            if self.calculate_color_percentage(cyan, self.box_of_screen(left, 0.105, left + 0.003, 0.167)) <= 0.7:
                return False  # 避免其他菜单的 Q/E 控件误判
        terminals = (self.lang.environment_monitoring.outskirts, self.lang.environment_monitoring.marker_stone)
        return sum(self._terminal_selected(terminal) for terminal in terminals) == 1  # 黑屏、双白底和未知状态均拒绝

    def select_terminal(self, matcher):  # 两个终端共用切换逻辑
        for _ in range(2):  # 有界重试被动画吞掉的点击
            if not self._monitoring_page():  # 每次重试都核对页面，避免等待期间已离开终端页
                raise RuntimeError("未进入环境监测终端列表")  # 固定坐标只能在已确认的终端页使用
            if not self._terminal_selected(matcher):  # 已选中时无需重复点击或识别终端名称
                self.click(self._terminal_area(matcher))  # 点击对应标签内已验证的固定空白区
                if not self.wait_until(lambda: self._terminal_selected(matcher), time_out=3, raise_if_not_found=False):
                    continue  # 等待点击生效后再检查稳定状态
            if (
                self.wait_ui_stable() and self._monitoring_page() and self._terminal_selected(matcher)
            ):  # 验证实际选中并等待对应卡片稳定
                return  # 选中与稳定均确认后才扫描
        raise RuntimeError("环境监测终端切换未完成")  # 不将旧终端卡片当成新终端

    def _observation_action(self, area):  # 仅在已确认终端页的卡片底部区分黄色接取、白色前往和无按钮
        sample = Box(
            area.x + int(area.width * 0.36),
            area.y + int(area.height * 0.2),
            int(area.width * 0.25),
            int(area.height * 0.6),
        )  # 中部空白避开左侧箭头装饰和右侧按钮文字
        yellow = {"r": (210, 255), "g": (200, 255), "b": (0, 90)}  # 已保存接取截图的亮黄背景
        if self.calculate_color_percentage(yellow, sample) > 0.9:
            return "accept"  # 接取按钮可以点一次等待白底
        if self.calculate_color_percentage(dict.fromkeys(("r", "g", "b"), (220, 255)), sample) > 0.9:
            return "go"  # 已保存前往截图的近白背景，灰色或已收录卡片不通过
        return None  # 过渡、暗色和未知按钮均不可操作

    def available_observations(self):  # 从当前终端的三张卡片读取可执行委托
        observations = []  # 只记录有操作按钮的卡片
        titles_on_page = None  # 没有可操作卡片时无需 OCR，同一稳定页的标题最多读取一次
        for index in range(3):  # 当前界面实测固定三列
            left = 0.049 + index * 0.304  # 根据 1920x1080 实测截图估计卡片左边界
            right = min(left + 0.293, 0.96)  # 根据实测截图估计卡片右边界
            title_box = self.box_of_screen(left, 0.285, right, 0.335)  # 仅标题起点范围，排除下面的拍摄说明
            action_box = self.box_of_screen(left, 0.85, right, 0.925)  # 仅本卡片底部操作区
            if not self._observation_action(action_box):  # 已收录卡片没有黄或白底按钮
                continue  # 不接取已完成观察点
            if titles_on_page is None:  # 观察点会轮换且可由用户指定，图标和颜色不能证明具体身份
                titles_on_page = self.ocr()  # 为本页可操作卡片复用同一次完整标题识别
            titles = [
                item
                for item in titles_on_page
                if title_box.x <= item.x < title_box.x + title_box.width
                and title_box.y <= item.y < title_box.y + title_box.height
            ]  # 整帧识别后按卡片定位，避免紧裁剪漏掉引号后的小字
            if len(titles) != 1:  # 不猜测截断或多行标题
                continue  # 保守跳过无法确认身份的卡片
            observations.append({"name": titles[0].name, "action_box": action_box})  # 保留动态操作区域
        return observations  # 返回当前终端候选

    def enter_observation(self, observation):  # 接取或继续指定卡片
        area = observation["action_box"]  # 始终局限于目标卡片
        if not self._monitoring_page():  # 颜色本身不具备页面身份，点击前再确认终端页
            raise RuntimeError("未进入环境监测终端列表")  # 禁止在其他页面按颜色点击
        if self._observation_action(area) == "accept":  # 仅未接取时点击一次
            self.click(area)  # 使用目标卡片内按钮区域
        if not self.wait_until(
            lambda: self._monitoring_page() and self._observation_action(area) == "go",
            time_out=5,
            settle_time=0.3,
            raise_if_not_found=False,
        ):  # 等待本卡片白底前往按钮稳定出现
            raise RuntimeError("未找到目标委托的前往任务按钮")  # 停止而不是点其他卡片
        self.click(area)  # 状态确认后只点击本卡片
        title = self.normalize_title(observation["name"])  # 去掉装饰性引号
        if not self.wait_ocr(
            match=re.compile(re.escape(title)), box=self.box_of_screen(0.32, 0.075, 0.97, 0.16), time_out=5
        ):  # 定位图标只能证明任务面板，动态标题文字才可确认进入的是所选观察点
            raise RuntimeError("任务详情与所选观察点不一致")  # 防止传送至错误任务
        if not self.wait_ui_stable():  # 追踪按钮入场稳定后再操作
            raise RuntimeError("环境监测任务详情未稳定")  # 禁止点击过渡画面

    def _tracked_map_locator(self):  # 同一帧同时出现停止追踪标记和地图定位才确认已追踪
        if not self.find_one(
            fL.skip_dialog_cancel, box=self.box_of_screen(0.93, 0.89, 0.97, 0.95), use_gray_scale=True
        ):  # 仅在停止追踪按钮内识别叉号，避免页面其他关闭按钮
            return None  # 通用叉号本身不能证明任务已追踪
        return self.find_one(fL.one_task_to_map, box=self.box.bottom_right)  # 返回定位按钮，绝不点击停止追踪

    def transfer_observation(self):  # 正确处理未追踪与已追踪两条入口
        locator = self._tracked_map_locator()  # 已追踪的常见路径先用已验证双模板，不重复 OCR
        if locator:
            self.click(locator)  # 点击同一帧已确认的地图定位按钮
        else:
            track_area = self.box_of_screen(0.81, 0.89, 0.98, 0.95)  # 仅限详情面板的追踪按钮
            track = self.find_one(fL.start_follow, box=track_area)  # 沿用默认阈值，实际命中才点击
            if not track:  # 缺少环境监测未追踪正例，尚不能证明现有追踪模板覆盖此按钮，文字仅作兜底
                tracks = self.ocr(match=self.lang.environment_monitoring.track, box=track_area)
                track = tracks[0] if tracks else None
            if track:
                self.click(track)  # 首次追踪会自动打开任务地图
            else:
                locator = self.wait_until(self._tracked_map_locator, time_out=4, raise_if_not_found=False)
                if not locator:
                    raise RuntimeError("无法确认环境监测任务追踪状态")  # 不把识别失败或单个通用图标当成已追踪
                self.click(locator)  # 允许追踪状态稍迟出现，但仍只点击定位图标
        if not self.wait_feature(
            fL.in_map, box=self.box_of_screen(0.027, 0.531, 0.051, 0.896), time_out=10, raise_if_not_found=False
        ):  # 验证地图已打开
            raise RuntimeError("环境监测任务地图未打开")  # 禁止在任务面板直接点传送
        if not self.wait_ui_stable():  # 等待地图上的按钮稳定
            raise RuntimeError("环境监测任务地图未稳定")  # 禁止点击过渡画面
        if not self.to_near_transfer_point(need_track=False):  # 复用前往传送和传送两步
            raise RuntimeError("环境监测快捷传送失败")  # 失败不进入后半流程
        if not self._wait_for_transfer_arrival():  # 等待大世界稳定出现
            raise RuntimeError("环境监测传送后未确认到达")  # 点击成功不等于到达

    def prepare_observation(self, target_name=""):  # 可供后半流程复用的入口
        self.selected_observation = None  # 清除上轮结果
        self.open_monitoring()  # 每次从明确入口开始
        desired = self.normalize_title(target_name)  # 配置名称按同一规则比较
        terminals = [
            self.lang.environment_monitoring.outskirts,
            self.lang.environment_monitoring.marker_stone,
        ]  # 固定终端遍历顺序
        for terminal in terminals:  # 两个终端分别识别
            self.select_terminal(terminal)  # 选择终端并等待稳定
            for observation in self.available_observations():  # 只考虑可操作卡片
                if desired and self.normalize_title(observation["name"]) != desired:  # 指定名称时不退回其他任务
                    continue  # 保留其他委托
                self.enter_observation(observation)  # 接取或继续所选观察点
                self.transfer_observation()  # 快捷传送并验证到达
                self.selected_observation = {
                    "name": observation["name"],
                    "terminal": terminal.pattern,
                }  # 交给后半流程的上下文
                self.log_info(f"[environment-monitoring] arrived: {self.selected_observation}")  # 记录实际到达任务
                return self.selected_observation  # 一次只处理一个观察点
        raise RuntimeError("未找到符合条件的可接取或可前往观察点")  # 不将无匹配伪报为成功

    def run(self):  # GUI 与 MCP 共用生产流程
        return self.prepare_observation(str(self.config.get("观察点名称", "")).strip())  # 到达后停止，保留拍照任务
