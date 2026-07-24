import threading
import time
from datetime import datetime
from typing import Any

import win32gui
from ok import BaseTask, TaskDisabledException, TriggerTask, WaitFailedException

from src.interaction.KeyConfig import KeyConfigManager
from src.interaction.ScreenPosition import ScreenPosition
from src.data.lang import get_lang_accessor
from src.core.base_mixin.account_override_mixin import AccountOverrideMixin
from src.core.base_mixin.game_flow_mixin import GameFlowMixin
from src.core.base_mixin.process_manager import ProcessManager
from src.core.base_mixin.runtime_mixin import RuntimeMixin
from src.core.base_mixin.window_arrow_drawing_mixin import WindowArrowDrawingMixin
from src.core.global_config_store import ENSURE_MAIN_ONCE_ACTION_SLEEP_NAME, KEY_CONFIG_NAME, get_global_config
from src.core.config_migration import migrate_config_file_keys
from src.core.game_window import find_game_hwnd
from src.config import config as app_config

# 覆写框架截图时间戳格式：日期_时分秒（无毫秒）
import ok.gui.debug.Screenshot as _ok_screenshot
_ok_screenshot.get_current_time_formatted = lambda: datetime.now().strftime("%Y%m%d_%H%M%S")


def back_window(prev):
    current = win32gui.GetForegroundWindow()

    if prev and win32gui.IsWindow(prev) and current != prev:
        try:
            win32gui.SetForegroundWindow(prev)
        except Exception:
            pass


def _extract_locale_from_object(obj: Any) -> str | None:
    """统一获取运行时 UI 语言。"""

    if obj is None:
        return None

    # 优先使用 executor.locale
    executor = getattr(obj, "executor", None)

    locale_obj = (
        getattr(executor, "locale", None)
        if executor is not None
        else getattr(obj, "locale", None)
    )

    if locale_obj is None:
        return None

    # 支持 enum / QLocale / 自定义 Locale 类
    if hasattr(locale_obj, "name"):
        try:
            name_attr = getattr(locale_obj, "name")
            value = name_attr() if callable(name_attr) else name_attr

            if value:
                return str(value)

        except Exception:
            pass

    return str(locale_obj)


def _round_ratio(value):
    try:
        return round(float(value), 3)
    except Exception:
        return value


def _screenshot_timestamp_prefix():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


class BaseEfTask(
    WindowArrowDrawingMixin,
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
        self.account_config_blacklist = set(getattr(self, "account_config_blacklist", ()))
        self.account_config_whitelist = set(getattr(self, "account_config_whitelist", ()))
        self.account_config_defaults = dict(getattr(self, "account_config_defaults", {}))
        self.account_config_description = dict(getattr(self, "account_config_description", {}))
        self.account_config_type = dict(getattr(self, "account_config_type", {}))
        self.box = ScreenPosition(self)  # 屏幕位置辅助对象，提供top/bottom/left/right等边界
        self.key_config = get_global_config(KEY_CONFIG_NAME)  # 获取全局热键配置
        self.once_sleep_time = get_global_config(ENSURE_MAIN_ONCE_ACTION_SLEEP_NAME).get(
            "SingleActionWithDelay", 1.5
        )  # 获取全局配置的单次动作睡眠时间
        self.key_manager = KeyConfigManager(self.key_config)  # 初始化热键管理器
        # 初始化窗口箭头绘制 Mixin
        self._init_window_arrow_drawing_mixin()

        # 语言访问器（按模块化 JSON 加载）
        try:
            self.lang = get_lang_accessor(self)
        except Exception:
            self.lang = get_lang_accessor(None)

        self._detector = None
        self._detector_lock = threading.Lock()
        self._yolo_loader = None
        self._yolo_model_key = None
        # WS 运行时基础状态（供触发式任务复用）
        self._ws_enabled = False
        self._ws_host = None
        self._ws_port = None
        self._ws_server_thread = None
        self._ws_loop = None
        self._ws_stop_event = None
        self._active_time_paused_total = 0.0
        self._task_pause_started_at = None
        self._seen_executor_pause_start = getattr(self.executor, "pause_start", None)

    def active_time(self) -> float:
        """Return monotonic task time with framework pauses excluded."""
        now = time.monotonic()
        executor = self.executor
        executor_pause_start = getattr(executor, "pause_start", None)
        current_executor_pause = 0.0

        if executor_pause_start != self._seen_executor_pause_start:
            pause_duration = max(0.0, time.time() - executor_pause_start)
            if executor.paused:
                current_executor_pause = pause_duration
            else:
                self._active_time_paused_total += pause_duration
                self._seen_executor_pause_start = executor_pause_start

        current_task_pause = 0.0
        if self._task_pause_started_at is not None:
            current_task_pause = max(0.0, now - self._task_pause_started_at)

        return now - self._active_time_paused_total - current_executor_pause - current_task_pause

    def sleep(self, timeout):
        """Sleep for active task time, keeping the deadline across pauses."""
        if timeout <= 0:
            return True

        deadline = self.active_time() + timeout
        while True:
            remaining = deadline - self.active_time()
            if remaining <= 0:
                return True
            super().sleep(min(remaining, 0.1))

    def pause(self):
        if not isinstance(self, TriggerTask) and self._task_pause_started_at is None:
            self._task_pause_started_at = time.monotonic()
        return super().pause()

    def unpause(self):
        if self._task_pause_started_at is not None:
            self._active_time_paused_total += max(0.0, time.monotonic() - self._task_pause_started_at)
            self._task_pause_started_at = None
        return super().unpause()

    def wait_until(self, condition, time_out=0, pre_action=None, post_action=None, settle_time=-1,
                   raise_if_not_found=False):
        """Framework wait_until variant whose timeout freezes while paused."""
        self.executor.reset_scene()
        start = self.active_time()
        if time_out == 0:
            time_out = self.executor.wait_scene_timeout
        settled = None

        while not self.executor.exit_event.is_set():
            if pre_action is not None:
                pre_action()
            self.next_frame()
            result = condition()
            if result:
                if settle_time == -1:
                    settle_time = self.executor.wait_until_settle_time
                if settle_time <= 0:
                    return result
                now = self.active_time()
                if settled is None:
                    settled = now
                elif now - settled > settle_time:
                    return result
                continue

            settled = None
            if post_action is not None:
                post_action()
            if self.active_time() - start > time_out:
                break

        if raise_if_not_found:
            raise WaitFailedException()
        return None

    def get_game_hwnd(self) -> int:
        """Return the game hwnd resolved from the configured window features."""
        hwnd = find_game_hwnd(app_config.get("windows", {}))
        if hwnd:
            return hwnd
        device_manager = getattr(getattr(self, "executor", None), "device_manager", None)
        hwnd_window = getattr(device_manager, "hwnd_window", None)
        return getattr(hwnd_window, "hwnd", 0)

    # ── 配置键迁移 ─────────────────────────────────────────
    def load_config(self):
        """走 MRO 收集各 mixin/任务的迁移表，执行后再加载配置。"""
        migrations = {}
        for klass in type(self).__mro__:
            table = getattr(klass, 'config_key_migrations', None)
            if table:
                migrations.update(table)
        migrate_config_file_keys(self.__class__.__name__, migrations)
        super().load_config()

    def box_of_screen(
            self,
            x=0,
            y=0,
            to_x=1.0,
            to_y=1.0,
            width=0.0,
            height=0.0,
            name=None,
            hcenter=False,
            vcenter=False,
            confidence=1.0,
    ):
        return super().box_of_screen(
            _round_ratio(x),
            _round_ratio(y),
            _round_ratio(to_x),
            _round_ratio(to_y),
            width=width,
            height=height,
            name=name,
            hcenter=hcenter,
            vcenter=vcenter,
            confidence=confidence,
        )

    def box_of_screen_scaled(
            self,
            original_screen_width,
            original_screen_height,
            x_original,
            y_original,
            to_x=0,
            to_y=0,
            width_original=0,
            height_original=0,
            name=None,
            hcenter=False,
            vcenter=False,
            confidence=1.0,
    ):
        return super().box_of_screen_scaled(
            original_screen_width,
            original_screen_height,
            _round_ratio(x_original),
            _round_ratio(y_original),
            _round_ratio(to_x),
            _round_ratio(to_y),
            width_original=width_original,
            height_original=height_original,
            name=name,
            hcenter=hcenter,
            vcenter=vcenter,
            confidence=confidence,
        )

    def click_relative(self, x, y, *args, **kwargs):
        return super().click_relative(_round_ratio(x), _round_ratio(y), *args, **kwargs)

    def middle_click_relative(self, x, y, *args, **kwargs):
        return super().middle_click_relative(_round_ratio(x), _round_ratio(y), *args, **kwargs)

    @property
    def runtime_locale(self) -> str | None:
        return _extract_locale_from_object(self)

    def screenshot_timestamp_prefix(self):
        return _screenshot_timestamp_prefix()

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

    def iter_multi_account_context(self, repeat_times: int = 1, empty_accounts_message: str | None = None,
                                   account_log_suffix: str = "", allow_multi_account: bool = True):
        """统一多账号执行上下文。

        当开启多账户模式时，会先读取账号列表；列表为空则直接结束当前任务。
        每次迭代前会自动设置当前账号、记录启动日志并执行登录流程。

        Args:
            repeat_times: 非多账户模式下的执行轮数。
            empty_accounts_message: 账号列表为空时的提示文案。
            account_log_suffix: 账号启动日志的后缀文本。

        Yields:
            tuple[int, int]: 当前轮次索引和总轮数。
        """
        accounts_bool = self.config.get("多账户模式", False) and allow_multi_account
        if accounts_bool:
            accounts_list = self.get_account_list()
            if not accounts_list:
                if empty_accounts_message:
                    self.log_info(empty_accounts_message, notify=True)
                return
            repeat_times = len(accounts_list)
        else:
            accounts_list = []

        for repeat_idx in range(repeat_times):
            if accounts_bool:
                account = accounts_list[repeat_idx]
                username = str(account.get("username", "")).strip()
                account_id = str(account.get("account_id", "")).strip() or username
                if not username:
                    self.log_info(f"第 {repeat_idx + 1}/{repeat_times} 个账号为空，已跳过")
                    continue

                self.set_current_account(username, account_id)
                self.log_info(f"开始第 {repeat_idx + 1}/{repeat_times} 个账号({username[-4:]}){account_log_suffix}")
                self.login_flow(username)
            else:
                self.set_current_account("", "")

            yield repeat_idx, repeat_times

    def handle_task_exception(self, e: Exception, prefix: str):
        """统一处理任务 run() 中的异常逻辑。

        - 截图（前缀基于日期 + prefix）
        - 根据配置 `发生异常时终止游戏` 决定是继续（记录日志）还是终止（记录并不抛出）
        - 对于 `TaskDisabledException` 总是重新抛出以便上层处理
        """
        try:
            self.screenshot(prefix)
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

    def mark_task_failure(self, message: str, task_name: str | None = None):
        """统一标记任务失败消息，并截图（包含时间和任务名称）。

        在日常任务编排器可用时写入 runner.failure_details；
        否则退化为普通日志，避免在独立任务中报错。
        """
        runner = getattr(self, "daily_runner", None)
        name = task_name or getattr(self, "current_task", None) or "UnknownTask"
        if runner is not None and hasattr(runner, "get_current_task_name"):
            name = task_name or runner.get_current_task_name() or name
        try:
            self.screenshot(f"fail_{name}")
        except Exception:
            pass

        if runner is not None and hasattr(runner, "set_task_failure"):
            runner.set_task_failure(message, task_name=task_name, screenshot_taken=True)
            return
        self.log_info(str(message))

    def on_destroy(self):
        self.release_yolo_detector()
        super().on_destroy()

    def register_config_groups(self, groups: dict, dropdown_name: str = "配置选择"):
        """
        注册配置分组，支持下拉切换 + 子配置折叠显示
        """
        # 初始化必要属性
        if not hasattr(self, "default_config") or self.default_config is None:
            self.default_config = {}

        if not hasattr(self, "config_type") or self.config_type is None:
            self.config_type = {}

        # 1. 创建下拉选择框
        dropdown_key = dropdown_name
        group_names = list(groups.keys())

        if not group_names:
            print("警告: groups 为空")
            return

        # 注册下拉框配置类型
        self.config_type[dropdown_key] = {
            "type": "drop_down",
            "options": group_names,
            "sub_configs": groups,  # 关键：用于框架实现折叠逻辑
        }

        # 2. 设置默认选中第一个分组
        self.default_config[dropdown_key] = group_names[0]

        # 3. 为所有配置项补充默认值（安全处理）
        for group_items in groups.values():
            for item in group_items:
                if isinstance(item, str):
                    key = item
                else:
                    # 处理 self.CFG_XXX 常量的情况
                    key = str(item)

                # 关键修复：避免 NoneType 错误
                if key not in self.default_config:
                    if hasattr(self, "config") and self.config is not None and key in self.config:
                        self.default_config[key] = self.config[key]
                    else:
                        # 给一个合理的默认值，后续 register_config 会根据类型覆盖
                        self.default_config[key] = None

        self.config_description.update({
            dropdown_key: "配置默认隐藏，选择后展开对应配置项。"
        })
