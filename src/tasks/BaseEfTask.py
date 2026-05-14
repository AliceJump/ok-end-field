import threading
from datetime import datetime

import win32gui
from ok import BaseTask, TaskDisabledException

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
        self.default_config_group = {}  # 配置项分组信息，格式为 { "分组名称": ["配置项1", "配置项2"] }
        self.box = ScreenPosition(self)  # 屏幕位置辅助对象，提供top/bottom/left/right等边界
        self.key_config = self.get_global_config("Game Hotkey Config")  # 获取全局热键配置
        self.once_sleep_time = self.get_global_config("Ensure Main Once Action Sleep").get(
            "SingleActionWithDelay", 1.5
        )  # 获取全局配置的单次动作睡眠时间
        self.key_manager = KeyConfigManager(self.key_config)  # 初始化热键管理器

        self._detector = None
        self._detector_loading = False
        self._detector_loaded_event = threading.Event()
        self._yolo_loader = None
        self._yolo_model_key = None
        self._start_detector_loading()
    def handle_task_exception(self, e: Exception, prefix: str):
        """统一处理任务 run() 中的异常逻辑。

        - 截图（前缀基于日期 + prefix）
        - 根据配置 `发生异常时终止游戏` 决定是继续（记录日志）还是终止（记录并不抛出）
        - 对于 `TaskDisabledException` 总是重新抛出以便上层处理
        """
        try:
            self.screenshot(f'{datetime.now().strftime("%Y%m%d")}_{prefix}')
        except Exception:
            pass

        if not self.config.get("发生异常时终止游戏", False):
            self.log_info("发生异常，继续游戏", notify=True)
            raise e
        else:
            if isinstance(e, TaskDisabledException):
                self.log_info("发生异常，继续游戏", notify=True)
                raise e
            else:
                self.log_info("发生异常，终止游戏", notify=True)
