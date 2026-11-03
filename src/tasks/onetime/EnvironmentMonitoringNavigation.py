from src.data.FeatureList import FeatureList as fL  # 复用已有追踪蓝点模板


class EnvironmentMonitoringNavigation:  # 共用传送后移动与目标对中
    def __init__(self, task):  # 复用所属任务的输入与生命周期
        self.task = task  # 不创建独立执行器

    def hold_route_key(self, key, duration, *, sprint=False):  # 执行可中止的移动操作
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

    def run_route(self, name, steps, *, sprint=False):  # 共用起步、分段移动和结束检查
        task = self.task  # 使用所属任务的大世界状态
        if not task.in_world():  # 必须从传送后的大世界起步
            raise RuntimeError(f"{name}路线未处于大世界")  # 不在菜单内发送移动
        for key, duration in steps:  # 各点位仅提供已确认的方向与时长
            self.hold_route_key(key, duration, sprint=sprint)  # 每段移动结束即松键
        if not task.wait_until(task.in_world, time_out=2, settle_time=0.5, raise_if_not_found=False):  # 等待大世界稳定
            raise RuntimeError(f"{name}路线结束后未恢复大世界")  # 不在异常状态进入相机

    def align_tracking_target(self, name):  # 由需要对中蓝点的路线调用
        task = self.task  # 使用所属任务的导航能力
        task._require_input_focus()  # 确认朝向调整按键可送达
        task.press_key("v", after_sleep=0)  # 显示当前追踪目标的方向标记
        markers = [  # 沿用现有蓝点的不同亮度模板
            fL.secondary_objective_direction_dot,
            fL.secondary_objective_direction_dot_light,
            fL.secondary_objective_direction_dot_light_two,
            fL.secondary_objective_direction_dot_light_three,
            fL.secondary_objective_direction_dot_light_fourth,
        ]  # 兼容不同背景亮度
        if not task.align_ocr_or_find_target_to_center(
            markers, only_x=True, ocr=False, threshold=0.8, max_time=10, raise_if_fail=False, allow_random_move=False
        ):  # 仅水平对中，不随机转向搜索
            raise RuntimeError(f"{name}追踪标记未对中")  # 不能确认朝向时停止


class PoolLotusNavigation(EnvironmentMonitoringNavigation):  # 小池荷传送后短路线
    def run(self):  # 向前一步，再向右走到竹子旁
        self.run_route("小池荷", (("w", 0.5), ("d", 3)))  # 保留已验证的移动时长
        self.align_tracking_target("小池荷")  # 导航结束后对中目标


class CisternOriginiumSlugsNavigation(EnvironmentMonitoringNavigation):  # 普通蓄水源石虫传送后短路线
    def run(self):  # 保持传送后的朝向，向后冲刺到取景位置
        self.run_route("蓄水源石虫", (("s", 7),), sprint=True)  # 后退冲刺到取景位置
        self.align_tracking_target("蓄水源石虫")  # 导航结束后对中目标


class ThilluNavigation(EnvironmentMonitoringNavigation):  # 表象传送落点即可取景
    def run(self):  # 原地等待传送后大世界稳定
        self.run_route("表象", ())  # 原地等待大世界稳定
        self.align_tracking_target("表象")  # 原地对中目标，不发送移动输入
