def _hold_route_key(task, key, duration, *, sprint=False):  # 两条短路线共用可中止的移动操作
    task._require_input_focus()  # 每段移动前确认焦点
    actual_key = task._resolve_config_key(key, "common")  # 遵循用户移动键绑定
    task.send_key_down(actual_key)  # 开始保持移动方向
    try:  # 中止、冲刺输入或等待异常都必须松键
        if sprint:  # 仅需要冲刺的路线启动加速
            task.press_key("shift", after_sleep=0.05)  # 沿用网格导航：移动时短按一次 Shift
        task.sleep(duration)  # 使用可响应任务停止的等待
    finally:  # 配对清理输入
        task.send_key_up(actual_key)  # 不留下按住的移动键


def _run_route(task, name, steps, *, sprint=False, face_target=False):  # 共用起步、分段移动和结束检查
    if not task.in_world():  # 必须从传送后的大世界起步
        raise RuntimeError(f"{name}路线未处于大世界")  # 不在菜单内发送移动
    for key, duration in steps:  # 各点位仅提供已确认的方向与时长
        _hold_route_key(task, key, duration, sprint=sprint)  # 每段移动结束即松键
    if not task.wait_until(task.in_world, time_out=2, settle_time=0.5, raise_if_not_found=False):  # 等待大世界状态稳定
        raise RuntimeError(f"{name}路线结束后未恢复大世界")  # 不在异常状态进入相机
    if face_target:  # 仅小池荷需要追踪目标调整朝向
        task._require_input_focus()  # 确认朝向调整按键可送达
        task.press_key("v", after_sleep=0.6)  # 复用追踪任务朝向调整


class PoolLotusNavigation:  # 小池荷传送后短路线
    def __init__(self, task):  # 复用所属任务的输入与生命周期
        self.task = task  # 不创建独立执行器

    def run(self):  # 向前一步，再向右走到竹子旁
        _run_route(self.task, "小池荷", (("w", 0.5), ("d", 3)), face_target=True)  # 保留已验证的时长与结束朝向


class CisternOriginiumSlugsNavigation:  # 普通蓄水源石虫传送后短路线
    def __init__(self, task):  # 复用所属任务的输入与生命周期
        self.task = task  # 不创建独立执行器

    def run(self):  # 保持传送后的朝向，向后冲刺到取景位置
        _run_route(self.task, "蓄水源石虫", (("s", 7),), sprint=True)  # 七秒已实测能进入取景范围，无需按 V
