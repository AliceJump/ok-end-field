"""小地图定位领域常驻任务。

该 TriggerTask 是融合定位状态的唯一生产者：持续采样小地图里程计、消费地图 WS、
执行静止校准，并把最新快照暴露给导航、物品导航和实时位置测试任务。

关键约束：

- 定位实例必须常驻，不能因执行器短暂暂停或暂无消费者而清理 WS；
- 消费者通过 ``RuntimeStateMixin.world_pose(frame=...)`` 读取统一状态快照；
- 导航只读取 ``position_trusted`` 为真的绝对坐标；普通重锚后必须等待静止校准，
  良性 ``too_long_dt`` 换锚不撤销信任。

完整定位链路见 ``docs/dev/小地图定位.md``。
"""

import threading

from ok import TriggerTask
from qfluentwidgets import FluentIcon

from src.core.BaseEfTask import BaseEfTask
from src.core.global_config_store import MINIMAP_LEGACY_SOURCE_TASKS
from src.icons import Icons
from src.localization.minimap_position_mixin import (
    CONFIG_EXPERIMENTAL_AUTOSTART,
    CONFIG_EXPERIMENTAL_EXE,
    CONFIG_EXPERIMENTAL_PORT,
    CONFIG_EXPERIMENTAL_STOP_ON_EXIT,
    CONFIG_EXPERIMENTAL_UPDATE,
    CONFIG_USE_EXPERIMENTAL,
    MinimapPositionMixin,
)


class MinimapPositionTask(MinimapPositionMixin, BaseEfTask, TriggerTask):
    """统一维护小地图融合定位，供导航和其他实时任务消费。"""

    requires_foreground = True

    def __init__(self, *args, **kwargs):
        """注册触发任务元数据并初始化常驻定位状态。"""
        super().__init__(*args, **kwargs)
        self.name = "小地图定位"
        self.description = "持续融合小地图里程计、朝向和地图坐标，静止时自动校准"
        self.icon = Icons.Navigation
        self.trigger_interval = 0.2

        self.default_config.update(
            {
                "_enabled": True,
                **self.minimap_position_default_config(),
            }
        )
        self.config_description.update(self.minimap_position_config_description())
        # 实验技术用带风险确认的自定义开关（见 src/patches/danger_switch_patch.py）；
        # 端口与后端程序相关项只在打开时才显示，避免默认状态下多出一堆无意义的项。
        self.config_type[CONFIG_USE_EXPERIMENTAL] = {
            "type": "danger_switch",
            "sub_configs": {
                True: [
                    CONFIG_EXPERIMENTAL_PORT,
                    CONFIG_EXPERIMENTAL_EXE,
                    CONFIG_EXPERIMENTAL_AUTOSTART,
                    CONFIG_EXPERIMENTAL_STOP_ON_EXIT,
                    CONFIG_EXPERIMENTAL_UPDATE,
                ]
            },
        }
        # 手动更新按钮。回调用闭包捕获本任务实例——框架的按钮控件是直接
        # ``btn.clicked.connect(callback)``，不给任务上下文，而闭包天然带着 self。
        self.config_type[CONFIG_EXPERIMENTAL_UPDATE] = {
            "type": "button",
            "text": CONFIG_EXPERIMENTAL_UPDATE,
            "icon": FluentIcon.DOWNLOAD,
            "callback": lambda *_: self.check_capture_backend_update(),
        }
        self._init_minimap_position_mixin()
        # 定位服务是常驻生产者，不能因为执行器暂停或暂无消费者而主动断开 WS。
        self._map_ws_consumer_idle_timeout = 0.0
        self._last_status_key = None
        self._last_status_log_at = 0.0

    def post_init(self):
        """首次升级时，把旧寻路任务里的非默认运行参数迁移过来。

        真值来源与分辨率标定由 ``global_config_store`` 在任务 Config 构造前处理；
        这里只合并仍属于任务侧的 WS 等待、校准距离和里程计提交参数。

        来源限定在 :data:`MINIMAP_LEGACY_SOURCE_TASKS`：所有者只有一个槽位，来源越多
        结果越依赖"哪个任务先被实例化"。每次采用都会写日志。
        """
        executor = getattr(self, "_executor", None)
        if executor is None or not hasattr(executor, "get_all_tasks"):
            return
        defaults = self.minimap_position_default_config()
        for task in executor.get_all_tasks():
            if task is self or not getattr(task, "config", None):
                continue
            if type(task).__name__ not in MINIMAP_LEGACY_SOURCE_TASKS:
                continue
            for key, default in defaults.items():
                old_value = task.config.get(key)
                if old_value is None or old_value == default:
                    continue
                if self.config.get(key) != default:
                    continue
                self.config[key] = old_value
                self.log_info(f"已迁移定位配置 {key}: {old_value!r}（来源 {type(task).__name__}）")

    def run(self):
        """采样一拍定位并发布节流后的状态；本任务不结束自身。"""
        if not self.in_world():
            return False
        if self.experimental_capture_enabled():
            # 直通模式不读画面、不建里程计、不启地图 WS，也不需要分辨率标定档案
            # （抓包后端直接给绝对坐标），所以整段旧初始化都跳过。
            #
            # 这里的 ensure 只是**兜底**（后端中途挂了、或玩家在游戏已经跑起来之后才打开
            # 这个开关）。真正的拉起时机在游戏启动之前，见
            # src/patches/capture_backend_autostart_patch.py —— 本任务要等到游戏窗口能截屏
            # 才会被执行，那时登录握手早就过去了。
            self.ensure_capture_backend()
            state = self.sample_world_pose(now=self.active_time())
            self._report_capture_status(state, self.active_time())
            return False
        self.start_minimap_position(wait_stable=False)
        if not self.minimap_position_ready:
            return False
        frame = self.next_frame()
        if frame is None:
            return False
        state = self.sample_world_pose(frame=frame, now=self.active_time())
        x, z = state.get("x"), state.get("z")
        status_reported = False
        if state.get("just_synced") and state.get("sync_residual") is not None:
            residual = state["sync_residual"]
            message = (
                "静止校准：原始融合与新 WS 误差 "
                f"{float(residual['dist']):.3f}m "
                f"(dx={float(residual['dx']):+.3f}, "
                f"dz={float(residual['dz']):+.3f})"
            )
            corrected_dist = residual.get("visual_anchor_chain_dist")
            corrected_dx = residual.get("visual_anchor_chain_dx")
            corrected_dz = residual.get("visual_anchor_chain_dz")
            if corrected_dist is not None and corrected_dx is not None and corrected_dz is not None:
                message += (
                    " | 锚定链校正输出与同一 WS 误差 "
                    f"{float(corrected_dist):.3f}m "
                    f"(dx={float(corrected_dx):+.3f}, "
                    f"dz={float(corrected_dz):+.3f})"
                )
            self.log_info(message)
            if corrected_dist is None:
                info_text = f"{float(residual['dist']):.2f}m"
            else:
                info_text = f"原始 {float(residual['dist']):.2f}m / 校正 {float(corrected_dist):.2f}m"
            self.info_set("小地图校准", info_text)
            status_reported = True
        status = self._position_status_text(state, x, z)
        status_key = (
            str(state.get("map_id") or ""),
            bool(state.get("anchor_set")),
            bool(state.get("position_trusted", True)),
            status,
        )
        now = self.active_time()
        if not status_reported and (status_key != self._last_status_key or now - self._last_status_log_at >= 30.0):
            text = f"({float(x):.1f}, {float(z):.1f}) {status}" if x is not None and z is not None else status
            self.info_set("小地图定位", text)
            self._last_status_key = status_key
            self._last_status_log_at = now
        return False

    def _report_capture_status(self, state: dict, now: float) -> None:
        """直通模式的状态栏文本：坐标 + 地图 + 这条采样已经多久了。

        采样的陈旧度（``capture_age``）**只显示不拦截**：上报是事件驱动的，静止时本来就没有
        新包，年龄大不等于数据坏了。它在"坐标跳变"或"导航拿着旧位置走"时是唯一的线索。

        真正会拦截的是**连接活性**与**地图识别**（见 ``_capture_trust``）。所以坐标可用但不可信
        时，这里必须把原因显式写出来，否则状态栏看着和正常一模一样。
        """

        if now - self._last_status_log_at < 1.0:
            return
        x, z = state.get("x"), state.get("z")
        if x is None or z is None:
            text = f"抓包源无坐标({state.get('trust_reason') or 'unknown'})"
        else:
            y = state.get("y")
            age = state.get("capture_age")
            parts = [f"({float(x):.1f}, {float(z):.1f})"]
            if y is not None:
                parts.append(f"Y={float(y):.1f}")
            parts.append(str(state.get("map_id") or "未知地图"))
            if isinstance(age, (int, float)):
                parts.append(f"{float(age):.1f}s前")
            # 连接号只在"换了连接"时有意义，但一直显示才能看出它什么时候变——重登正好
            # 表现为这个数字跳一格。不可信时补上原因，别让它和正常帧长得一样。
            if state.get("capture_flow") is not None:
                parts.append(f"连接{state['capture_flow']}")
            if not state.get("position_trusted", True):
                parts.append(f"不可用({state.get('trust_reason') or 'unknown'})")
            text = "抓包 " + " ".join(parts)
        self.info_set("小地图定位", text)
        self._last_status_log_at = now

    @staticmethod
    def _position_status_text(state: dict, x, z) -> str:
        """生成定位任务状态栏文本。"""

        if x is None or z is None:
            reason = state.get("odom_reason") or "no_position"
            return f"未锚定({reason})"
        if not state.get("position_trusted", True):
            return "待校准"
        motion = "静止" if state.get("rest") else "移动"
        correction = state.get("visual_anchor_chain_correction_m")
        if correction is not None:
            return f"{motion} 锚定链校正={float(correction):.2f}m"
        return motion

    def on_danger_switch_confirmed(self, key: str) -> None:
        """风险开关被确认打开后的钩子（由 ``danger_switch_patch`` 回调）。

        在用户刚勾上「使用实验技术」时**后台**把后端拉下来：那时他还没启动游戏，正好用这段
        时间把 18 MB 拿完，点「启动游戏」时就不必干等。真正的兜底在启动前那一次
        ``ensure_capture_backend``，所以这里失败也不影响什么。
        """

        if key == CONFIG_USE_EXPERIMENTAL:
            self.prefetch_capture_backend()

    def check_capture_backend_update(self) -> None:
        """「检查并更新后端」按钮：忽略节流立刻查一次，并把结果回报到界面。

        跑在后台线程里——按钮回调在 GUI 线程上，18 MB 的下载同步做会把界面卡死。
        """

        def _run() -> None:
            try:
                path = self.capture_backend_installer().check_now()
            except Exception as exc:
                self.log_warning(f"实验技术：检查更新失败（{type(exc).__name__}）", notify=True)
                return
            if path is None or not path.is_file():
                self.log_warning("实验技术：没拿到后端程序，请检查网络后重试", notify=True)
                return
            self.log_info(f"实验技术：后端已就绪 —— {path}", notify=True)

        threading.Thread(target=_run, name="CaptureBackendCheckUpdate", daemon=True).start()

    def on_destroy(self):
        """任务销毁时停止 WS 客户端和本地位置源。

        由本应用拉起的抓包后端**默认留着不关**：它的状态绑在当前这一局连接上，关掉再拉起
        等于丢了登录握手，必须重启游戏才能恢复。要干净退出的用户把「停止定位时关闭后端」
        打开即可（见 ``CONFIG_EXPERIMENTAL_STOP_ON_EXIT``）。
        """
        try:
            self.stop_capture_backend()
            self.stop_minimap_position()
        finally:
            super().on_destroy()
