"""Application-level world-position service.

The service owns independent visual-map and packet-position sources but does not depend on a
TriggerTask being registered or enabled. It is created on first use and lives
with the executor until application shutdown.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from src.core.BaseEfTask import BaseEfTask
from src.core.global_config_store import get_global_config
from src.core.NavConfig import NAV_CONFIG_NAME, NAV_MAP_ID_KEY
from src.core.PositionConfig import (
    CONFIG_COMMIT_MIN_SHIFT,
    CONFIG_EXPERIMENTAL_PORT,
    CONFIG_USE_EXPERIMENTAL,
    CONFIG_VISUAL_ANCHOR_CHAIN_ANCHOR_WEIGHT,
    CONFIG_VISUAL_ANCHOR_CHAIN_CORRECTION,
    CONFIG_VISUAL_ANCHOR_CHAIN_EDGE_GAP,
    CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME,
    CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME_EDGES,
    CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME_MIN_RESPONSE,
    CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME_WEIGHT,
    CONFIG_VISUAL_ANCHOR_CHAIN_MAX_CORRECTION,
    CONFIG_VISUAL_ANCHOR_CHAIN_MIN_RESPONSE,
    CONFIG_VISUAL_ANCHOR_CHAIN_WINDOW,
    CONFIG_VISUAL_MAP_ROOT,
    DEFAULT_VISUAL_MAP_ROOT,
    POSITION_CONFIG_NAME,
)
from src.localization.calibrated_map_store import CalibratedMapStore
from src.localization.capture_pose_mixin import CapturePoseMixin
from src.localization.minimap_heading_mixin import MinimapHeadingMixin
from src.localization.visual_position_source import KeyframeConfig, VisualPositionSource
from src.localization.wgc_frame_timing import MAX_FRAME_LEAD_S, timing_for_frame
from src.runtime_state.topics import RuntimeTopic
from src.runtime_state.world_pose import publish_world_pose
from src.tasks.mixin.login_mixin import LoginMixin
from src.tasks.mixin.runtime_state_mixin import RuntimeStateMixin

_SERVICE_ATTR = "_world_pose_service"
_SERVICE_LOCK = threading.Lock()
_UNSET = object()


class WorldPoseService(MinimapHeadingMixin, RuntimeStateMixin, CapturePoseMixin, LoginMixin, BaseEfTask):
    """Continuously maintain fused world position for on-demand consumers."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "World Pose Service"
        self._init_source_state()

    def _init_source_state(self):
        self._init_minimap_heading_mixin()
        self._init_runtime_state_mixin()
        self._init_capture_pose_mixin()
        self._visual_source = None
        self._source_signature = None
        self._minimap_started = False
        self._minimap_last_state = None
        self._visual_error = "visual_map_assets_missing"
        self._visual_retry_at = 0.0
        self._source_epoch = 0
        self._last_anchor_token = None
        self._published_sync_seq = 0
        self._last_status_key = None
        self._last_status_log_at = 0.0
        self._last_position_ui_at = -float("inf")

    def load_config(self):
        """Use the application-level global config instead of a task JSON file."""

        self.config = get_global_config(POSITION_CONFIG_NAME)

    def _now(self, now):
        return self.active_time() if now is None else now

    def _nav_config(self):
        return get_global_config(NAV_CONFIG_NAME)

    @property
    def minimap_position_ready(self):
        # Initialization and trusted coordinates are separate contracts.
        return self._minimap_started

    @property
    def minimap_odometry(self):
        return self._visual_source.odometry if self._visual_source is not None else None

    @property
    def _minimap_scale(self):
        scale = self._visual_source._scale if self._visual_source is not None else None
        return 1 / (4 * scale) if scale else 0.0

    @property
    def minimap_fusion(self):
        return self._visual_source

    def minimap_rest_diag(self):
        return self._visual_source.rest_diag if self._visual_source is not None else None

    def start_minimap_position(self, *, wait_stable=True):
        capture = self.experimental_capture_enabled()
        map_id = str(self._nav_config().get(NAV_MAP_ID_KEY) or "").strip() if not capture else ""
        root = self._cfg_str(CONFIG_VISUAL_MAP_ROOT, DEFAULT_VISUAL_MAP_ROOT) if not capture else ""
        chain_options = {
            "anchor_weight": self._cfg_float(CONFIG_VISUAL_ANCHOR_CHAIN_ANCHOR_WEIGHT, 50),
            "commit_min_shift_px": self._cfg_float(CONFIG_COMMIT_MIN_SHIFT, 2),
            "keyframes": KeyframeConfig(
                interval=self._cfg_int(CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME, 10),
                edges=self._cfg_int(CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME_EDGES, 8),
                min_response=self._cfg_float(CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME_MIN_RESPONSE, 0.3),
                weight=self._cfg_float(CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME_WEIGHT, 25),
            ),
            "chain_enabled": self._cfg_bool(CONFIG_VISUAL_ANCHOR_CHAIN_CORRECTION, True),
            "window_size": self._cfg_int(CONFIG_VISUAL_ANCHOR_CHAIN_WINDOW, 60),
            "edge_gap": self._cfg_int(CONFIG_VISUAL_ANCHOR_CHAIN_EDGE_GAP, 12),
            "min_response": self._cfg_float(CONFIG_VISUAL_ANCHOR_CHAIN_MIN_RESPONSE, 0.25),
            "max_correction_m": self._cfg_float(CONFIG_VISUAL_ANCHOR_CHAIN_MAX_CORRECTION, 1),
        }
        signature = (
            capture,
            str(Path(root).expanduser().resolve()) if root else "",
            map_id,
            tuple(chain_options.values()) if not capture else (self._cfg_int(CONFIG_EXPERIMENTAL_PORT, 8765),),
        )
        if signature == self._source_signature and (
            capture or self._visual_source is not None or self.active_time() < self._visual_retry_at
        ):
            return self.minimap_position_ready
        self.stop_minimap_position()
        self._source_signature = signature
        self._minimap_started = True
        if capture:
            return self.minimap_position_ready
        try:
            map_id, locator = CalibratedMapStore(root).load(map_id, background=True)
            locator.prewarm()
            self._visual_source = VisualPositionSource(
                self,
                locator,
                map_id,
                **chain_options,
            )
            self._visual_error = ""
        except (OSError, ValueError) as exc:
            self._visual_retry_at = self.active_time() + 5.0
            self._visual_error = str(exc)
            self.log_warning(f"Visual localization unavailable: {exc}")
        return self.minimap_position_ready

    def stop_minimap_position(self):
        self._source_epoch += 1
        if self._visual_source is not None:
            self._visual_source.close()
        self._visual_source = None
        self._source_signature = None
        self._minimap_started = False
        self._minimap_last_state = None
        self.runtime_state_hub.clear(RuntimeTopic.WORLD_POSE)

    def latest_minimap_state(self, *, max_age=1, now=None):
        state = self._minimap_last_state
        if state is None or self._now(now) - state["sample_t"] > max_age:
            return None
        return dict(state)

    def sample_world_pose(self, frame=None, *, now=None) -> dict:
        self.start_minimap_position(wait_stable=False)
        if self.experimental_capture_enabled():
            self.ensure_capture_backend()
            state = self._capture_world_pose()
            if state.get("x") is None:
                self.ensure_capture_session(reason="抓包没有坐标")
            self._report_capture_status(state, self._now(now))
            source = "capture"
        else:
            source = "visual_map"
            state = self._sample_visual_pose(frame)
            self._report_position_status(state, now)
        token = (self._source_epoch, source, state.get("sync_seq", 0))
        if token != self._last_anchor_token:
            self._published_sync_seq += 1
            self._last_anchor_token = token
        state["sync_seq"] = self._published_sync_seq
        state["sample_t"] = self._now(now)
        self._minimap_last_state = dict(state)
        publish_world_pose(self.runtime_state_hub, state, source=source, now=self._now(now))
        return state

    def _sample_visual_pose(self, frame):
        if self._visual_source is None:
            return {
                "x": None,
                "y": None,
                "z": None,
                "position_trusted": False,
                "anchor_set": False,
                "trust_reason": self._visual_error,
                "odom_reason": self._visual_error,
                "sync_supported": False,
                "sync_needed": False,
            }
        if frame is None:
            frame = self.next_frame()
        timing = timing_for_frame(getattr(self.executor, "method", None), frame)
        stamp = timing.frame_time if timing is not None else time.time()
        state = self._locate_visual_frame(frame, stamp, timing)
        state["frame_t"] = stamp
        if time.time() - stamp > 2:
            state = self._visual_source.unavailable("visual_result_too_old")
        state["frame_time_source"] = timing.source if timing is not None else "sample_time_fallback"
        state["frame_clock_uncertainty_s"] = timing.clock_uncertainty_s if timing is not None else None
        bearing, score = self._read_arrow(frame) if frame is not None else (None, 0)
        state["heading"], state["heading_score"] = bearing, score
        return state

    def _locate_visual_frame(self, frame, stamp, timing):
        if timing is not None and not -MAX_FRAME_LEAD_S <= time.time() - timing.frame_time <= 2:
            self._visual_source.reset()
            return self._visual_source.unavailable("stale_visual_frame")
        try:
            return self._visual_source.sample(frame, now=stamp)
        except (OSError, ValueError) as exc:
            self._visual_source.reset()
            return self._visual_source.unavailable(f"visual_resource_error:{type(exc).__name__}")

    def _report_position_status(self, state: dict, now=None) -> None:
        x, z = state.get("x"), state.get("z")
        status = self._position_status_text(state, x, z)
        status_key = (
            str(state.get("map_id") or ""),
            bool(state.get("anchor_set")),
            bool(state.get("position_trusted", True)),
            state.get("tier_id"),
        )
        current = self._now(now)
        changed = status_key != self._last_status_key
        text = f"({float(x):.1f}, {float(z):.1f}) {status}" if x is not None and z is not None else status
        if changed or current - self._last_position_ui_at >= 0.5:
            self.info_set("小地图定位", text, log=False)
            self._last_position_ui_at = current
        interval = 30.0 if state.get("position_trusted") else 5.0
        if changed or current - self._last_status_log_at >= interval:
            self.log_info(f"小地图定位：{text}")
            if not state.get("position_trusted") and state.get("visual_confidence") is not None:
                self.log_debug(
                    f"Visual acquisition rejected: reason={state.get('trust_reason')} "
                    f"ncc={state['visual_confidence']:.3f} full={state['visual_full_ncc']:.3f} "
                    f"margin={state['visual_margin']:.3f} search_s={state['visual_match_elapsed_s']:.3f}"
                )
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
