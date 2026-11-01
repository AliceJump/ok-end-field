from ok import TaskDisabledException  # 诊断异常不能吞掉用户停止请求

from src.tasks.onetime.environment_monitoring_photo import xiaochihe_target_checked  # 识别拍摄目标的圆勾状态
from src.tasks.onetime.EnvironmentMonitoringEntryTask import EnvironmentMonitoringEntryTask  # 复用委托选择和快捷传送


class EnvironmentMonitoringPhotoTask(EnvironmentMonitoringEntryTask):  # 小池荷可重复取景，不消耗委托
    def __init__(self, *args, **kwargs):  # 构造阶段不操作游戏
        super().__init__(*args, **kwargs)  # 保留工具分组和入口复用
        self.name = "环境监测：小池荷取景"  # 明确当前只支持小池荷
        self.description = "前往小池荷，走到竹子旁并打开相机，检查拍摄条件后停止，不拍照。"  # 保留委托供重复实测
        self.default_config = {}  # 路线固定，不允许将其用于其他观察点
        self.config_description = {}  # 不继承前半流程的自由目标配置

    def _require_input_focus(self):  # 前台按键必须确认实际获得焦点
        if not self.wait_until(
            lambda: self.active_and_send_mouse_delta(only_activate=True), time_out=3, raise_if_not_found=False
        ):  # 复用现有前台输入接口并有界等待
            raise RuntimeError("游戏窗口未获得输入焦点")  # 不把未送达的按键当成完成

    def _hold_route_key(self, key, duration):  # 固定路线按键保证停止时释放
        self._require_input_focus()  # 每段移动前确认焦点
        actual_key = self._resolve_config_key(key, "common")  # 遵循用户移动键绑定
        self.send_key_down(actual_key)  # 开始移动
        try:  # 中止或异常也不能留下按住的移动键
            self.sleep(duration)  # 使用可响应任务停止的等待
        finally:  # 配对清理输入
            self.send_key_up(actual_key)  # 松开移动键

    def walk_pool_lotus_route(self):  # 用户确认的短路线，无地图导航
        if not self.in_world():  # 必须从传送后的大世界起步
            raise RuntimeError("小池荷路线未处于大世界")  # 不在菜单发送移动
        if self.ocr(
            match=self.lang.environment_monitoring.industrial, box=self.box_of_screen(0, 0, 0.13, 0.08)
        ):  # 检查工业模式
            self._require_input_focus()  # 确认模式切换可送达
            self.press_key("tab", after_sleep=0.5)  # 切回探索模式
        self._hold_route_key("w", 0.5)  # 用户更正为传送后先向前一步
        self._hold_route_key("d", 3)  # 用户确认原先右移过量，缩短至竹子旁停下
        self.sleep(0.5)  # 松开移动键后等待碰撞和角色动作收尾
        if not self.wait_until(
            self.in_world, time_out=2, settle_time=0.5, raise_if_not_found=False
        ):  # 排除落水或黑场过渡
            raise RuntimeError("小池荷路线结束后未恢复大世界")  # 不在不可操作状态继续开相机
        self._require_input_focus()  # 确认追踪视角按键可送达
        self.press_key("v", after_sleep=0.6)  # 复用追踪任务的朝向调整

    def open_camera(self):  # 选择工具与进入拍照分开验证
        photo_ui = self.lang.environment_monitoring.camera_exit  # 相机界面的专属提示
        if self.ocr(match=photo_ui, box=self.box.right):  # 已在相机时不再按 R 切换视角
            return  # 保持当前取景状态
        self._require_input_focus()  # 轮盘悬停使用真实光标
        tool_key = self._resolve_config_key("r", "common")  # 遵循快捷工具键设置
        self.send_key_down(tool_key)  # 长按打开工具轮盘
        try:  # 无论识别成功与否均释放工具键
            if not self.wait_ocr(match=self.lang.environment_monitoring.tool_wheel, time_out=3):  # 等待轮盘实际展开
                try:  # 错误现场采集失败不能覆盖实际流程错误
                    self.log_info(
                        f"[environment-monitoring] tool-wheel-missing: {self.ocr()}"
                    )  # 保留长按期间的实际页面提示
                    self.screenshot(
                        "environment-monitoring-tool-wheel-missing", frame=self.frame
                    )  # 松开按键前保存错误现场
                except TaskDisabledException:  # 用户停止仍优先传播
                    raise  # 外层 finally 负责释放工具键
                except Exception:  # 诊断失败不影响轮盘未打开的判断
                    pass  # 保留下面的业务错误
                raise RuntimeError("快捷工具轮盘未打开")  # 不在大世界盲移光标
            self._require_input_focus()  # 真实光标移动前再次检查目标窗口
            self.smooth_drag((0.5, 0.5), (0.396, 0.312), duration=0.3)  # 实测左上相机位置；此接口仅移动光标，不按鼠标
            if not self.wait_ocr(
                match=self.lang.environment_monitoring.camera_tool,
                box=self.box_of_screen(0.4, 0.43, 0.6, 0.56),
                time_out=3,
            ):  # 中央文字变成拍照才说明悬停选中成功
                raise RuntimeError("工具轮盘未选中相机")  # 禁止激活未知工具
        finally:  # 不留下长按状态
            self.send_key_up(tool_key)  # 松开确认工具选择
        if not self.wait_ocr(match=photo_ui, box=self.box.right, time_out=2):  # 兼容选中已装备相机时直接进入的情况
            self._require_input_focus()  # 确认短按可送达
            self.press_key("r", down_time=0.15)  # 装备完成后再短按进入相机
            if not self.wait_ocr(match=photo_ui, box=self.box.right, time_out=5):  # 必须验证真实相机界面
                raise RuntimeError("相机工具已选择，但未进入拍照界面")  # 不将装备图标误判为相机打开

    def photo_condition_met(self):  # 目标身份与勾选必须来自同一帧
        if not self.ocr(match=self.lang.environment_monitoring.camera_exit, box=self.box.right):  # 排除大世界或轮盘
            return False  # 非相机界面不能报告条件满足
        if not self.ocr(
            match=self.lang.environment_monitoring.pool_lotus_photo, box=self.box_of_screen(0.04, 0.66, 0.28, 0.72)
        ):  # 确认圆勾对应小池荷目标
            return False  # 不把其他任务的标记算进来
        return xiaochihe_target_checked(self.frame)  # 检查左下目标行的圆勾，不依赖场景内容

    def rotate_photo_view(self):  # 在相机画面中央横向拖动，不触碰快门
        self._require_input_focus()  # 旋转视角需要实际光标移动
        if not self.ocr(match=self.lang.environment_monitoring.camera_exit, box=self.box.right):  # 手势前确认仍在相机
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

    def prepare_photo(self):  # 传送后可独立复测的后半流程
        self.walk_pool_lotus_route()  # 前进一步再向右撞竹子
        self.open_camera()  # 装备并进入相机
        ready = self.adjust_photo_view()  # 先检查条件，必要时小步转动镜头
        self.next_frame()  # 检查最后一次调整后实际保留的界面
        if not self.ocr(match=self.lang.environment_monitoring.camera_exit, box=self.box.right):  # 不能沿用打开时的状态
            raise RuntimeError("小池荷取景结束时相机界面已关闭")  # 不返回与现场不符的 camera_open=True
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
        self.prepare_observation("小池荷")  # 复用已验证的识别、接取、追踪和传送
        return self.prepare_photo()  # 停在相机内，保留任务供下一轮验证
