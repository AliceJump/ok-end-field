"""小地图实时位置能力（mixin）：一次调用同时给出「方向」和「坐标」。

坐标 = 小地图位移里程计（相对、高频） + 官方地图 WS 绝对坐标（绝对、有延迟）的融合：

  1. 静止时用 WS 绝对坐标设锚点 ``P0`` 并清零里程计（静止时延迟无影响）；
  2. 移动期间用里程计增量推位置：``P = P0 + M @ 里程计位移``；
  3. 再次静止时用新的 WS 坐标重新校准，把漂移限制在两次校准之间。

「静止」要求小地图里程计低速；若已经收到至少两次 WS 样本，还要求 WS 也没有明显位移。
WS 有传输延迟，只在两边都停住时才能安全地用它做基准。移动中收到的 WS 样本直接忽略、
不做暂存：那是移动途中的旧坐标，静止后应用它只会把里程计已经推进的位置拽回去。

定位状态由应用级 ``WorldPoseService`` 维护。服务每次采样后通过
``sample_world_pose()`` 把稳定契约发布到 ``RuntimeStateHub``；导航等消费方使用
``RuntimeStateMixin.world_pose()``，禁止每个任务各自启动里程计和 WS 位置源。

``minimap_position()`` 返回 dict：

    x / z            融合坐标（米，世界系 x/z）。未设锚点时 None。
    heading          朝向（**罗盘方位角**，度）：正北 0°、正东 90°、正南 180°、正西 270°，
                     顺时针增大。由底层箭头角经
                     :func:`minimap_odometry.arrow_angle_to_bearing` 换算而来
                     （箭头角是屏幕上从正北逆时针，与之互为镜像）。
    heading_score    朝向置信度；低于配置的「朝向最低分数」应判为不可用。
    anchor_set       是否已用 WS 设过锚点（False 时 x/z 为 None）。
    rest             本拍是否静止；静止时才允许用 WS 校准并清零里程计。
    last_sync_t      最近一次校准的时间戳。
    dmap_px          里程计累计位移（地图系像素，x 右 / y 下）。
    world_delta      上述位移映射到世界系的增量（米）。
    ws               最近收到的 WS 坐标 (x, z)，可能滞后；None = 还没收到。
    map_id           最近 WS 位置所属地图；None = 还没收到。
    error            |融合坐标 - 最近 WS|（米）。移动时≈WS 延迟（不是误差），静止校准后≈0。
    odom_ok/odom_reason  本拍是否采到有效位移样本及原因（"ok"/"too_soon"/"no_frame"/
                     "shift_too_small"/"low_response"/"exceed_max_shift"/"too_long_dt"/
                     "speed_anomaly"，
                     未采到时 odom_ok 为 False，odom_reason 说明为什么），
                     用来区分"位置在动"和"位置源已经没有数据了"。
    position_trusted  当前绝对坐标是否可信。重新锚定、换地图或里程计守卫触发后会变为
                     False，必须等到下一次静止 WS 校准才恢复。**例外**：良性换锚
                     （``too_long_dt``，相关可信、只是基线到了上限）不撤销信任——
                     位移提交阈值开启后它会是常态，撤销信任会让导航反复停车等重新校准。
    trust_reason      position_trusted 的最近状态来源。
    just_synced/sync_residual  本拍是否刚触发静止校准，以及校准前的原始融合残差和
                     上一拍视觉锚定链校正输出残差。原始字段为
                     ``{map_x, map_z, ws_x, ws_z, dx, dz, dist}``；校正字段为
                     ``{visual_anchor_chain_x, visual_anchor_chain_z,
                     visual_anchor_chain_dx, visual_anchor_chain_dz,
                     visual_anchor_chain_dist, visual_anchor_chain_reason}``。
    sync_checked/sync_redundant  本拍收到 WS 后是否检查了静止校准，以及检查结果是否为
                     “当前估计已与 WS 对齐，无需重锚”。

``minimap_position()`` 是定位所有者内部的低层采样入口。普通消费者不要直接调用它，
应通过 ``RuntimeStateMixin.world_pose(frame=...)`` 请求采样并读取发布快照；这样当前帧、
朝向和里程计仍由同一个所有者按同一拍处理。

本模块只做编排，几何/融合分别在 ``minimap_odometry`` 与 ``minimap_position_fusion``；
位置源（官方地图 WS 客户端 / 本地 WS 服务）的启停复用 ``WsPositionMixin``。完整链路见
``docs/dev/小地图定位.md``。
"""

from __future__ import annotations

import math
import os
import threading

import numpy as np

from src.core.global_config_store import get_global_config
from src.core.NavConfig import (
    NAV_CONFIG_NAME,
    NAV_CONTENT_KEY,
    NAV_MATRIX_SUFFIX,
    NAV_RESOLUTION_TIERS,
    NAV_SCALE_CONSTANT_KEY,
    NAV_SCALE_SUFFIX,
    NAV_WS_ACCOUNT_KEY,
    NavProfile,
    nav_profile_for_width,
)
from src.core.PositionConfig import (
    CONFIG_COMMIT_MIN_SHIFT,
    CONFIG_EXPERIMENTAL_AUTOSTART,
    CONFIG_EXPERIMENTAL_EXE,
    CONFIG_EXPERIMENTAL_PORT,
    CONFIG_EXPERIMENTAL_RELOGIN,
    CONFIG_EXPERIMENTAL_STOP_ON_EXIT,
    CONFIG_EXPERIMENTAL_UPDATE,
    CONFIG_SYNC_DISTANCE,
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
    CONFIG_WS_MIN_HITS,
    CONFIG_WS_WAIT,
    DEFAULT_COMMIT_MIN_SHIFT_PX,
    DEFAULT_POSITION_CONFIG,
    POSITION_CONFIG_DESCRIPTION,
)
from src.localization.capture_backend_installer import CaptureBackendInstaller
from src.localization.capture_backend_launcher import (
    BackendLauncher,
    probe_backend,
    shared_launcher,
)
from src.localization.capture_position_source import (
    CAPTURE_HEALTH_DECODING,
    CAPTURE_HEALTH_MISSED_HANDSHAKE,
    CAPTURE_HEALTH_NO_CONNECTION,
    CAPTURE_HEALTH_OK,
    CAPTURE_SOURCE_NAME,
    REST_SPEED_THRESHOLD,
    CaptureHealth,
    CapturePositionSource,
)
from src.localization.capture_travel_poles import CaptureTravelPoleLoad, CaptureTravelPoleSource
from src.localization.minimap_heading_mixin import MinimapHeadingMixin
from src.localization.minimap_odometry import MinimapOdometry
from src.localization.minimap_position_fusion import MinimapPositionFusion
from src.localization.minimap_sample_stitch import fused_map_px
from src.localization.minimap_visual_anchor_chain import (
    MinimapKeyframeVisualAnchorChain,
    MinimapVisualAnchorChainCorrector,
)
from src.localization.ws_position_mixin import WsPositionMixin
from src.runtime_state.topics import RuntimeTopic
from src.runtime_state.world_pose import publish_world_pose
from src.tasks.account.account_scope_store import (
    get_account_map_content,
    resolve_account_id,
)
from src.tasks.mixin.runtime_state_mixin import RuntimeStateMixin

__all__ = [
    "CONFIG_COMMIT_MIN_SHIFT",
    "CONFIG_EXPERIMENTAL_AUTOSTART",
    "CONFIG_EXPERIMENTAL_EXE",
    "CONFIG_EXPERIMENTAL_PORT",
    "CONFIG_EXPERIMENTAL_RELOGIN",
    "CONFIG_EXPERIMENTAL_STOP_ON_EXIT",
    "CONFIG_EXPERIMENTAL_UPDATE",
    "CONFIG_SYNC_DISTANCE",
    "CONFIG_USE_EXPERIMENTAL",
    "CONFIG_VISUAL_ANCHOR_CHAIN_ANCHOR_WEIGHT",
    "CONFIG_VISUAL_ANCHOR_CHAIN_CORRECTION",
    "CONFIG_VISUAL_ANCHOR_CHAIN_EDGE_GAP",
    "CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME",
    "CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME_EDGES",
    "CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME_MIN_RESPONSE",
    "CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME_WEIGHT",
    "CONFIG_VISUAL_ANCHOR_CHAIN_MAX_CORRECTION",
    "CONFIG_VISUAL_ANCHOR_CHAIN_MIN_RESPONSE",
    "CONFIG_VISUAL_ANCHOR_CHAIN_WINDOW",
    "CONFIG_WS_MIN_HITS",
    "CONFIG_WS_WAIT",
    "DEFAULT_COMMIT_MIN_SHIFT_PX",
    "MinimapPositionMixin",
    "parse_map_to_world",
]

#: 抓包错过握手时最多重建几次连接（每次都是一次"离开游戏再进入"）。用尽即停手——
#: 无人值守时反复登出比不修更糟。
CAPTURE_RELOGIN_ATTEMPTS = 3
#: 重建连接后等后端接管新连接的秒数。游戏重新登录要读条，中间会经过"没有连接"的阶段。
CAPTURE_RELOGIN_WAIT_SECONDS = 90.0
#: 两次重建之间的最小间隔。本方法每拍都被调，没有它就等于"连续猛试"，而每次都要把玩家
#: 踢回登录界面——实测两次只隔了 14 秒，太激进。
CAPTURE_RELOGIN_INTERVAL_SECONDS = 20.0


def parse_map_to_world(value):
    """把 "a11,a12,a21,a22" 解析为 2x2 矩阵；非法或含非有限值时返回 None。

    非有限值（nan/inf）必须在这里挡掉：否则融合坐标会变成 nan（而不是 None），
    下游的 ``x is None`` 守卫失效，规划阶段才在 ``index_of_world`` 深处炸栈。
    """
    parts = str(value or "").split(",")
    if len(parts) != 4:
        return None
    try:
        nums = [float(p) for p in parts]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(n) for n in nums):
        return None
    return [[nums[0], nums[1]], [nums[2], nums[3]]]


class MinimapPositionMixin(MinimapHeadingMixin, RuntimeStateMixin, WsPositionMixin):
    """小地图实时位置：同时给出方向（朝向角）和坐标（小地图推算 + WS 校准）。

    朝向相关的能力（读朝向、转到指定方位）来自 :class:`MinimapHeadingMixin`。

    **比例尺、轴映射与 WS 真值来源都在全局「导航配置」里**（``src/core/NavConfig.py``），
    任务侧不再配置：比例尺随画面宽变化（``比例尺 = C / 画面宽``），配在任务里必然会在
    换分辨率时过期——历史上 2560 宽量出的 0.6703 被拿到 1920 下用，距离就系统性偏了 27%。
    """

    # ------------------------------------------------------------------ #
    # 配置（任务把这两个 dict merge 进自己的 default_config / config_description）
    # ------------------------------------------------------------------ #
    @staticmethod
    def minimap_position_default_config() -> dict:
        """返回小地图定位配置的默认值（不含比例尺/轴映射/真值——那些在全局导航配置）。"""
        return dict(DEFAULT_POSITION_CONFIG)

    @staticmethod
    def minimap_position_config_description() -> dict:
        """返回定位配置键的用户说明，包含静止校准语义。"""
        return dict(POSITION_CONFIG_DESCRIPTION)

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def _init_minimap_position_mixin(self):
        """在任务 __init__ 里调用（只建状态，不起线程、不抓帧）。"""
        self._init_minimap_heading_mixin()
        self._init_runtime_state_mixin()
        self._init_ws_position_mixin()
        self._minimap_od: MinimapOdometry | None = None
        self._minimap_fusion: MinimapPositionFusion | None = None
        self._minimap_visual_anchor_chain: MinimapVisualAnchorChainCorrector | None = None
        self._minimap_keyframe_visual_anchor_chain: MinimapKeyframeVisualAnchorChain | None = None
        self._minimap_keyframe_counter = 0
        self._minimap_keyframe_uid = 0
        self._minimap_ws_map_id: str | None = None
        self._minimap_last_ws: tuple[float, float] | None = None
        self._minimap_last_ws_xyz: tuple[float, float, float] | None = None
        self._minimap_profile_signature = None
        self._minimap_source_cred = ""
        self._minimap_scale = 0.0
        self._minimap_started = False
        self._minimap_last_state: dict | None = None
        self._minimap_sync_seq = 0
        # 距离上次真实校准的累计位移，用于触发周期性停车校准。
        self._minimap_distance_since_sync = 0.0
        self._minimap_prev_position: tuple[float, float] | None = None
        self._minimap_prev_map_id: str | None = None
        # 绝对坐标信任位：重锚/换图/里程计守卫触发后清零，下一次静校准恢复。
        self._minimap_position_trusted = False
        self._minimap_trust_reason = "uninitialized"
        # 实验技术（抓包直通）状态：数据源实例与已消费的样本序号。
        # 直通模式下 sync_seq 每来一条新样本就 +1，导航据此失效"已走过的已知格"。
        self._minimap_capture_source: CapturePositionSource | None = None
        self._minimap_capture_travel_pole_source: CaptureTravelPoleSource | None = None
        self._minimap_capture_launcher: BackendLauncher | None = None
        self._minimap_capture_installer: CaptureBackendInstaller | None = None
        #: 预下载线程句柄；非 None 表示已经起过一次，不再重复起
        self._minimap_capture_prefetch: threading.Thread | None = None
        #: 已尝试重建连接的次数；抓包恢复正常即清零
        self._minimap_capture_relogin_attempts = 0
        #: 重建相关的告警只报一次，避免每拍刷屏
        self._minimap_capture_relogin_warned = False
        #: 下一次允许重建的时间点（active_time 基准）
        self._minimap_capture_relogin_next_at = 0.0
        self._minimap_capture_seq = 0
        #: 上一条坐标所属的连接号；只有它变化才推进 ``_minimap_capture_seq``
        self._minimap_capture_last_flow: int | None = None

    def _nav_config(self):
        """读全局「导航配置」。取不到时返回 None（由调用方决定怎么报）。"""
        try:
            return get_global_config(NAV_CONFIG_NAME)
        except Exception as e:  # 该对象是框架 Config，异常类型不可控
            self.log_warning(f"读取全局「{NAV_CONFIG_NAME}」失败: {e}")
            return None

    def _nav_profile(self) -> NavProfile | None:
        """按**当前画面宽度**从全局「导航配置」选出比例尺与轴映射。

        比例尺随画面宽变化是物理事实（``比例尺 = C / 画面宽``），所以这里永远按当前分辨率
        现算，任务侧不再有"比例尺"这个配置项——配在任务里必然会在换分辨率时过期。
        """
        config = self._nav_config()
        if config is None:
            return None
        values = {
            NAV_SCALE_CONSTANT_KEY: config.get(NAV_SCALE_CONSTANT_KEY),
            **{
                f"{name}{suffix}": config.get(f"{name}{suffix}")
                for name, _ in NAV_RESOLUTION_TIERS
                for suffix in (NAV_SCALE_SUFFIX, NAV_MATRIX_SUFFIX)
            },
        }
        return nav_profile_for_width(int(getattr(self, "width", 0) or 0), values)

    def _build_visual_anchor_chain(self, scale: float):
        """创建局部求解器与全局关键帧视觉锚定链校正；关闭时返回 (None, None)。"""

        if not self._cfg_bool(CONFIG_VISUAL_ANCHOR_CHAIN_CORRECTION, True):
            return None, None
        corrector = MinimapVisualAnchorChainCorrector(
            window_size=self._cfg_int(CONFIG_VISUAL_ANCHOR_CHAIN_WINDOW, 60),
            edge_gap=self._cfg_int(CONFIG_VISUAL_ANCHOR_CHAIN_EDGE_GAP, 12),
            min_response=self._cfg_float(CONFIG_VISUAL_ANCHOR_CHAIN_MIN_RESPONSE, 0.25),
            anchor_weight=self._cfg_float(CONFIG_VISUAL_ANCHOR_CHAIN_ANCHOR_WEIGHT, 50.0),
            scale_m_per_px=(scale if scale > 0 else None),
            max_correction_m=self._cfg_float(CONFIG_VISUAL_ANCHOR_CHAIN_MAX_CORRECTION, 5.0),
        )
        keyframe_chain = MinimapKeyframeVisualAnchorChain(
            max_edges=self._cfg_int(CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME_EDGES, 8),
            min_response=self._cfg_float(CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME_MIN_RESPONSE, 0.3),
            anchor_weight=self._cfg_float(CONFIG_VISUAL_ANCHOR_CHAIN_ANCHOR_WEIGHT, 50.0),
            scale_m_per_px=(scale if scale > 0 else None),
        )
        return corrector, keyframe_chain

    def start_minimap_position(self, *, wait_stable: bool = True) -> bool:
        """建里程计 + 融合、启动位置源、等 WS 稳定并立即设锚点。

        Returns:
            True = 已用稳定的 WS 真值锚定（x/z 立即可用）；
            False = 没有可用的官方 WS 真值（未配置 content / 认证失败 / 等稳定超时），
            此时若本地 WS 服务能提供位置，会在后续静止时自动完成首次锚定。
        """
        if self.experimental_capture_enabled():
            # 抓包直通模式：坐标不来自小地图，**不需要**比例尺、里程计、融合与地图 WS。
            # 必须在这里提前返回：否则下面那道 `_nav_profile()` 会因为拿不到分辨率/配置无效而
            # 返回 False，让调用方以为"定位器没初始化好"——而抓包根本不用比例尺，于是导航会在
            # 真正该看后端的地方报一条指向全局导航配置的错。即使比例尺算得出来，也会白建两个
            # 对象并启动地图 WS，正是本模式声明要绕开的那一整套。
            return True
        profile = self._nav_profile()
        if profile is None:
            self.log_warning(
                "无法确定导航比例尺：拿不到画面分辨率（游戏窗口还没就绪），或全局"
                f"「{NAV_CONFIG_NAME}」里的档位与比例尺常数都是无效值。"
                "请确认游戏窗口已连接，并检查全局配置里的导航配置。",
                notify=True,
            )
            return False
        matrix = parse_map_to_world(profile.map_to_world)
        if matrix is None:
            self.log_warning(
                f"全局「{NAV_CONFIG_NAME}」的轴映射无法解析（应为 4 个逗号分隔数字）: {profile.map_to_world!r}",
                notify=True,
            )
            return False
        scale = profile.scale
        signature = (
            int(getattr(self, "width", 0) or 0),
            int(getattr(self, "height", 0) or 0),
            float(scale),
            tuple(float(v) for v in matrix[0]),
            tuple(float(v) for v in matrix[1]),
        )

        try:
            cred = self._resolve_ws_cred()
        except Exception as e:
            self.log_warning(f"读取地图 WS content 失败，改用本地 WS 位置源: {e}")
            cred = ""

        if self._minimap_started and self._minimap_fusion is not None:
            if signature != self._minimap_profile_signature:
                self.log_info("导航比例尺或轴映射已变化，重建小地图定位器")
                self.stop_minimap_position()
                self._minimap_od = None
                self._minimap_fusion = None
                self._minimap_last_state = None
            else:
                if cred != self._minimap_source_cred:
                    self.log_info("地图 WS 真值来源已变化，清空绝对锚点等待重新校准")
                    self._minimap_fusion.reset()
                    if self._minimap_visual_anchor_chain is not None:
                        self._minimap_visual_anchor_chain.reset()
                    if self._minimap_keyframe_visual_anchor_chain is not None:
                        self._minimap_keyframe_visual_anchor_chain.reset()
                    self._minimap_keyframe_counter = 0
                    self._minimap_keyframe_uid = 0
                    self._minimap_ws_map_id = None
                    self._minimap_last_ws = None
                    self._minimap_last_ws_xyz = None
                    self._minimap_position_trusted = False
                    self._minimap_trust_reason = "source_changed"
                    self._minimap_distance_since_sync = 0.0
                    self._minimap_prev_position = None
                    self._minimap_prev_map_id = None
                    self._minimap_last_state = None
                    self._minimap_source_cred = cred
                self._ensure_ws_position_source(cred)
                estimate = self._minimap_fusion.estimate()
                return estimate is not None

        self.log_info(f"导航比例尺 {scale:.6f} 米/像素（{profile.source}），轴映射 {profile.map_to_world}")

        self._minimap_scale = scale
        commit_min_shift = max(
            0.0,
            self._cfg_float(CONFIG_COMMIT_MIN_SHIFT, DEFAULT_COMMIT_MIN_SHIFT_PX),
        )
        self._minimap_od = MinimapOdometry(
            self,
            scale_m_per_px=(scale if scale > 0 else None),
            commit_min_shift_px=commit_min_shift,
        )
        self._minimap_fusion = MinimapPositionFusion(
            self._minimap_od,
            map_to_world_px=matrix,
            scale_m_per_px=scale,
            arrow_func=self._read_arrow,
        )
        (
            self._minimap_visual_anchor_chain,
            self._minimap_keyframe_visual_anchor_chain,
        ) = self._build_visual_anchor_chain(scale)
        self._minimap_keyframe_counter = 0
        self._minimap_keyframe_uid = 0
        self._minimap_ws_map_id = None
        self._minimap_last_ws = None
        self._minimap_last_ws_xyz = None
        self._minimap_distance_since_sync = 0.0
        self._minimap_prev_position = None
        self._minimap_prev_map_id = None
        self._minimap_position_trusted = False
        self._minimap_trust_reason = "uninitialized"
        self._minimap_profile_signature = signature
        self._minimap_source_cred = cred

        self._ensure_ws_position_source(cred)
        self._minimap_started = True
        if not cred:
            self.log_info(
                "未配置地图 WS content（content/地图账号为空）：没有绝对基准，"
                "位置需等本地 WS 提供坐标并在静止时自动锚定"
            )
            return False
        if not self._is_map_ws_client_enabled():
            self.log_warning("地图 WS 客户端未启动（认证失败或缺少设备ID），稍后由触发周期重试")
            return False

        self.log_info("地图 WS 客户端已启动，等待位置流稳定……", notify=True)
        if not wait_stable:
            return False
        wait = self._cfg_float(CONFIG_WS_WAIT, 10.0)
        min_hits = max(2, self._cfg_int(CONFIG_WS_MIN_HITS, 3))
        stable, map_id, last_pos = self._wait_ws_stable(timeout=wait, min_hits=min_hits)
        if not stable:
            self.log_warning(
                f"WS 位置流未在 {wait:.1f}s 内稳定（mapId={map_id}）：先只用里程计与朝向，等 WS 稳定且静止时再自动锚定",
                notify=True,
            )
            return False
        self._minimap_ws_map_id = map_id
        if last_pos is not None and last_pos[3] == map_id:
            # 用最后一条稳定位置立即设锚点，避免起步前几拍没有坐标
            self._minimap_fusion.sync(last_pos, map_id=map_id, now=self.active_time())
            self._minimap_last_ws = (last_pos[0], last_pos[2])
            self._minimap_last_ws_xyz = (last_pos[0], last_pos[1], last_pos[2])
            self._minimap_position_trusted = True
            self._minimap_trust_reason = "initial_sync"
            self.log_info(
                f"WS 已稳定并设置锚点: mapId={map_id} pos=({last_pos[0]:.2f},{last_pos[2]:.2f})",
                notify=True,
            )
        return True

    def stop_minimap_position(self):
        """停掉全部位置源（任务收尾/窗口丢失时调用）。锚点状态保留，可接着用。"""
        try:
            self._stop_position_sources()
        except Exception as e:
            self.log_warning(f"停止位置源失败: {e}")
        finally:
            if self._minimap_visual_anchor_chain is not None:
                self._minimap_visual_anchor_chain.reset()
            if self._minimap_keyframe_visual_anchor_chain is not None:
                self._minimap_keyframe_visual_anchor_chain.reset()
            self._minimap_keyframe_counter = 0
            self._minimap_keyframe_uid = 0
            self._minimap_started = False
            self.runtime_state_hub.clear(RuntimeTopic.WORLD_POSE)

    # ------------------------------------------------------------------ #
    # 位置源（官方地图 WS 客户端 / 本地 WS 服务）
    # ------------------------------------------------------------------ #
    def _resolve_ws_cred(self) -> str:
        """按优先级解析地图同步 content（全局导航配置的 content > 地图账号 > 当前账号上下文）。"""
        config = self._nav_config()
        content = account = ""
        if config is not None:
            content = str(config.get(NAV_CONTENT_KEY) or "").strip()
            account = str(config.get(NAV_WS_ACCOUNT_KEY) or "").strip()
        if content:
            return content
        if account:
            account_id = resolve_account_id(account, create_if_missing=False) or account
            return get_account_map_content(account_id, account_name=account)
        account_id = str(getattr(self, "current_account_id", "") or "").strip()
        account_name = str(getattr(self, "current_user", "") or "").strip()

        executor = getattr(self, "executor", None)
        current_task = getattr(executor, "current_task", None) if executor is not None else None
        if current_task is not None and current_task is not self:
            account_id = account_id or str(getattr(current_task, "current_account_id", "") or "").strip()
            account_name = account_name or str(getattr(current_task, "current_user", "") or "").strip()

        return get_account_map_content(account_id or account_name, account_name=account_name)

    def _poll_ws_position(self, timeout: float = 0.0):
        """取一条**新到**的 WS 位置（不从缓存取旧值）。

        Returns:
            (x, y, z, map_id) | None：仅当收到有效新位置时返回。
            用 ``_recv_ws_position_payload``（仅新消息）而非 ``_recv_ws_position_payload_or_cached``，
            避免把同一条旧位置重复当成新样本。
        """
        try:
            payload = self._recv_ws_position_payload(timeout=timeout)
        except Exception:
            return None
        if payload is None:
            return None
        try:
            pos, map_id, x, y, z = self._extract_position_payload(payload)
        except Exception:
            return None
        if pos is None or map_id is None:
            return None
        return (x, y, z, map_id)

    def _wait_ws_stable(self, timeout: float = 10.0, min_hits: int = 3):
        """等到 WS 位置流稳定，返回 ``(是否稳定, 最近的 mapId, 最后一条位置)``。

        连续收到有效位置、且 mapId 一致，累计达 ``min_hits`` 才算稳定。稳定前 WS 可能
        还没完成认证/账号解析，此时当作基准的坐标是空或陈旧的，会导致锚点/标定不准。
        最后一条位置用于稳定后立即设锚点，避免起步前几拍没有坐标。
        """
        map_id = None
        hits = 0
        last_pos = None
        t0 = self.active_time()
        while self.active_time() - t0 < timeout:
            pos_ws = self._poll_ws_position(timeout=0.5)
            if pos_ws is None:
                continue
            mid = pos_ws[3]
            last_pos = pos_ws
            if map_id is None:
                map_id = mid
                hits = 1
            elif mid == map_id:
                hits += 1
            else:
                # 地图已切换，重新累计
                map_id = mid
                hits = 1
            if hits >= min_hits:
                return True, map_id, last_pos
        return False, map_id, last_pos

    # ------------------------------------------------------------------ #
    # 朝向 / 位置
    # ------------------------------------------------------------------ #
    def minimap_position(
        self,
        frame=None,
        *,
        now=None,
        feed_ws: bool = True,
        allow_sync: bool = True,
    ) -> dict:
        """采样一拍，返回本拍的「方向 + 坐标」。

        Args:
            frame: 当前帧。**强烈建议传入**——不传会自己 ``next_frame()`` 抓一帧，
                导致"算位移用的帧"和"读朝向用的帧"不是同一时刻。
            now: 可选时间戳（秒），默认用 ``active_time()``。
            feed_ws: 是否顺带消费一条新到的 WS 位置并做静止校准。默认 True；
                调用方要自己读原始 WS 样本时传 False（避免样本被这里吃掉）。
            allow_sync: 为 False 时仍消费并记录 WS 样本、更新静止判定，但不重锚。

        Returns:
            见模块 docstring 的字段说明。``x``/``z`` 为 None 表示还没锚定。
        """
        if self._minimap_fusion is None or self._minimap_od is None:
            raise RuntimeError("请先调用 start_minimap_position() 初始化小地图位置源")

        st = self._minimap_fusion.state(now=now, frame=frame)
        st["just_synced"] = False
        st["sync_residual"] = None
        st["sync_checked"] = False
        st["sync_redundant"] = False
        if feed_ws:
            self._feed_ws_position(st, self._now(now), allow_sync=allow_sync)
            st["rest"] = self._minimap_fusion.is_rest()
        st["map_id"] = self._minimap_ws_map_id

        # 本拍是否有**有效位移样本**（区分"位置在动"与"位置源已经没有数据了"）。
        # 注意必须同时看 sampled：锚帧初始化（anchor_init）与采样过密（too_soon）
        # 都会 ok=True 但 sampled=False，此时并没有新的位移数据。
        last = self._minimap_od.last_result() or {}
        st["odom_ok"] = bool(last.get("ok") and last.get("sampled"))
        st["odom_reason"] = last.get("reason")
        # 守卫换锚才撤销信任；"良性换锚"（too_long_dt：相关可信、只是基线到了上限）不算。
        # 位移提交阈值开启后锚帧会被扣住等位移，dt 涨到 sample_max_dt 是**常态**——
        # 若把它也当成"测坏了"，导航会停车等重新校准，一次停 6 秒，比漂移更影响体感。
        if last.get("reanchored") and not last.get("benign_reanchor"):
            self._minimap_position_trusted = False
            self._minimap_trust_reason = str(last.get("reason") or "reanchored")

        st["ws"] = self._minimap_last_ws
        st["ws_xyz"] = self._minimap_last_ws_xyz
        st["y"] = self._minimap_last_ws_xyz[1] if self._minimap_last_ws_xyz is not None else None
        self._apply_visual_anchor_chain(st, now)
        st["error"] = None
        if st.get("x") is not None and st.get("z") is not None and self._minimap_last_ws is not None:
            st["error"] = math.hypot(st["x"] - self._minimap_last_ws[0], st["z"] - self._minimap_last_ws[1])
        st["sync_seq"] = self._minimap_sync_seq
        st["sample_t"] = self._now(now)
        st["position_trusted"] = bool(self._minimap_position_trusted and st.get("anchor_set"))
        st["trust_reason"] = self._minimap_trust_reason
        self._update_sync_request(st)
        self._minimap_last_state = dict(st)
        return st

    def _apply_visual_anchor_chain(self, st: dict, now) -> None:
        """用关键帧视觉锚定链校正本拍位置；只改内存状态，不落盘、不绘图。"""

        corrector = self._minimap_visual_anchor_chain
        if corrector is None or self._minimap_od is None or self._minimap_fusion is None:
            return
        x, z = st.get("x"), st.get("z")
        if x is None or z is None:
            corrector.reset()
            return
        st["raw_x"] = float(x)
        st["raw_z"] = float(z)
        last = self._minimap_od.last_result() or {}
        if not last.get("sampled") and not st.get("just_synced"):
            previous = self._minimap_last_state
            if previous is not None and previous.get("x") is not None:
                st["x"] = previous.get("x")
                st["z"] = previous.get("z")
                for key in (
                    "visual_anchor_chain_x",
                    "visual_anchor_chain_z",
                    "visual_anchor_chain_correction_px",
                    "visual_anchor_chain_correction_m",
                    "visual_anchor_chain_nodes",
                    "visual_anchor_chain_edges",
                    "visual_anchor_chain_anchors",
                    "visual_anchor_chain_iterations",
                    "visual_anchor_chain_min_response",
                    "visual_anchor_chain_median_response",
                    "visual_anchor_chain_residual_median",
                    "visual_anchor_chain_residual_max",
                ):
                    if key in previous:
                        st[key] = previous[key]
                st["visual_anchor_chain_reason"] = previous.get(
                    "visual_anchor_chain_reason",
                    "no_new_sample",
                )
            else:
                st["visual_anchor_chain_reason"] = "no_new_sample"
            return
        gray, alpha = self._minimap_od.last_ring_patch()
        if gray is None or alpha is None:
            return
        matrix = self._minimap_fusion.map_to_world_px
        raw_px = fused_map_px(matrix, x, z)
        if raw_px is None:
            return
        anchor = None
        if st.get("just_synced") and st.get("ws") is not None:
            ws = st["ws"]
            anchor = fused_map_px(matrix, ws[0], ws[1])
        keyframe_anchor = None
        keyframe_weight = 0.0
        keyframe_result = None
        if self._minimap_keyframe_visual_anchor_chain is not None:
            interval = max(1, self._cfg_int(CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME, 10))
            self._minimap_keyframe_counter += 1
            is_keyframe = bool(st.get("just_synced")) or self._minimap_keyframe_counter >= interval
            if is_keyframe:
                self._minimap_keyframe_counter = 0
                keyframe_result = self._minimap_keyframe_visual_anchor_chain.add(
                    uid=self._minimap_keyframe_uid,
                    time=self._now(now),
                    gray=gray,
                    alpha=alpha,
                    initial_pos=raw_px,
                    anchor=anchor,
                )
                self._minimap_keyframe_uid += 1
                if keyframe_result.valid:
                    keyframe_anchor = keyframe_result.position
                    keyframe_weight = self._cfg_float(CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME_WEIGHT, 25.0)
        result = corrector.add(
            time=self._now(now),
            gray=gray,
            alpha=alpha,
            initial_pos=raw_px,
            anchor=anchor,
            keyframe_anchor=keyframe_anchor,
            keyframe_anchor_weight=keyframe_weight,
        )
        if keyframe_result is not None:
            st["visual_anchor_chain_keyframe_reason"] = keyframe_result.reason
            st["visual_anchor_chain_keyframe_correction_m"] = float(keyframe_result.correction_m)
            st["visual_anchor_chain_keyframe_nodes"] = int(keyframe_result.nodes)
            st["visual_anchor_chain_keyframe_edges"] = int(keyframe_result.edges)
            st["visual_anchor_chain_keyframe_anchors"] = int(keyframe_result.anchors)
            st["visual_anchor_chain_keyframe_iterations"] = int(keyframe_result.iterations)
            st["visual_anchor_chain_keyframe_min_response"] = float(keyframe_result.min_response)
            st["visual_anchor_chain_keyframe_median_response"] = float(keyframe_result.median_response)
            st["visual_anchor_chain_keyframe_residual_median"] = float(keyframe_result.residual_median)
            st["visual_anchor_chain_keyframe_residual_max"] = float(keyframe_result.residual_max)
        else:
            previous = self._minimap_last_state
            for key in (
                "visual_anchor_chain_keyframe_reason",
                "visual_anchor_chain_keyframe_correction_m",
                "visual_anchor_chain_keyframe_nodes",
                "visual_anchor_chain_keyframe_edges",
                "visual_anchor_chain_keyframe_anchors",
                "visual_anchor_chain_keyframe_iterations",
                "visual_anchor_chain_keyframe_min_response",
                "visual_anchor_chain_keyframe_median_response",
                "visual_anchor_chain_keyframe_residual_median",
                "visual_anchor_chain_keyframe_residual_max",
            ):
                if previous is not None and key in previous:
                    st[key] = previous[key]
        st["visual_anchor_chain_correction_px"] = (
            float(result.correction_px[0]),
            float(result.correction_px[1]),
        )
        st["visual_anchor_chain_correction_m"] = float(result.correction_m)
        st["visual_anchor_chain_nodes"] = int(result.nodes)
        st["visual_anchor_chain_edges"] = int(result.edges)
        st["visual_anchor_chain_anchors"] = int(result.anchors)
        st["visual_anchor_chain_iterations"] = int(result.iterations)
        st["visual_anchor_chain_min_response"] = float(result.min_response)
        st["visual_anchor_chain_median_response"] = float(result.median_response)
        st["visual_anchor_chain_residual_median"] = float(result.residual_median)
        st["visual_anchor_chain_residual_max"] = float(result.residual_max)
        st["visual_anchor_chain_reason"] = result.reason
        if not result.valid:
            return
        world = np.asarray(matrix, dtype=np.float64) @ result.position
        if not np.all(np.isfinite(world)):
            st["visual_anchor_chain_reason"] = "bad_position"
            return
        st["visual_anchor_chain_x"] = float(world[0])
        st["visual_anchor_chain_z"] = float(world[1])
        st["x"] = float(world[0])
        st["z"] = float(world[1])

    def sample_world_pose(self, frame=None, *, now=None) -> dict:
        """采样并发布一帧世界坐标，供状态总线消费者使用。"""

        if self.experimental_capture_enabled():
            state = self._capture_world_pose(now=now)
        else:
            state = self.minimap_position(frame=frame, now=now)
        publish_world_pose(
            self.runtime_state_hub,
            state,
            source=self._minimap_position_source_name(),
            now=self._now(now),
        )
        return state

    # ------------------------------------------------------------------ #
    # 实验技术：抓包直通
    # ------------------------------------------------------------------ #
    def experimental_capture_enabled(self) -> bool:
        """是否启用外部抓包直通定位。"""

        return self._cfg_bool(CONFIG_USE_EXPERIMENTAL, False)

    def capture_position_source(self) -> CapturePositionSource:
        """按当前配置端口返回抓包数据源（端口变化时重建）。"""

        port = int(self._cfg_int(CONFIG_EXPERIMENTAL_PORT, 8765))
        source = self._minimap_capture_source
        if source is None or source.port != port:
            source = CapturePositionSource(
                port,
                log_info=self.log_info,
                log_warning=self.log_warning,
            )
            self._minimap_capture_source = source
        return source

    def capture_travel_pole_source(self) -> CaptureTravelPoleSource:
        """按当前配置端口返回按需拉取的滑索快照数据源。"""

        port = int(self._cfg_int(CONFIG_EXPERIMENTAL_PORT, 8765))
        source = self._minimap_capture_travel_pole_source
        if source is None or source.port != port:
            source = CaptureTravelPoleSource(port, log_warning=self.log_warning)
            self._minimap_capture_travel_pole_source = source
        return source

    def capture_travel_pole_graph(self, *, map_id: str, include_shared: bool) -> CaptureTravelPoleLoad:
        """读取一次抓包滑索快照；调用方负责仅在实验抓包模式下调用。"""

        return self.capture_travel_pole_source().load(map_id=map_id, include_shared=include_shared)

    def capture_backend_launcher(self) -> BackendLauncher:
        """按当前配置返回后端拉起器（与游戏启动前的自动拉起共用同一个实例）。"""

        port = int(self._cfg_int(CONFIG_EXPERIMENTAL_PORT, 8765))
        executable = self._cfg_str(CONFIG_EXPERIMENTAL_EXE, "")
        launcher = shared_launcher(
            port,
            executable=executable,
            log_info=self.log_info,
            log_warning=self.log_warning,
        )
        self._minimap_capture_launcher = launcher
        return launcher

    def capture_backend_installer(self) -> CaptureBackendInstaller:
        """按当前配置返回托管安装器（进程内复用同一个实例）。"""

        installer = self._minimap_capture_installer
        if installer is None:
            installer = CaptureBackendInstaller(
                log_info=self.log_info,
                log_warning=self.log_warning,
            )
            self._minimap_capture_installer = installer
        return installer

    def ensure_capture_backend_installed(self) -> bool:
        """确保托管目录里有一份可用的后端 exe；返回它是否可用。

        只有「使用实验技术」打开后才会走到这里，所以关着的时候一次网络请求都不会发。检查受
        :data:`~src.localization.capture_backend_installer.CHECK_TTL_SECONDS` 节流——本方法会被
        定位任务**每一拍**调用，没有节流会把未认证的 GitHub 配额打光。

        指定了「实验技术后端程序路径」（或环境变量）时完全跳过：用户自带后端就不该再联网。
        """

        if self._cfg_str(CONFIG_EXPERIMENTAL_EXE, "").strip() or os.environ.get("EF_CAPTURE_BACKEND"):
            return True
        path = self.capture_backend_installer().ensure_installed(allow_network=True)
        return path is not None and path.is_file()

    def prefetch_capture_backend(self) -> None:
        """后台预下载后端，不阻塞界面。

        时机是用户**刚勾上**风险开关的那一刻：那时他还没启动游戏，趁这段时间把 18 MB 拿下来，
        等点「启动游戏」时就不必干等。整个过程失败都不影响什么——启动前那次兜底会再试一次。
        """

        installer = self.capture_backend_installer()
        if installer.ensure_installed(allow_network=False) is not None:
            return

        def _run() -> None:
            try:
                installer.ensure_installed(allow_network=True)
            except Exception as exc:  # 后台线程里的异常没人接，必须自己吞掉
                self.log_warning(f"实验技术：后端预下载失败（{type(exc).__name__}）")

        if getattr(self, "_minimap_capture_prefetch", None) is not None:
            return
        self._minimap_capture_prefetch = threading.Thread(target=_run, name="CaptureBackendPrefetch", daemon=True)
        self._minimap_capture_prefetch.start()

    def ensure_capture_backend(self) -> bool:
        """确保抓包后端在跑；返回它当前是否可用。

        关掉自动启动时只探活、不拉起——留给"我自己管后端进程"的用法。

        **必须在游戏建立主连接之前调用**：后端要抓到登录握手才推得出正确的解密流。所以
        定位任务在启用直通的第一拍就会走到这里，而不是等到第一次要坐标时。
        """

        launcher = self.capture_backend_launcher()
        if not self._cfg_bool(CONFIG_EXPERIMENTAL_AUTOSTART, True):
            return probe_backend(launcher.port)
        # 先保证 exe 在本地（缺了就下载/更新），再拉起。这一步是**阻塞**的：宁可让启动多等
        # 十几秒，也不能让游戏先连上——那样这一局就抓不到登录握手了。
        if not self.ensure_capture_backend_installed():
            return False
        return launcher.ensure_running()

    def capture_session_health(self) -> CaptureHealth | None:
        """读一次抓包会话的健康状态；开关关着时返回 ``None``。"""

        if not self.experimental_capture_enabled():
            return None
        return self.capture_position_source().health()

    def force_new_capture_connection(self) -> bool:
        """走**完整的重进流程**（打开登出入口 → 确认 → 点一下继续），逼一条新的 TCP 连接。

        只对"抓包错过了本次握手"这种情形有意义：后端没看到 SYN 就推不出密钥流，而它本身没坏
        ——换一条连接让它从头抓就行。

        复用 :meth:`LoginMixin.relogin_same_account`：按用户口径**不重新选择账号**，确认登出后
        在登录界面（出现 ``fL.logout``、"点击任意位置继续"）点一下屏幕中央继续进入。用鸭子类型
        取方法而不是 import，定位层不反向依赖任务层；拿不到可用的重进实现就干净失败——宁可不做，
        也不要乱点。
        """

        relogin = getattr(self, "relogin_same_account", None)
        if not callable(relogin):
            self.log_warning(
                "实验技术：当前任务没有可用的重进实现，无法自动重建连接——请手动退回登录界面再进入一次",
                notify=True,
            )
            return False
        try:
            return bool(relogin())
        except Exception as exc:
            self.log_warning(f"实验技术：自动重新登录失败（{type(exc).__name__}: {exc}）", notify=True)
            return False

    def wait_for_capture_connection(self, *, timeout: float, tick: float = 2.0) -> CaptureHealth:
        """等新连接被接管：一直轮询到"握手成功"或"又错过握手"。

        游戏重新登录要读条，这期间后端会先看到「没有连接」、再到「等待登录初始化」、最后才
        「正在解码」，所以不能只查一次。返回值是最后一次读到的健康状态。
        """

        deadline = self.active_time() + max(1.0, float(timeout))
        health = self.capture_position_source().health()
        while self.active_time() < deadline:
            # 读到"正在解码"就够：握手已经拿到，坐标迟早会来。
            if health.state in (CAPTURE_HEALTH_OK, CAPTURE_HEALTH_DECODING):
                return health
            # 又错过了一次：再等下去也不会自己好，交给调用方决定要不要再试
            if health.needs_new_connection:
                return health
            self.sleep(max(0.2, float(tick)))
            health = self.capture_position_source().health()
        return health

    def ensure_capture_session(self, *, reason: str) -> bool:
        """校验抓包是否**真的**接上了；后端在跑但当前没有坐标时，回登录界面重进一次。

        这是「启用后第一拍 / 每次导航前 / 每次切号后」三个时机共用的入口。返回抓包当前是否可用。

        "可用" = 已经有坐标，或握手已成、第一条坐标在路上（``ok`` / ``decoding``）。

        会触发重建的只有**后端在跑、但当前没有坐标且不会自己恢复**这一种：``no_connection``
        （后端一条游戏连接都没看到）与 ``missed_handshake``（看到连接却没过握手）。前者多半是
        后端晚于游戏连接才开始抓，后者是错过了 SYN——两种都靠"退到登录界面再进入"逼一条新连接，
        让后端从连接开头抓握手。

        ``handshake_pending``（握手正在进行）与 ``backend_down``（后端不可达）都不动手：前者再等
        一会就好，后者重建也没用——对它们做"退出重进"只会白白打断玩家。

        最多尝试 :data:`CAPTURE_RELOGIN_ATTEMPTS` 次，**成功即清零**：重登后能正常抓包，下一次
        再出问题理应还能修。次数用尽就停手并报错，绝不在无人值守时反复登出。
        """

        health = self.capture_session_health()
        if health is None:
            return False
        if health.state in (CAPTURE_HEALTH_OK, CAPTURE_HEALTH_DECODING):
            self._minimap_capture_relogin_attempts = 0
            self._minimap_capture_relogin_warned = False
            return True
        if health.state not in (CAPTURE_HEALTH_NO_CONNECTION, CAPTURE_HEALTH_MISSED_HANDSHAKE):
            return False
        if not self._cfg_bool(CONFIG_EXPERIMENTAL_RELOGIN, True):
            self._warn_capture_relogin_disabled()
            return False
        if self._minimap_capture_relogin_attempts >= CAPTURE_RELOGIN_ATTEMPTS:
            self._warn_capture_relogin_exhausted(health)
            return False
        # 本方法每拍都会被调（"没坐标"时），没有这个间隔的话三次尝试会在一分钟内打光：
        # 实测两次之间只隔了 14 秒，而每次都要把玩家从游戏里踢回登录界面——太激进了。
        now = self.active_time()
        if now < self._minimap_capture_relogin_next_at:
            return False
        self._minimap_capture_relogin_next_at = now + CAPTURE_RELOGIN_INTERVAL_SECONDS

        self._minimap_capture_relogin_attempts += 1
        attempt = self._minimap_capture_relogin_attempts
        self.log_warning(
            f"实验技术：{reason}——本次抓包没有可用坐标（{health.detail or health.state}），"
            f"正在返回登录界面重新进入以建立新连接（第 {attempt}/{CAPTURE_RELOGIN_ATTEMPTS} 次）",
            notify=True,
        )
        if not self.force_new_capture_connection():
            return False

        after = self.wait_for_capture_connection(timeout=CAPTURE_RELOGIN_WAIT_SECONDS)
        if after.state in (CAPTURE_HEALTH_OK, CAPTURE_HEALTH_DECODING):
            self._minimap_capture_relogin_attempts = 0
            self.log_info("实验技术：新连接已被后端接管，抓包恢复")
            return True
        self.log_warning(
            f"实验技术：重新进入后仍没抓到握手（{after.state}）"
            + ("，稍后会自动再试" if attempt < CAPTURE_RELOGIN_ATTEMPTS else ""),
            notify=True,
        )
        return False

    def _warn_capture_relogin_disabled(self) -> None:
        if self._minimap_capture_relogin_warned:
            return
        self._minimap_capture_relogin_warned = True
        self.log_warning(
            "实验技术：本次抓包没有可用坐标，而「抓包失败时自动重新登录」是关着的——"
            "请手动退回登录界面再进入一次，或打开该开关",
            notify=True,
        )

    def _warn_capture_relogin_exhausted(self, health: CaptureHealth) -> None:
        if self._minimap_capture_relogin_warned:
            return
        self._minimap_capture_relogin_warned = True
        self.log_warning(
            f"实验技术：已尝试 {CAPTURE_RELOGIN_ATTEMPTS} 次重建连接仍未抓到握手（{health.detail or health.state}）。"
            "请手动重启游戏后再启用抓包；本条不再自动重试",
            notify=True,
        )

    def stop_capture_backend(self) -> None:
        """按配置停掉后端。默认不停——见 :data:`CONFIG_EXPERIMENTAL_STOP_ON_EXIT` 的说明。"""

        launcher = self._minimap_capture_launcher
        if launcher is None:
            return
        if self._cfg_bool(CONFIG_EXPERIMENTAL_STOP_ON_EXIT, False):
            launcher.stop()
        self._minimap_capture_launcher = None

    def _capture_world_pose(self, *, now=None) -> dict:
        """把抓包样本组装成定位状态契约（直通：抓包给什么就用什么）。

        与融合链的关键差别是整个校准体系都不参与：坐标本来就是绝对值，没有"锚点"可设、
        也没有漂移可修。因此只要坐标可用，``sync_needed`` 就恒为假——否则导航的
        ``CALIBRATE`` 状态会把车停下来等一个永远不会到来的静止校准。

        但"绝对"不等于"当前"：坐标是**某一条 TCP 连接**解出来的，而那条连接会随着登出、
        换账号、重启游戏而结束。后端把最后一次解出的坐标一直留着（它的 ``state.last`` 没有
        清空点），所以重登之后、新会话报出第一条坐标之前，``/api/state`` 给的仍是**上一局**
        的坐标——那可能是另一张地图、乃至另一个账号的位置。这种坐标对导航比"没有坐标"更糟，
        所以这里按下面两条扣掉信任，交给 :meth:`_capture_trust` 判：

        * 产出它的连接已经不再喂数据（``WAIT_TRUST``，带 ``capture_session_ended``）；
        * 认不出地图（``WAIT_TRUST``，带 ``capture_no_parent_map`` / ``capture_unknown_map``）
          ——**决不回退到调用方请求的 map_id**：实测场景 463 是自带独立坐标空间的可走区域，
          拿它当 map02 会在错的网格上从错的起点规划路线。

        两条都只是扣信任、不清空坐标（``x``/``z`` 照给），这样导航停在 ``WAIT_TRUST`` 时能把
        真正的原因打出来，而不是含糊地报"未锚定"。
        """

        # 不传本任务时钟：抓包源要用**墙钟**去减后端记录的 Unix 时间戳算陈旧度，
        # 传 active_time()（time.monotonic）进去会让差值恒为负、被夹成 0。
        sample = self.capture_position_source().poll()
        if sample is None:
            # 后端没数据：不发布伪造坐标。沿用上一帧只会让导航拿着旧位置继续走，
            # 所以这里显式把坐标置空，让消费者看到"位置不可用"。
            # 刻意不清空 ``_minimap_capture_last_flow``：数据中断后接上的如果还是同一条连接，
            # 就不该被当成新的一局而让导航作废"走过的已知格"。
            return {
                "x": None,
                "y": None,
                "z": None,
                "heading": None,
                "heading_score": None,
                "map_id": None,
                "anchor_set": False,
                "position_trusted": False,
                "trust_reason": "capture_unavailable",
                "rest": False,
                "odom_ok": False,
                "odom_reason": "capture_unavailable",
                "sync_needed": False,
                "sync_checked": True,
                # 校准不成立是**数据源的性质**，与此刻有没有坐标无关，所以这条分支也给。
                "sync_supported": False,
                "sync_seq": self._minimap_capture_seq,
                "distance_since_sync": 0.0,
            }

        # ``sync_seq`` 只在**换了连接**（换了一局）时推进：导航把它解释成"真重锚 → 世界坐标
        # 整体平移 → 已走过的已知格作废"，并在 ``NavSession.note_sync`` 里清空
        # ``visited_cells``。抓包源给的是绝对坐标，移动时每拍都在变；若按"采样变了"就推进，
        # 移动本身就会每拍清空导航的历史格，偏航回退（网格导航文档 §7.5）会退化成
        # "没有已走过的已知格可返回"。
        #
        # 判据必须用连接号而不是坐标：换连接时坐标可能逐位相同（"登出后在原地重新登录"），
        # 那种情况只有 ``flow`` 变了。连接号识别不出来（``None``）时按"同一局"处理，宁可少
        # 失效一次，也不要把正常移动误判成重锚。
        if sample.flow != self._minimap_capture_last_flow:
            self._minimap_capture_last_flow = sample.flow
            self._minimap_capture_seq += 1

        rest = sample.speed is not None and abs(sample.speed) < REST_SPEED_THRESHOLD
        trusted, trust_reason = self._capture_trust(sample)
        return {
            "x": sample.x,
            "y": sample.y,
            "z": sample.z,
            # 朝向与 src/nav/route_follower.bearing_to_point 同一套约定（正北 0、顺时针），
            # 是游戏上报的真值而非图像推断，因此置信度给满。
            "heading": sample.heading,
            "heading_score": None if sample.heading is None else 1.0,
            "map_id": sample.map_id or None,
            "anchor_set": trusted,
            "position_trusted": trusted,
            "trust_reason": trust_reason,
            "rest": rest,
            "odom_ok": True,
            "odom_reason": "capture",
            "sync_needed": False,
            "sync_checked": True,
            # 契约字段：直通源上"静止校准"这个概念不成立，导航据此不必停车等校准。
            # 直接置为 False，而不是让导航去比对 source 名称（导航层不该知道本模块的命名）。
            "sync_supported": False,
            "just_synced": False,
            "sync_seq": self._minimap_capture_seq,
            "distance_since_sync": 0.0,
            # 以下字段只供状态展示，不进发布契约（build_world_pose 会略过未知键）。
            "capture_age": sample.age_seconds,
            "capture_speed": sample.speed,
            "capture_map_id_source": sample.map_id_source,
            "capture_scene": sample.scene,
            "capture_flow": sample.flow,
            "capture_flow_status": sample.flow_status,
            "capture_flow_alive": sample.flow_alive,
        }

    @staticmethod
    def _capture_trust(sample) -> tuple[bool, str]:
        """这条抓包坐标能不能被导航当真值；返回 ``(可信, 原因)``。

        只做两件事，都遵循"没有把握就不给导航用"：先看产出它的连接是否还在，再看地图是否
        认得出来。坐标数值本身**不**在这里判——数值是不是合理是后端的事，这里只判"这份坐标
        描述的是不是当下的位置"。
        """

        if not sample.flow_alive:
            return False, "capture_session_ended"
        if not sample.map_id:
            return False, (
                "capture_no_parent_map" if sample.map_id_source == "no_parent_map" else "capture_unknown_map"
            )
        return True, CAPTURE_SOURCE_NAME

    def _minimap_position_source_name(self) -> str:
        """返回当前定位数据源名称，用于状态契约和诊断。"""

        if self.experimental_capture_enabled():
            return CAPTURE_SOURCE_NAME
        if self._is_map_ws_client_enabled():
            return "official_ws"
        if self._is_ws_position_server_enabled():
            return "local_ws"
        return "minimap"

    def _update_sync_request(self, st: dict) -> None:
        """更新“距离上次校准的累计移动量”，供导航决定何时停车请求校准。"""
        x, z = st.get("x"), st.get("z")
        map_id = st.get("map_id")
        if bool(st.get("just_synced")) or bool(st.get("sync_checked")):
            self._minimap_distance_since_sync = 0.0
            self._minimap_prev_position = (float(x), float(z)) if x is not None and z is not None else None
            self._minimap_prev_map_id = str(map_id) if map_id is not None else None
        elif x is not None and z is not None:
            position = (float(x), float(z))
            if self._minimap_prev_position is not None and map_id == self._minimap_prev_map_id:
                moved = math.hypot(
                    position[0] - self._minimap_prev_position[0],
                    position[1] - self._minimap_prev_position[1],
                )
                if moved <= 10.0:
                    self._minimap_distance_since_sync += moved
            self._minimap_prev_position = position
            self._minimap_prev_map_id = str(map_id) if map_id is not None else None
        elif self._minimap_prev_map_id != map_id:
            self._minimap_distance_since_sync = 0.0
            self._minimap_prev_position = None
            self._minimap_prev_map_id = str(map_id) if map_id is not None else None

        threshold = max(0.0, self._cfg_float(CONFIG_SYNC_DISTANCE, 100.0))
        st["distance_since_sync"] = self._minimap_distance_since_sync
        st["sync_needed"] = bool(
            self._minimap_last_ws is not None and threshold > 0 and self._minimap_distance_since_sync >= threshold
        )

    def latest_minimap_state(
        self,
        *,
        max_age: float | None = None,
        now: float | None = None,
    ) -> dict | None:
        """返回最近一次定位快照；超过 ``max_age`` 时返回 None。"""
        state = self._minimap_last_state
        if state is None:
            return None
        if max_age is not None:
            sample_t = state.get("sample_t")
            if sample_t is None or self._now(now) - float(sample_t) > max(0.0, float(max_age)):
                return None
        return dict(state)

    def _now(self, now):
        return self.active_time() if now is None else now

    def _feed_ws_position(self, st: dict, now, *, allow_sync: bool = True) -> bool:
        """消费一条新到的 WS 位置：静止时用它校准，否则忽略（不暂存）。返回本拍是否已校准。"""
        pos_ws = self._poll_ws_position(timeout=0.0)
        if pos_ws is None:
            return False
        x, y, z, map_id = pos_ws
        if self._minimap_ws_map_id is None:
            self._minimap_ws_map_id = map_id
        elif self._minimap_ws_map_id != map_id:
            # 换地图：旧地图的锚点在新地图上没有意义，清掉重来（等再次静止自动锚定）。
            # 本拍必须把坐标字段改成"未锚定"，否则会返回上一张地图的坐标。
            self.log_info(f"地图已切换 {self._minimap_ws_map_id} -> {map_id}，清空锚点等待重新校准")
            self._minimap_ws_map_id = map_id
            self._minimap_fusion.reset()
            if self._minimap_visual_anchor_chain is not None:
                self._minimap_visual_anchor_chain.reset()
            if self._minimap_keyframe_visual_anchor_chain is not None:
                self._minimap_keyframe_visual_anchor_chain.reset()
            self._minimap_keyframe_counter = 0
            self._minimap_keyframe_uid = 0
            self._minimap_last_ws = (x, z)
            self._minimap_last_ws_xyz = (x, y, z)
            self._minimap_position_trusted = False
            self._minimap_trust_reason = "map_changed"
            self._apply_estimate(st, self._minimap_fusion.estimate())
            return False

        synced = self._minimap_fusion.try_sync(
            pos_ws,
            map_id=map_id,
            now=now,
            allow_sync=allow_sync,
        )
        self._minimap_last_ws = (x, z)
        self._minimap_last_ws_xyz = (x, y, z)
        st["sync_checked"] = synced
        st["sync_redundant"] = bool(synced and self._minimap_fusion.last_sync_redundant)
        # 空操作（估计已与 WS 重合）不算"刚校准"：不重读估计、也不产生新的残差，
        # 这样静止时不会每秒刷一条 [静止校准]，上一次真正有意义的残差也留得住。
        if synced and not self._minimap_fusion.last_sync_redundant:
            self._minimap_sync_seq += 1
            self._apply_estimate(st, self._minimap_fusion.estimate())
            st["just_synced"] = True
            st["sync_residual"] = self._augment_sync_residual(self._minimap_fusion.last_sync_residual)
        if synced:
            self._minimap_position_trusted = True
            self._minimap_trust_reason = "sync"
        elif not self._minimap_position_trusted and self._minimap_fusion.should_force_sync():
            self._minimap_fusion.force_sync(pos_ws, map_id=map_id, now=now)
            self._minimap_sync_seq += 1
            self._apply_estimate(st, self._minimap_fusion.estimate())
            st["just_synced"] = True
            st["sync_checked"] = True
            st["sync_residual"] = self._augment_sync_residual(self._minimap_fusion.last_sync_residual)
            self._minimap_position_trusted = True
            self._minimap_trust_reason = "forced_sync"
            last_result = self._minimap_od.last_result() or {}
            self.log_warning(
                "连续稳定 WS 但地图里程计无法确认静止，执行强制重锚: "
                f"ws_hits={self._minimap_fusion.ws_stable_hits} "
                f"odom_reason={last_result.get('reason')}"
            )
            return True
        return synced

    def _augment_sync_residual(self, residual: dict | None) -> dict | None:
        """在原始融合校准残差上补充校准前最后一拍的锚定链校正误差。"""

        if residual is None:
            return None
        previous = self._minimap_last_state
        if previous is None:
            return dict(residual)
        visual_x = previous.get("x")
        visual_z = previous.get("z")
        ws_x = residual.get("ws_x")
        ws_z = residual.get("ws_z")
        if visual_x is None or visual_z is None or ws_x is None or ws_z is None:
            return dict(residual)

        out = dict(residual)
        vx = float(visual_x)
        vz = float(visual_z)
        out["visual_anchor_chain_x"] = vx
        out["visual_anchor_chain_z"] = vz
        out["visual_anchor_chain_dx"] = vx - float(ws_x)
        out["visual_anchor_chain_dz"] = vz - float(ws_z)
        out["visual_anchor_chain_dist"] = math.hypot(
            vx - float(ws_x),
            vz - float(ws_z),
        )
        out["visual_anchor_chain_reason"] = previous.get("visual_anchor_chain_reason")
        return out

    @staticmethod
    def _apply_estimate(st: dict, est: dict | None) -> None:
        """把融合估计写回本拍结果；未锚定（``est is None``）时把坐标字段清成 None。"""
        if est is None:
            st.update(
                {
                    "x": None,
                    "z": None,
                    "anchor_set": False,
                    "dmap_px": (0.0, 0.0),
                    "world_delta": (0.0, 0.0),
                }
            )
            return
        st.update(est)

    # ------------------------------------------------------------------ #
    # 只读访问（给需要更细控制的调用方）
    # ------------------------------------------------------------------ #
    @property
    def minimap_odometry(self) -> MinimapOdometry | None:
        """里程计实例（累计位移 ``position_px()`` / ``forward_strafe_m()`` 等）。"""
        return self._minimap_od

    @property
    def minimap_fusion(self) -> MinimapPositionFusion | None:
        """融合实例（``estimate()`` / ``last_sync_residual`` / ``rest_diag`` 等）。"""
        return self._minimap_fusion

    @property
    def minimap_position_ready(self) -> bool:
        """定位器是否已完成初始化，可安全调用 :meth:`minimap_position`。

        抓包直通模式下恒为真：那条路根本不经过里程计与融合（:meth:`sample_world_pose` 会走
        :meth:`_capture_world_pose`），而 :meth:`minimap_position` 也只在非抓包分支被调用。
        之所以要让它返回真，是因为这个属性被调用方当成"定位服务可用吗"的闸门——抓包模式下用
        里程计对象在不在来判断，会拿一个本模式根本不需要的条件把整条路挡死。
        """

        if self.experimental_capture_enabled():
            return True
        return self._minimap_od is not None and self._minimap_fusion is not None

    def minimap_rest_diag(self) -> dict | None:
        """最近一次静止判定的实测值（``map_speed_m_s`` / ``ws_moved_m`` / ``reason``）。"""
        if self._minimap_fusion is None:
            return None
        return self._minimap_fusion.rest_diag
