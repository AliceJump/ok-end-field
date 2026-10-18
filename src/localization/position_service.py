"""Application-level world-position service.

The service owns long-lived WS/odometry/fusion state but does not depend on a
TriggerTask being registered or enabled. It is created on first use and lives
with the executor until application shutdown.
"""

from __future__ import annotations

import threading

from src.core.BaseEfTask import BaseEfTask
from src.core.global_config_store import get_global_config
from src.core.PositionConfig import CONFIG_USE_EXPERIMENTAL, POSITION_CONFIG_NAME
from src.localization.minimap_position_mixin import MinimapPositionMixin
from src.tasks.mixin.login_mixin import LoginMixin

_SERVICE_ATTR = "_world_pose_service"
_SERVICE_LOCK = threading.Lock()
_UNSET = object()


class WorldPoseService(MinimapPositionMixin, LoginMixin, BaseEfTask):
    """Continuously maintain fused world position for on-demand consumers."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "World Pose Service"
        self._init_minimap_position_mixin()
        # Positioning is retained after first use until application shutdown.
        self._map_ws_consumer_idle_timeout = 0.0
        self._last_status_key = None
        self._last_status_log_at = 0.0

    def load_config(self):
        """Use the application-level global config instead of a task JSON file."""

        self.config = get_global_config(POSITION_CONFIG_NAME)

    def sample_world_pose(self, frame=None, *, now=None) -> dict:
        """Sample one frame on demand and publish it to the runtime state hub."""

        if not self.experimental_capture_enabled():
            self.start_minimap_position(wait_stable=False)
            if not self.minimap_position_ready:
                return {}
            if frame is None:
                frame = self.next_frame()
            state = super().sample_world_pose(frame=frame, now=now)
            self._report_position_status(state, now)
            return state

        self.ensure_capture_backend()
        state = super().sample_world_pose(frame=frame, now=now)
        if state.get("x") is None:
            self.ensure_capture_session(reason="抓包没有坐标")
        self._report_capture_status(state, self._now(now))
        return state

    def _report_position_status(self, state: dict, now=None) -> None:
        x, z = state.get("x"), state.get("z")
        status = self._position_status_text(state, x, z)
        status_key = (
            str(state.get("map_id") or ""),
            bool(state.get("anchor_set")),
            bool(state.get("position_trusted", True)),
            status,
        )
        current = self._now(now)
        if status_key != self._last_status_key or current - self._last_status_log_at >= 30.0:
            text = f"({float(x):.1f}, {float(z):.1f}) {status}" if x is not None and z is not None else status
            self.info_set("小地图定位", text)
            self._last_status_key = status_key
            self._last_status_log_at = current

    def _report_capture_status(self, state: dict, now: float) -> None:
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
            if state.get("capture_flow") is not None:
                parts.append(f"连接{state['capture_flow']}")
            if not state.get("position_trusted", True):
                parts.append(f"不可用({state.get('trust_reason') or 'unknown'})")
            text = "抓包 " + " ".join(parts)
        self.info_set("小地图定位", text)
        self._last_status_log_at = now

    @staticmethod
    def _position_status_text(state: dict, x, z) -> str:
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
        if key == CONFIG_USE_EXPERIMENTAL:
            self.prefetch_capture_backend()

    def check_capture_backend_update(self) -> None:
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
        try:
            self.stop_capture_backend()
            self.stop_minimap_position()
        finally:
            super().on_destroy()


def get_world_pose_service(executor=_UNSET):
    """Return the executor-scoped position service, creating it on first use."""

    if executor is _UNSET:
        from ok import og

        executor = getattr(og, "executor", None)
    if executor is None:
        return None

    service = getattr(executor, _SERVICE_ATTR, None)
    if service is not None:
        return service

    with _SERVICE_LOCK:
        service = getattr(executor, _SERVICE_ATTR, None)
        if service is not None:
            return service
        app = getattr(executor, "app", None)
        if app is None:
            from ok import og

            # BaseTask.tr() delegates to the UI/headless app. ``og`` is only the
            # global container and does not implement translation.
            app = getattr(og, "app", None) or og
        service = WorldPoseService(executor=executor, app=app)
        service.after_init(executor=executor, scene=getattr(executor, "scene", None))
        setattr(executor, _SERVICE_ATTR, service)
        return service


def shutdown_world_pose_service(executor) -> None:
    service = getattr(executor, _SERVICE_ATTR, None)
    if service is None:
        return
    setattr(executor, _SERVICE_ATTR, None)
    service.on_destroy()


def check_capture_backend_update_from_settings(*_args) -> None:
    service = get_world_pose_service()
    if service is not None:
        service.check_capture_backend_update()


__all__ = [
    "WorldPoseService",
    "check_capture_backend_update_from_settings",
    "get_world_pose_service",
    "shutdown_world_pose_service",
]
