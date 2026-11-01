class EnvironmentMonitoringNavigationMixin:  # 各观察点传送后的路线独立维护
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
        self._hold_route_key("w", 0.5)  # 传送后先向前一步
        self._hold_route_key("d", 3)  # 向右移动至竹子旁
        if not self.wait_until(
            self.in_world, time_out=2, settle_time=0.5, raise_if_not_found=False
        ):  # 等待大世界状态持续稳定，排除落水或黑场过渡
            raise RuntimeError("小池荷路线结束后未恢复大世界")  # 不在不可操作状态继续开相机
        self._require_input_focus()  # 确认追踪视角按键可送达
        self.press_key("v", after_sleep=0.6)  # 复用追踪任务的朝向调整

    def navigate_observation(self, observation):  # 根据已选中的观察点调用对应路线
        name = self.normalize_title(observation["name"])  # 去除 OCR 标题的引号与空白
        route = OBSERVATION_ROUTES.get(name)  # 支持列表与路线使用同一份注册表
        if route is None:  # 未注册点位不可套用其他路线
            raise RuntimeError(f"未支持观察点导航：{name}")  # 不进入相机或错误路线
        route(self)  # 执行该点位的传送后导航


OBSERVATION_ROUTES = {"小池荷": EnvironmentMonitoringNavigationMixin.walk_pool_lotus_route}  # 新点位注册对应导航
