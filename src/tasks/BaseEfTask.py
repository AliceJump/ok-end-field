import threading

import win32gui
from ok import BaseTask

from src.interaction.KeyConfig import KeyConfigManager
from src.interaction.ScreenPosition import ScreenPosition
from src.tasks.mixin.account_override_mixin import AccountOverrideMixin
from src.tasks.mixin.game_flow_mixin import GameFlowMixin
from src.tasks.mixin.process_manager import ProcessManager
from src.tasks.mixin.runtime_mixin import RuntimeMixin


def back_window(prev):
    current = win32gui.GetForegroundWindow()

    if prev and win32gui.IsWindow(prev) and current != prev:
        try:
            win32gui.SetForegroundWindow(prev)
        except Exception:
            pass


class BaseEfTask(
    AccountOverrideMixin,
    GameFlowMixin,
    RuntimeMixin,
    BaseTask,
    ProcessManager,
):
    """游戏自动化任务基类，提供通用的交互和识别功能。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._logged_in = False  # 记录是否已登录游戏
        self.current_user = ""  # 记录当前用户
        self.current_account_id = ""  # 记录当前账号稳定ID（优先用于账号覆盖）
        self.support_multi_account = False  # 明确标识该任务是否支持多账号执行逻辑

        self.box = ScreenPosition(self)  # 屏幕位置辅助对象，提供top/bottom/left/right等边界
        self.key_config = self.get_global_config("Game Hotkey Config")  # 获取全局热键配置
        self.once_sleep_time = self.get_global_config("Ensure Main Once Action Sleep").get(
            "SingleActionWithDelay", 1.5
        )  # 获取全局配置的单次动作睡眠时间
        self.key_manager = KeyConfigManager(self.key_config)  # 初始化热键管理器

        self._detector = None
        self._detector_loading = False
        self._detector_loaded_event = threading.Event()
        self._start_detector_loading()

    def set_current_account(self, username, account_id):
        """设置当前账号信息，供账号覆盖功能使用。

        调用时机：
            应在任何依赖账号覆盖的配置读取或任务执行前调用。通常在账号
            登录上下文已经确定、但尚未开始读取账号相关配置时设置。

        多次调用行为：
            允许重复调用。后一次调用会直接覆盖此前保存的
            ``self.current_user`` 和 ``self.current_account_id``，并重新执行
            ``_bind_account_aware_config_get()``，使后续配置获取逻辑以最新
            的账号信息为准。

        参数约束：
            username:
                当前账号对应的用户名/显示名，应为字符串。建议传入稳定、可
                识别的原始用户名，不要传入 ``None``、临时拼接值或仅用于显示
                的不稳定别名。
            account_id:
                当前账号的稳定唯一标识，应为字符串。账号覆盖逻辑优先使用该值，
                因此应尽量传入跨会话保持不变的账号ID，而不是可能变化的昵称、
                索引或临时标记。
        """
        self.current_user = username
        self.current_account_id = account_id
        self._bind_account_aware_config_get()
