import re  # 将观察点名称作为字面文本匹配

from ok import Box  # 保留选中背景检测的像素边界
from ok.util.color import calculate_color_percentage  # 复用颜色占比排除灰白色圆勾
from qfluentwidgets import FluentIcon  # 复用日常任务分组图标

from src.data.FeatureList import FeatureList as fL  # 复用任务定位、地图和相机模板
from src.tasks.navigation.mixin.map_mixin import MapMixin  # 复用传送及到达确认
from src.tasks.onetime.EnvironmentMonitoringNavigation import (  # 根据观察点选择导航组件
    OBSERVATION_ROUTES,  # 入口筛选与路线分发共用注册表
    EnvironmentMonitoringNavigation,  # 使用当前任务执行点位路线
)

TERMINAL_LEFTS = {"outskirts": 0.06, "marker_stone": 0.27}  # 按固定左右顺序标识终端及其标签空白区


class EnvironmentMonitoringPhotoTask(MapMixin):  # 共用接取、传送与相机流程
    def __init__(self, *args, **kwargs):  # 构造阶段不操作游戏
        super().__init__(*args, **kwargs)  # 初始化项目任务与生命周期
        self.group_name = "日常任务"  # 环境监测属于日常业务
        self.group_icon = FluentIcon.CALENDAR  # 与现有日常子任务一致
        self.icon = FluentIcon.SEARCH  # 保持原有任务图标
        self.name = "环境监测"  # 任务名称不绑定某个观察点
        self.description = "前往小池荷，走到竹子旁并打开相机，检查拍摄条件后停止，不拍照。"  # 保留委托供重复实测
        self.default_config = {}  # 路线固定，不允许将其用于其他观察点
        self.config_description = {}  # 不展示观察点名称配置

        self.selected_observation = None  # 每次执行重新建立交接信息
        self.navigation = EnvironmentMonitoringNavigation(self)  # 路线复用当前任务的设备和状态

    @staticmethod  # 标题符号不是观察点身份的一部分
    def normalize_title(text):  # 兼容符号、空白与英文大小写差异
        return re.sub(r"[\W_]+", "", text).casefold()  # 保留各语言文字，完整名称仍须一致

    def observation_route(self, title):  # 按语言名称找到已注册的路线
        normalized = self.normalize_title(title)  # 不依赖引号和空格
        matched = None  # 多条路线同时匹配时不能猜测
        for key, route in OBSERVATION_ROUTES.items():  # 只识别已经注册路线的观察点
            name = getattr(self.lang.environment_monitoring, key, None)  # 从语言资源读取当前语言名称
            if isinstance(name, re.Pattern):  # 语言资源中的正则用于短识别词
                found = bool(normalized) and not name.search("") and bool(name.search(normalized))  # 不接受空匹配
            else:  # 普通名称仍要求完整一致
                found = isinstance(name, str) and name and normalized == self.normalize_title(name)  # 避免泛化误匹配
            if found:  # 已找到候选路线
                if matched is not None:  # 多条路线匹配同一卡片
                    return None  # 停止而不是根据注册顺序猜测
                matched = route  # 记录唯一匹配的导航方法
        return matched  # 未匹配时返回 None

    def open_monitoring(self):  # 从任意可恢复页面进入环境监测
        self.ensure_main()  # 复用主界面恢复
        if not self.to_model_area("武陵"):  # 复用地区建设区域选择
            raise RuntimeError("无法进入武陵地区建设")  # 明确入口失败
        if not self.wait_ui_stable():  # 等待概览入场结束后寻找入口
            raise RuntimeError("地区建设页面未稳定")  # 不点击过渡画面
        if not self.find_one(fL.transaction_overview):  # 稳定等待期间页面可能变化，首点前也重新核对概览
            raise RuntimeError("未处于地区建设概览")  # 不在未知页面点击入口
        entry = self.wait_until(
            lambda: (
                self.find_one(fL.transaction_overview)  # 同帧确认仍在地区建设概览
                and self.find_one(fL.environment_monitoring_enter, box=self.box.right)  # 识别环境监测入口图标
            ),  # 防止页面变化后点击误匹配的图标
            time_out=5,  # 限制等待时间
            raise_if_not_found=False,  # 未找到交由下面停止
        )  # 在地区建设概览中等待入口出现
        if not entry:  # 未找到环境监测入口
            raise RuntimeError("未找到环境监测入口")  # 不盲点其他模块
        self.click(entry)  # 点击识别到的环境监测图标
        if not self.wait_until(self._monitoring_page, time_out=5, raise_if_not_found=False):  # 验证确实进入终端页
            raise RuntimeError("未进入环境监测终端列表")  # 未进入时停止，不再识别入口文字
        if not self.wait_ui_stable():  # 等待卡片入场完成
            raise RuntimeError("环境监测页面未稳定")  # 超时不扫描过渡画面

    def _terminal_area(self, terminal):  # 两个终端顺序固定，空白区兼作点击和选中检测区域
        left = TERMINAL_LEFTS[terminal]  # 内部标识定位左右标签，未知终端不回退到其他标签
        height, width = self.frame.shape[:2]  # 使用当前帧尺寸
        x, y = int(width * left), int(height * 0.145)  # 保持原裁剪的向下取整边界
        return Box(x, y, int(width * (left + 0.07)) - x, int(height * 0.16) - y)  # 避开文字与等级数字

    def _terminal_selected(self, terminal):  # 验证终端标签的白色选中背景
        white = dict.fromkeys(("r", "g", "b"), (186, 255))  # 严格保留各通道大于 185 的边界
        return self.calculate_color_percentage(white, self._terminal_area(terminal)) > 0.7  # 只有当前选中标签呈白底

    def _monitoring_page(self):  # 组合控件位置和终端配色，不能只用通用按键模板认定页面
        if not self.find_one(fL.q, box=self.box_of_screen(0.025, 0.12, 0.046, 0.155), use_gray_scale=True):
            return False  # 左侧终端切换键必须存在
        if not self.find_one(fL.e, box=self.box_of_screen(0.45, 0.12, 0.47, 0.155), use_gray_scale=True):
            return False  # 右侧终端切换键必须存在
        cyan = {"r": (0, 90), "g": (130, 240), "b": (130, 240)}  # 两个终端均有青色侧条
        for left in (0.046, 0.25):  # 限定已验证的两条竖线位置
            if self.calculate_color_percentage(cyan, self.box_of_screen(left, 0.105, left + 0.003, 0.167)) <= 0.7:
                return False  # 避免其他菜单的 Q/E 控件误判
        return (
            sum(self._terminal_selected(terminal) for terminal in TERMINAL_LEFTS) == 1
        )  # 黑屏、双白底和未知状态均拒绝

    def select_terminal(self, terminal):  # 两个终端共用切换逻辑
        for _ in range(2):  # 有界重试被动画吞掉的点击
            if not self._monitoring_page():  # 每次重试都核对页面，避免等待期间已离开终端页
                raise RuntimeError("未进入环境监测终端列表")  # 固定坐标只能在已确认的终端页使用
            if not self._terminal_selected(terminal):  # 已选中时无需重复点击或识别终端名称
                self.click(self._terminal_area(terminal))  # 点击对应标签内已验证的固定空白区
                if not self.wait_until(lambda: self._terminal_selected(terminal), time_out=3, raise_if_not_found=False):
                    continue  # 等待点击生效后再检查稳定状态
            if (
                self.wait_ui_stable() and self._monitoring_page() and self._terminal_selected(terminal)
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
            if titles_on_page is None:  # 观察点会轮换，图标和颜色不能证明具体身份
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
        if not self.wait_until(
            self._task_detail_page, time_out=5, raise_if_not_found=False
        ):  # 通过任务页固定图标确认页面
            raise RuntimeError("未进入任务详情")  # 未进入时不尝试追踪或传送
        if not self.wait_ui_stable():  # 追踪按钮入场稳定后再操作
            raise RuntimeError("环境监测任务详情未稳定")  # 禁止点击过渡画面

    def _task_detail_page(self):  # 根据任务页顶部的 Q、E 图标确认界面
        return bool(  # 两个图标必须在同一帧出现
            self.find_one(fL.q, box=self.box_of_screen(0.34, 0.02, 0.37, 0.06), use_gray_scale=True)  # 左切换键
            and self.find_one(fL.e, box=self.box_of_screen(0.63, 0.02, 0.66, 0.06), use_gray_scale=True)  # 右切换键
        )  # 名称沿用已选中的卡片，不重复 OCR

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
            track = self.find_one(fL.transfer_go, box=track_area)  # 未追踪实测匹配追踪按钮图标
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

    def prepare_observation(self):  # 自动前往当前支持的观察点
        self.selected_observation = None  # 清除上轮结果
        self.open_monitoring()  # 每次从明确入口开始
        for terminal in TERMINAL_LEFTS:  # 按固定左右顺序遍历两个终端
            self.select_terminal(terminal)  # 选择终端并等待稳定
            for observation in self.available_observations():  # 只考虑可操作卡片
                if not self.observation_route(observation["name"]):  # 只选择已注册路线的点位
                    continue  # 跳过尚未支持的委托
                self.enter_observation(observation)  # 接取或继续所选观察点
                self.transfer_observation()  # 快捷传送并验证到达
                self.selected_observation = {
                    "name": observation["name"],
                    "terminal": terminal,
                }  # 交给后半流程的上下文
                self.log_info(f"[environment-monitoring] arrived: {self.selected_observation}")  # 记录实际到达任务
                return self.selected_observation  # 一次只处理一个观察点
        raise RuntimeError("未找到符合条件的可接取或可前往观察点")  # 不将无匹配伪报为成功

    def _require_input_focus(self):  # 前台按键必须确认实际获得焦点
        if not self.wait_until(
            lambda: self.active_and_send_mouse_delta(only_activate=True), time_out=3, raise_if_not_found=False
        ):  # 复用现有前台输入接口并有界等待
            raise RuntimeError("游戏窗口未获得输入焦点")  # 不把未送达的按键当成完成

    def _camera_open(self, frame=None):  # 同帧双模板确认相机布局，不依赖退出提示文字
        frame = self.frame if frame is None else frame  # 固定本次识别所用截图
        return bool(
            self.find_one(fL.esc, box=self.box_of_screen(0.90, 0.55, 0.96, 0.61), frame=frame)
            and self.find_one(fL.plus_button, box=self.box_of_screen(0.59, 0.90, 0.64, 0.96), frame=frame)
        )  # 右侧退出键与底部焦距加号必须同时命中默认阈值

    def open_camera(self):  # 选择工具与进入拍照分开验证
        self.next_frame()  # 入口判断使用当前页面，不沿用上一动作的旧帧
        if self._camera_open():  # 已在相机时不再按 R 切换视角
            return  # 保持当前取景状态
        self._require_input_focus()  # 轮盘悬停使用真实光标
        tool_key = self._resolve_config_key("r", "common")  # 遵循快捷工具键设置
        self.send_key_down(tool_key)  # 长按打开工具轮盘
        try:  # 中止或异常也要释放工具键
            self.sleep(1)  # 长按工具键等待轮盘展开
            self._require_input_focus()  # 真实光标移动前再次检查目标窗口
            self.smooth_drag((0.5, 0.5), (0.396, 0.312), duration=0.3)  # 实测左上相机位置；此接口仅移动光标，不按鼠标
            self.sleep(0.2)  # 等待轮盘处理悬停位置后再松开
        finally:  # 不留下长按状态
            self.send_key_up(tool_key)  # 松开确认工具选择
        if not self.wait_until(
            self._camera_open, time_out=2, raise_if_not_found=False
        ):  # 松键后逐帧验证是否直接进入相机
            self._require_input_focus()  # 确认短按可送达
            self.press_key("r", down_time=0.15)  # 装备完成后再短按进入相机
            if not self.wait_until(self._camera_open, time_out=5, raise_if_not_found=False):  # 短按后验证相机双模板
                raise RuntimeError("未进入拍照界面")  # 不将固定输入当作成功打开相机

    def photo_condition_met(self, frame=None):  # 仅检查相机界面与条件圆勾
        frame = self.frame if frame is None else frame  # 固定相机布局和圆勾所用截图
        if not self._camera_open(frame=frame):  # 排除大世界或轮盘
            return False  # 非相机界面不能报告条件满足
        marker = self.find_one(  # 识别相机目标行的圆勾模板
            fL.photo_target_checked, box=self.box_of_screen(0.02, 0.655, 0.055, 0.715), frame=frame
        )  # 只在目标标记区域寻找，模板定位用于后续颜色检查
        if not marker:  # 未找到圆勾模板
            return False  # 不把未满足条件算作就绪
        yellow = {"r": (180, 255), "g": (155, 255), "b": (0, 120)}  # 亮起圆勾的黄色范围
        return calculate_color_percentage(frame, yellow, marker) > 0.03  # 灰白勾可能匹配模板，还需确认黄色亮起

    def rotate_photo_view(self):  # 在相机画面中央横向拖动，不触碰快门
        self._require_input_focus()  # 旋转视角需要实际光标移动
        self.next_frame()  # 手势前重新确认当前布局
        if not self._camera_open():  # 手势前确认仍在相机
            raise RuntimeError("调整视角前相机界面已关闭")  # 禁止在大世界发出鼠标手势
        start, end = (0.5, 0.45), (0.625, 0.45)  # 画面中央横向一小段，远离快门和左下目标
        self.mouse_down(*self.normalize_pos(start), key="left")  # 按住旋转视角用的左键
        try:  # 中止也必须释放鼠标
            self.smooth_drag(start, end, duration=0.3)  # 复用已有客户端坐标移动接口
            self.sleep(0.2)  # 等待旋转输入被游戏处理
        finally:  # 清理旋转手势
            self.mouse_up(key="left")  # 松开后才能检查稳定画面
        self.sleep(0.3)  # 刷新旋转后的目标状态

    def adjust_photo_view(self):  # 有界寻找满足条件的朝向，不移动角色
        for step in range(9):  # 初始状态加最多八次横向调整
            self.next_frame()  # 每轮使用调整后的新画面
            if self.photo_condition_met():  # 条件亮起立即停止调整
                return True  # 保留已满足的角度，不按快门
            if step < 8:  # 最后一次检查后不再增加位移
                self.rotate_photo_view()  # 只旋转镜头，不发出方向键
        return False  # 有限范围内未找到时如实返回

    def prepare_photo(self):  # 进入相机并检查取景条件
        self.open_camera()  # 装备并进入相机
        self.adjust_photo_view()  # 先检查条件，必要时小步转动镜头
        frame = self.next_frame()  # 检查最后一次调整后实际保留的界面
        if not self._camera_open(frame=frame):  # 不能沿用打开时的状态
            raise RuntimeError("小池荷取景结束时相机界面已关闭")  # 不返回与现场不符的 camera_open=True
        ready = bool(self.photo_condition_met(frame=frame))  # 用最后一帧重算条件，不沿用旋转过程中的旧结果
        self.log_info(
            f"[environment-monitoring] camera_open=True target=小池荷 photo_ready={ready} shutter=False"
        )  # 如实记录取景状态
        return {
            "name": "小池荷",
            "camera_open": True,
            "photo_ready": ready,
            "photo_taken": False,
        }  # 不触发快门或提交委托

    def run(self):  # GUI 与 MCP 共用从入口开始的可重复流程
        observation = self.prepare_observation()  # 自动选择当前支持的观察点并传送
        name = observation["name"]  # 保留实际识别名称用于错误提示
        route_name = self.observation_route(name)  # 获取当前点位注册的导航方法
        route = getattr(self.navigation, route_name, None) if route_name else None  # 不为未知点位调用其他路线
        if not callable(route):  # 路线缺失时不得继续相机阶段
            raise RuntimeError(f"未支持观察点导航：{name}")  # 未注册或无效的路线直接停止
        route()  # 执行传送后的点位导航
        return self.prepare_photo()  # 停在相机内，保留任务供下一轮验证
