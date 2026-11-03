class EnvironmentMonitoringNavigation:  # 共用传送后移动与状态检查
    def __init__(self, task):  # 复用所属任务的输入与生命周期
        self.task = task  # 不创建独立执行器

    def hold_route_key(self, key, duration, sprint=False):  # 执行可中止的移动操作
        task = self.task  # 使用所属任务输入
        task._require_input_focus()  # 每段移动前确认焦点
        actual_key = task._resolve_config_key(key, "common")  # 遵循用户移动键绑定
        task.send_key_down(actual_key)  # 开始保持移动方向
        try:  # 中止、冲刺输入或等待异常都必须松键
            if sprint:  # 仅需要冲刺的路线启动加速
                task.press_key("shift", after_sleep=0.05)  # 沿用网格导航的冲刺方式
            task.sleep(duration)  # 使用可响应任务停止的等待
        finally:  # 配对清理输入
            task.send_key_up(actual_key)  # 不留下按住的移动键

    def run_route(self, name, steps, sprint=False):  # 共用起步、分段移动和结束检查
        task = self.task  # 使用所属任务的大世界状态
        if not task.in_world():  # 必须从传送后的大世界起步
            raise RuntimeError(f"{name}路线未处于大世界")  # 不在菜单内发送移动
        for key, duration in steps:  # 各点位仅提供已确认的方向与时长
            self.hold_route_key(key, duration, sprint=sprint)  # 每段移动结束即松键
        if not task.wait_until(task.in_world, time_out=2, settle_time=0.5, raise_if_not_found=False):  # 等待大世界稳定
            raise RuntimeError(f"{name}路线结束后未恢复大世界")  # 不在异常状态进入相机


class PoolLotusNavigation(EnvironmentMonitoringNavigation):  # 小池荷传送后短路线
    def run(self):  # 向前一步，再向右走到竹子旁
        self.run_route("小池荷", (("w", 0.5), ("d", 3)))  # 保留已验证的移动时长


class CisternOriginiumSlugsNavigation(EnvironmentMonitoringNavigation):  # 普通蓄水源石虫传送后短路线
    def run(self):  # 保持传送后的朝向，向后冲刺到取景位置
        self.run_route("蓄水源石虫", (("s", 7),), sprint=True)  # 后退冲刺到取景位置


class StationaryObservationNavigation(EnvironmentMonitoringNavigation):  # 传送落点即可取景
    def run(self):  # 原地等待传送后大世界稳定
        self.run_route("原地取景", ())  # 原地等待大世界稳定
