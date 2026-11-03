class CisternOriginiumSlugsNavigation:  # 普通蓄水源石虫的传送后短路线
    def __init__(self, task):  # 复用所属任务的输入与生命周期
        self.task = task  # 不创建独立执行器

    def run(self):  # 保持传送后的朝向，向后跑到取景位置
        if not self.task.in_world():  # 仅允许从传送后的大世界起步
            raise RuntimeError("蓄水源石虫路线未处于大世界")  # 不在菜单内移动
        self.task._require_input_focus()  # 确保移动按键送到游戏
        key = self.task._resolve_config_key("s", "common")  # 遵循用户后退键绑定
        self.task.send_key_down(key)  # 先保持后退方向，不调整传送后的朝向
        try:  # 停止或异常时也必须松开移动键
            self.task.press_key("shift", after_sleep=0.05)  # 沿用网格导航：保持移动时短按一次 Shift 进入冲刺
            self.task.sleep(7)  # 用户指定冲刺七秒；已实测可进入普通源石虫取景范围
        finally:  # 保证输入配对释放
            self.task.send_key_up(key)  # 停下后再进入共用相机流程
        if not self.task.wait_until(
            self.task.in_world, time_out=2, settle_time=0.5, raise_if_not_found=False
        ):  # 确认移动结束后仍处于稳定大世界
            raise RuntimeError("蓄水源石虫路线结束后未恢复大世界")  # 不在异常状态打开相机
