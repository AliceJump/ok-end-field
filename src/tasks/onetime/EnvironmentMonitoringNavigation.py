OBSERVATION_ROUTES = {"pool_lotus": "walk_pool_lotus_route"}  # 语言无关的观察点标识及导航方法


class EnvironmentMonitoringNavigation:  # 使用所属任务的输入和识别能力执行导航
    def __init__(self, task):  # 复用任务的生命周期和设备
        self.task = task  # 不创建独立任务或执行器

    def _hold_route_key(self, key, duration):  # 固定路线按键保证停止时释放
        self.task._require_input_focus()  # 每段移动前确认焦点
        actual_key = self.task._resolve_config_key(key, "common")  # 遵循用户移动键绑定
        self.task.send_key_down(actual_key)  # 开始移动
        try:  # 中止或异常也不能留下按住的移动键
            self.task.sleep(duration)  # 使用可响应任务停止的等待
        finally:  # 配对清理输入
            self.task.send_key_up(actual_key)  # 松开移动键

    def walk_pool_lotus_route(self):  # 用户确认的短路线，无地图导航
        if not self.task.in_world():  # 必须从传送后的大世界起步
            raise RuntimeError("小池荷路线未处于大世界")  # 不在菜单发送移动
        self._hold_route_key("w", 0.5)  # 用户更正为传送后先向前一步
        self._hold_route_key("d", 3)  # 用户确认原先右移过量，缩短至竹子旁停下
        if not self.task.wait_until(
            self.task.in_world, time_out=2, settle_time=0.5, raise_if_not_found=False
        ):  # 等待大世界状态持续稳定，排除落水或黑场过渡
            raise RuntimeError("小池荷路线结束后未恢复大世界")  # 不在不可操作状态继续开相机
        self.task._require_input_focus()  # 确认追踪视角按键可送达
        self.task.press_key("v", after_sleep=0.6)  # 复用追踪任务的朝向调整
