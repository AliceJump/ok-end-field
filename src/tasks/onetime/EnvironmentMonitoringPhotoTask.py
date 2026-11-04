import re  # 将观察点名称作为字面文本匹配
from typing import ClassVar  # 标明终端布局由类共享

from ok import Box, TaskDisabledException  # 保留选中背景检测的像素边界及任务中止语义
from qfluentwidgets import FluentIcon  # 复用日常任务分组图标

from src.data.FeatureList import FeatureList as fL  # 复用任务定位、地图和相机模板
from src.tasks.navigation.mixin.map_mixin import MapMixin  # 复用传送及到达确认
from src.tasks.onetime.EnvironmentMonitoringNavigation import (  # 各观察点导航组件共用短路线执行逻辑
    CisternOriginiumSlugsNavigation,  # 普通源石虫短路线
    PoolLotusNavigation,  # 小池荷短路线
    StationaryObservationNavigation,  # 传送后原地取景
)

OBSERVATIONS = {  # 观察点语言键对应导航类和横向取景方向
    "pool_lotus": (PoolLotusNavigation, "right"),  # 小池荷向右取景
    "cistern_originium_slugs": (CisternOriginiumSlugsNavigation, "left"),  # 普通源石虫向左取景
    "thillu": (StationaryObservationNavigation, "right"),  # 表象在传送落点向右取景
    "peach_tree": (StationaryObservationNavigation, "right"),  # 桃树在传送落点即可取景
}  # 仅注册已支持的点位


class EnvironmentMonitoringPhotoTask(MapMixin):  # 共用接取、传送与相机流程
    TERMINAL_LEFTS: ClassVar[dict[str, float]] = {"outskirts": 0.06, "marker_stone": 0.27}  # 固定顺序与标签空白区

    def __init__(self, *args, **kwargs):  # 构造阶段不操作游戏
        super().__init__(*args, **kwargs)  # 初始化项目任务与生命周期
        self.group_name = "日常任务"  # 环境监测属于日常业务
        self.group_icon = FluentIcon.CALENDAR  # 与现有日常子任务一致
        self.icon = FluentIcon.SEARCH  # 保持原有任务图标
        self.name = "环境监测"  # 任务名称不绑定某个观察点
        self.default_config = {}  # 路线固定，不允许将其用于其他观察点
        self.config_description = {}  # 不展示观察点名称配置

        self.selected_observation = None  # 每次执行重新建立交接信息
        self.navigation = None  # 匹配观察点后创建对应导航组件
        self.completed_observations = set()  # 本次运行已拍摄的点位

    @staticmethod  # 标题符号不是观察点身份的一部分
    def normalize_title(text):  # 兼容符号、空白与英文大小写差异
        return re.sub(r"[\W_]+", "", text).casefold()  # 保留各语言文字

    def observation_key(self, title):  # 按标题识别已注册路线的观察点
        normalized = self.normalize_title(title)  # 不依赖引号和空格
        matched = None  # 多个观察点同时匹配时不能猜测
        for key in OBSERVATIONS:  # 只识别支持导航的观察点
            name = getattr(self.lang.environment_monitoring, key, None)  # 从语言资源读取当前语言名称
            if isinstance(name, re.Pattern):  # 语言资源中的正则用于短识别词
                found = bool(normalized) and not name.search("") and bool(name.search(normalized))  # 不接受空匹配
            elif isinstance(name, str):  # 字面名称按点位选择匹配方式
                expected = self.normalize_title(name)  # 与标题使用相同的归一化规则
                found = bool(expected) and expected in normalized  # 其他点位允许名称只是标题的一部分
                if key == "cistern_originium_slugs":  # 同名活力版包含普通源石虫名称
                    found = found and normalized == expected  # 普通源石虫要求完整一致
            else:  # 没有可用的语言名称
                found = False  # 不将空值视为匹配
            if not found:  # 当前观察点不匹配
                continue  # 检查下一个已注册点位
            if matched is not None:  # 多个观察点匹配同一卡片
                return None  # 停止而不是根据注册顺序猜测
            matched = key  # 记录唯一匹配的观察点语言键
        return matched  # 未匹配时返回 None

    def observation_route(self, title):  # 仅已注册导航的观察点允许自动接取
        observation = OBSERVATIONS.get(self.observation_key(title))  # 查找已注册点位
        return observation[0] if observation else None  # 未匹配时返回 None

    def open_monitoring(self):  # 从任意可恢复页面进入环境监测
        self.ensure_main()  # 复用主界面恢复
        if not self.to_model_area("武陵"):  # 复用地区建设区域选择
            raise RuntimeError("无法进入武陵地区建设")  # 明确入口失败
        if not self.wait_ui_stable():  # 等待概览入场结束后寻找入口
            raise RuntimeError("地区建设页面未稳定")  # 不点击过渡画面
        if not self.wait_click_feature(
            fL.environment_monitoring_enter,
            box=self.box.right,  # 识别并点击环境监测入口图标
            time_out=5,  # 限制等待时间
            raise_if_not_found=False,  # 未找到交由下面停止
        ):  # 在地区建设概览中等待入口出现并点击
            raise RuntimeError("未找到环境监测入口")  # 不盲点其他模块
        if not self.wait_until(self._monitoring_page, time_out=5, raise_if_not_found=False):  # 验证确实进入终端页
            raise RuntimeError("点击环境监测入口后未进入终端列表")  # 未进入时停止，不再识别入口文字
        if not self.wait_ui_stable():  # 等待卡片入场完成
            raise RuntimeError("环境监测页面未稳定")  # 超时不扫描过渡画面

    def _terminal_area(self, terminal):  # 两个终端顺序固定，空白区兼作点击和选中检测区域
        left = self.TERMINAL_LEFTS[terminal]  # 内部标识定位左右标签，未知终端不回退到其他标签
        height, width = self.frame.shape[:2]  # 使用当前帧尺寸
        x, y = int(width * left), int(height * 0.145)  # 保持原裁剪的向下取整边界
        return Box(x, y, int(width * (left + 0.07)) - x, int(height * 0.16) - y)  # 避开文字与等级数字

    def _terminal_selected(self, terminal):  # 验证终端标签的白色选中背景
        white = dict.fromkeys(("r", "g", "b"), (186, 255))  # 严格保留各通道大于 185 的边界
        return self.find_color(white, self._terminal_area(terminal), threshold=0.7)  # 只有当前选中标签呈白底

    def _monitoring_page(self):  # 组合控件位置和终端配色，不能只用通用按键模板认定页面
        if not self.find_one(fL.q, box=self.box_of_screen(0.025, 0.12, 0.046, 0.155), use_gray_scale=True):
            return False  # 左侧终端切换键必须存在
        if not self.find_one(fL.e, box=self.box_of_screen(0.45, 0.12, 0.47, 0.155), use_gray_scale=True):
            return False  # 右侧终端切换键必须存在
        cyan = {"r": (0, 90), "g": (130, 240), "b": (130, 240)}  # 两个终端均有青色侧条
        for left in (0.046, 0.25):  # 限定已验证的两条竖线位置
            if not self.find_color(cyan, self.box_of_screen(left, 0.105, left + 0.003, 0.167), threshold=0.7):
                return False  # 避免其他菜单的 Q/E 控件误判
        return (
            sum(self._terminal_selected(terminal) for terminal in self.TERMINAL_LEFTS) == 1
        )  # 黑屏、双白底和未知状态均拒绝

    def select_terminal(self, terminal):  # 两个终端共用切换逻辑
        if not self._monitoring_page():  # 固定位置点击前确认终端页面
            raise RuntimeError("切换终端时未处于环境监测终端列表")  # 避免在其他页面点击
        white = dict.fromkeys(("r", "g", "b"), (186, 255))  # 复用选中标签的白色范围
        if not self.click_until_color(
            white, self._terminal_area(terminal), threshold=0.7, time_out=3, raise_if_not_found=False
        ):  # 点击标签直到选中背景出现
            raise RuntimeError("环境监测终端切换未完成")  # 超时不扫描旧卡片
        if not (self.wait_ui_stable() and self._monitoring_page() and self._terminal_selected(terminal)):
            raise RuntimeError("环境监测终端切换未完成")  # 确认页面和选中状态后才扫描

    def _observation_action(self, area):  # 仅在已确认终端页的卡片底部区分黄色接取、白色前往和无按钮
        sample = Box(
            area.x + int(area.width * 0.36),
            area.y + int(area.height * 0.2),
            int(area.width * 0.25),
            int(area.height * 0.6),
        )  # 中部空白避开左侧箭头装饰和右侧按钮文字
        yellow = {"r": (210, 255), "g": (200, 255), "b": (0, 90)}  # 已保存接取截图的亮黄背景
        if self.find_color(yellow, sample, threshold=0.9):
            return "accept"  # 接取按钮可以点一次等待白底
        if self.find_color(dict.fromkeys(("r", "g", "b"), (220, 255)), sample, threshold=0.9):
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
                raise RuntimeError("无法识别环境监测观察点标题")  # 识别异常不能当作没有符合条件的点位
            observations.append({"name": titles[0].name, "action_box": action_box})  # 保留动态操作区域
        return observations  # 返回当前终端候选

    def enter_observation(self, observation):  # 接取或继续指定卡片
        area = observation["action_box"]  # 始终局限于目标卡片
        if not self._monitoring_page():  # 颜色本身不具备页面身份，点击前再确认终端页
            raise RuntimeError("打开观察点前未处于环境监测终端列表")  # 禁止在其他页面按颜色点击
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
        for terminal in self.TERMINAL_LEFTS:  # 按固定左右顺序遍历两个终端
            self.select_terminal(terminal)  # 选择终端并等待稳定
            for observation in self.available_observations():  # 只考虑可操作卡片
                identity = self.normalize_title(observation["name"])  # 同名点位不区分终端
                if identity in self.completed_observations:  # 照片关闭后卡片可能仍显示可操作
                    continue  # 本次运行不重复拍摄
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
        self.log_info("[environment-monitoring] 没有符合条件的观察点，跳过")  # 没有支持的委托属于正常结果
        return None  # 不需要传送或执行后续路线

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
            if not self.wait_ui_stable(
                box=self.box_of_screen(0.3, 0.2, 0.7, 0.8), stable_time=1.2, max_wait=5, refresh_interval=0.2
            ):  # 长按期间等待轮盘区域稳定，稳定时间覆盖已验证的展开时长
                raise RuntimeError("工具轮盘未稳定")  # 不在持续变化的界面选择工具
            self._require_input_focus()  # 真实光标移动前再次检查目标窗口
            self.smooth_drag((0.5, 0.5), (0.396, 0.312), duration=0.3)  # 实测左上相机位置；此接口仅移动光标，不按鼠标
            if not self.wait_ui_stable(
                box=self.box_of_screen(0.3, 0.2, 0.7, 0.8), stable_time=0.3, max_wait=3, refresh_interval=0.1
            ):  # 等待悬停后的轮盘区域稳定
                raise RuntimeError("工具轮盘悬停未稳定")  # 不将过渡中的选择视为完成
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
        return self.find_color(yellow, marker, threshold=0.03, frame=frame)  # 同帧确认圆勾黄色亮起

    def rotate_photo_view(self, direction="right"):  # 在画面中央按点位方向拖动，不触碰快门
        if direction not in ("left", "right"):  # 当前只支持横向搜索
            raise ValueError(f"不支持的取景方向：{direction}")  # 禁止静默执行上下调整
        self._require_input_focus()  # 旋转视角需要实际光标移动
        self.next_frame()  # 手势前重新确认当前布局
        if not self._camera_open():  # 手势前确认仍在相机
            raise RuntimeError("调整视角前相机界面已关闭")  # 禁止在大世界发出鼠标手势
        start, end = (0.5, 0.45), (0.625, 0.45)  # 画面中央横向一小段，远离快门和左下目标
        if direction == "left":  # 普通源石虫向左搜索
            end = (0.375, 0.45)  # 与右移等幅，保持纵坐标不变
        self.mouse_down(*self.normalize_pos(start), key="left")  # 按住旋转视角用的左键
        try:  # 中止也必须释放鼠标
            self.smooth_drag(start, end, duration=0.3)  # 复用已有客户端坐标移动接口
        finally:  # 清理旋转手势
            self.mouse_up(key="left")  # 松开后才能检查稳定画面
        if not self.wait_ui_stable(stable_time=0.3, max_wait=3, refresh_interval=0.1):  # 等待旋转后的画面稳定
            raise RuntimeError("调整视角后画面未稳定")  # 不在持续变化的画面判断拍摄条件

    def adjust_photo_view(self):  # 有界寻找满足条件的朝向，不移动角色
        title = (self.selected_observation or {}).get("name", "")  # 使用入口保存的标题，不增加 OCR
        key = self.observation_key(title)  # 根据已注册的观察点选择取景策略
        _, direction = OBSERVATIONS.get(key, (None, "right"))  # 未识别时沿用默认方向
        if not self.wait_ui_stable(  # 等待相机目标标记区域入场稳定
            box=self.box_of_screen(0.02, 0.655, 0.055, 0.715), stable_time=0.5, max_wait=3, refresh_interval=0.1
        ):  # 只观察条件标记，避免背景动画影响稳定判断
            raise RuntimeError("相机目标条件区域未稳定")  # 不在过渡画面判断或转动
        max_steps = 8  # 各点位共用最大横向调整次数
        for step in range(max_steps + 1):  # 初始状态加有界微调
            self.next_frame()  # 每轮使用调整后的新画面
            if self.photo_condition_met():  # 条件亮起立即停止调整
                return True  # 保留已满足的角度，不按快门
            if step < max_steps:  # 最后一次检查后不再增加位移
                self.log_info(
                    f"[environment-monitoring] 取景条件未识别，向{direction}调整：{step + 1}/{max_steps}"
                )  # 记录转动原因
                self.rotate_photo_view(direction=direction)  # 只旋转镜头，不发出移动按键
        return False  # 有限范围内未找到时如实返回

    def prepare_photo(self):  # 进入相机并检查取景条件
        name = (self.selected_observation or {}).get("name", "")  # 返回实际观察点名称，不绑定小池荷
        self.open_camera()  # 装备并进入相机
        self.adjust_photo_view()  # 先检查条件，必要时小步转动镜头
        frame = self.next_frame()  # 检查最后一次调整后实际保留的界面
        if not self._camera_open(frame=frame):  # 不能沿用打开时的状态
            raise RuntimeError("取景结束时相机界面已关闭")  # 不返回与现场不符的 camera_open=True
        ready = bool(self.photo_condition_met(frame=frame))  # 用最后一帧重算条件，不沿用旋转过程中的旧结果
        self.log_info(
            f"[environment-monitoring] camera_open=True target={name} photo_ready={ready} shutter=False"
        )  # 如实记录取景状态
        return {
            "name": name,  # 保留入口实际识别结果
            "camera_open": True,
            "photo_ready": ready,
            "photo_taken": False,
        }  # 不触发快门或提交委托

    def take_photo(self):  # 已取景后拍照并关闭照片预览，不返回终端确认收录
        self._require_input_focus()  # 快门点击前确认焦点
        if not self.photo_condition_met(frame=self.next_frame()):  # 使用新帧重新确认相机与目标黄勾
            raise RuntimeError("拍照前目标条件已失效")  # 不根据旧取景结果盲拍
        self.click_relative(0.856, 0.803)  # 相机布局已确认，点击固定快门位置
        close_area = self.box_of_screen(0.94, 0, 0.98, 0.08)  # 限定照片预览右上角关闭按钮
        close = self.wait_feature(
            fL.close, box=close_area, use_gray_scale=True, time_out=8, raise_if_not_found=False
        )  # 复用已验证关闭模板
        if not close:  # 按快门后必须观察到照片预览
            raise RuntimeError("拍照后未出现照片预览关闭按钮")  # 不将输入发送成功当作拍照成功
        self.click(close)  # 用户要求拍照后点击关闭，不另外确认已收录
        if not self.wait_until(
            lambda: self._camera_open() or self.in_world(), time_out=5, raise_if_not_found=False
        ):  # 等待离开照片预览
            raise RuntimeError("拍照后照片预览未关闭")  # 关闭失败须如实报告

    def navigate_observation(self, observation):  # 按观察点注册表执行导航
        name = observation["name"]  # 保留实际识别名称用于错误提示
        navigation_class = self.observation_route(name)  # 获取当前点位注册的导航类
        if not callable(navigation_class):  # 路线缺失时不得继续相机阶段
            raise RuntimeError(f"未支持观察点导航：{name}")  # 未注册或无效的路线直接停止
        self.navigation = navigation_class(self)  # 导航组件复用当前任务的设备和生命周期
        self.navigation.run()  # 每个点位统一通过 run 执行导航

    def photograph_observation(self):  # 共用取景与拍照流程
        if not self.prepare_photo()["photo_ready"]:  # 未满足条件时不拍照
            self.mark_task_failure("未能调整到满足条件的取景角度")  # 复用保留首条失败原因的机制
            return False  # 日常执行器据此记录失败
        self.take_photo()  # 拍照并关闭照片预览
        return True  # 预览关闭后才报告成功

    def run(self):  # GUI 与一键日常共用完整流程
        try:  # 独立运行和一键日常共用执行与失败处理
            self.completed_observations = set()  # 每次运行重新扫描所有点位
            for _ in range(6):  # 两个终端各有三张卡片，最多处理六个点位
                self.navigation = None  # 每轮清除上一次观察点的导航组件
                observation = self.prepare_observation()  # 自动选择当前支持的观察点并传送
                if observation is None:  # 当前没有符合条件的观察点
                    return True  # 无任务属于正常完成
                self.navigate_observation(observation)  # 执行点位导航
                if not self.photograph_observation():  # 取景失败时不继续下一个点位
                    return False  # 保留失败结果
                identity = self.normalize_title(observation["name"])  # 与扫描时使用相同身份
                self.completed_observations.add(identity)  # 成功关闭预览后才排除重复点位
            return True  # 达到本次运行的最大点位数
        except TaskDisabledException:  # 任务停止必须继续传递给日常执行器
            raise  # 不将用户中止当成普通失败
        except Exception as error:  # 当前点位不可用或取景失败
            self.mark_task_failure(self.tr("环境监测执行失败：{error}").format(error=error))  # 记录具体原因
            return False  # 允许继续后续日常
