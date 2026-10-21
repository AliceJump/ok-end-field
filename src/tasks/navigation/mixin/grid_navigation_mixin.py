"""网格规划与游戏内导航执行。

网格存 ``0=未知 / 1=可行走 / 2=阻挡``，世界换算为
``world = origin + (i, j) * cell_size``：行沿世界 Z、列沿世界 X。
规划航点因此统一使用 ``(x, z)`` 世界坐标。

本 mixin 只负责编排，不拥有定位源：

- ``WorldPoseService`` 提供共享的实时 ``(x, z)``、朝向和可信状态；
- ``GridPlanner`` 在 ``*.grid.npz`` 上做 A*；
- ``GridRouteFollower`` 输出 ``TURN/WALK/WAIT/STUCK/REPLAN/DONE``；
- 本层把动作转换为鼠标、键盘输入，并处理校准、重规划和脱困。

滑索场景使用模板判断“是否在滑索架上”，WS 坐标只用于判断到达、起点未触发或错误
滑索架。错误落点不会先下索；本层会按当前实际滑索节点重新规划，并保证同一滑索架
上的连续路线在一次执行中完成。

导航主循环的信任顺序不可颠倒：

1. 等绝对坐标锚定；
2. 等 ``position_trusted=True``；普通重锚后先做静止校准，良性 ``too_long_dt`` 不撤销信任；
3. 等本拍有有效里程计样本；
4. 再允许 follower 规划或继续行走。

偏航恢复优先原地校准；若当前位置不属于已知 free 格，则先返回本次实际走过的最近
已知格，校准后再重新规划。

模块分层、坐标约定、规划代价与排查顺序见 ``docs/dev/网格导航.md``。
"""

from __future__ import annotations

import math
from enum import StrEnum
from pathlib import Path

from src.core.global_config_store import get_global_config
from src.core.GridNavConfig import (
    CONFIG_GRID_ALLOW_UNKNOWN,
    CONFIG_GRID_CALIBRATION_WAYPOINTS,
    CONFIG_GRID_DIR,
    CONFIG_GRID_FILE,
    CONFIG_GRID_FRONTIER_MARGIN,
    CONFIG_GRID_FRONTIER_PENALTY,
    CONFIG_GRID_GOAL_RADIUS,
    CONFIG_GRID_HEADING_TOLERANCE,
    CONFIG_GRID_MARGIN,
    CONFIG_GRID_MAX_EXPAND,
    CONFIG_GRID_MAX_RECOVERIES,
    CONFIG_GRID_MAX_REPLANS,
    CONFIG_GRID_MAX_TURN_ROUNDS,
    CONFIG_GRID_MOVING_TURN_GAIN,
    CONFIG_GRID_MOVING_TURN_MAX_DEG,
    CONFIG_GRID_MOVING_TURN_MAX_START_DEG,
    CONFIG_GRID_MOVING_TURN_MIN_DISTANCE,
    CONFIG_GRID_NARROW_CALIBRATION,
    CONFIG_GRID_NARROW_STOP_RADIUS,
    CONFIG_GRID_RECOVERY_TIME,
    CONFIG_GRID_RISK_COST,
    CONFIG_GRID_SHORTCUT_RADIUS,
    CONFIG_GRID_STUCK_DISTANCE,
    CONFIG_GRID_STUCK_WINDOW,
    CONFIG_GRID_TICK,
    CONFIG_GRID_TIMEOUT,
    CONFIG_GRID_TURN_TOLERANCE,
    CONFIG_GRID_TURN_WHILE_MOVING,
    CONFIG_GRID_USE_SHARED_ZIP_LINES,
    CONFIG_GRID_USE_ZIP_LINES,
    CONFIG_GRID_WALL_PENALTY,
    CONFIG_GRID_WAYPOINT_RADIUS,
    CONFIG_GRID_WAYPOINT_TOLERANCE,
    CONFIG_GRID_ZOOM,
    DEFAULT_GRID_NAV_CONFIG,
    GRID_HEADING_TOLERANCE_DEG,
    GRID_NAV_CONFIG_DESCRIPTION,
    GRID_TURN_TOLERANCE_DEG,
)
from src.core.NavConfig import NAV_CONFIG_NAME
from src.data import map_mark_query, user_map_mark_store
from src.data.FeatureList import FeatureList as fL
from src.nav.grid_io import GRID_SUFFIX, DenseGrid, load_grid
from src.nav.grid_planner import GridPlanner, PlanResult
from src.nav.layered_map import LayeredMap, LayerKey, TierLink, layer_key, load_layered_map, resolve_tier_by_height
from src.nav.layered_planner import LayeredPlanner, LayeredRoute
from src.nav.nav_state import (
    GridRouteRequest,
    NavSession,
    NavState,
    NavTick,
    SyncReason,
    SyncWait,
)
from src.nav.route_follower import (
    DONE,
    FAILED,
    NARROW_CALIBRATE,
    REPLAN,
    STUCK,
    TURN,
    WAIT,
    WALK,
    ZIP_LINE,
    FollowerConfig,
    FollowerStep,
    GridRouteFollower,
    angle_delta,
    bearing_to_point,
    distance_xz,
)
from src.nav.zip_line_graph import ZIP_LINE_POWER_FACILITY_NAMES, ZipLineGraph, ZipLineNode
from src.tasks.mixin.runtime_state_mixin import RuntimeStateMixin
from src.tasks.navigation.mixin.zip_line_mixin import ZipLineReplanRequired

GRID_ZIP_LINE_ESC_THRESHOLD = 0.8
GRID_ZIP_LINE_APPROACH_DISTANCE_M = 5.0
GRID_ZIP_LINE_APPROACH_TIMEOUT_S = 20.0
GRID_ZIP_LINE_RETREAT_DURATION_S = 0.6
GRID_ZIP_LINE_BOARD_CONFIRM_TIMEOUT_S = 1.5
GRID_ZIP_LINE_BOARD_CONFIRM_POLL_S = 0.15
GRID_ZIP_LINE_WS_STEP_DURATION_S = 0.6
GRID_ZIP_LINE_WS_MIN_STEP_DURATION_S = 0.3
GRID_ZIP_LINE_WS_STOP_DISTANCE_M = 0.6
GRID_ZIP_LINE_PROMPT_POLL_S = 0.1
GRID_ZIP_LINE_LOCAL_SEARCH_STEP_S = 0.15
GRID_ZIP_LINE_APPROACH_MAX_ATTEMPTS = 3
GRID_NARROW_CALIBRATION_MAX_ATTEMPTS = 3
GRID_SPRINT_MIN_SEGMENT_METERS = 15.0

__all__ = [
    "CONFIG_GRID_ALLOW_UNKNOWN",
    "CONFIG_GRID_CALIBRATION_WAYPOINTS",
    "CONFIG_GRID_DIR",
    "CONFIG_GRID_FILE",
    "CONFIG_GRID_FRONTIER_MARGIN",
    "CONFIG_GRID_FRONTIER_PENALTY",
    "CONFIG_GRID_GOAL_RADIUS",
    "CONFIG_GRID_HEADING_TOLERANCE",
    "CONFIG_GRID_MARGIN",
    "CONFIG_GRID_MAX_EXPAND",
    "CONFIG_GRID_MAX_RECOVERIES",
    "CONFIG_GRID_MAX_REPLANS",
    "CONFIG_GRID_MAX_TURN_ROUNDS",
    "CONFIG_GRID_MOVING_TURN_GAIN",
    "CONFIG_GRID_MOVING_TURN_MAX_DEG",
    "CONFIG_GRID_MOVING_TURN_MAX_START_DEG",
    "CONFIG_GRID_MOVING_TURN_MIN_DISTANCE",
    "CONFIG_GRID_RECOVERY_TIME",
    "CONFIG_GRID_RISK_COST",
    "CONFIG_GRID_SHORTCUT_RADIUS",
    "CONFIG_GRID_STUCK_DISTANCE",
    "CONFIG_GRID_STUCK_WINDOW",
    "CONFIG_GRID_TICK",
    "CONFIG_GRID_TIMEOUT",
    "CONFIG_GRID_TURN_TOLERANCE",
    "CONFIG_GRID_TURN_WHILE_MOVING",
    "CONFIG_GRID_USE_ZIP_LINES",
    "CONFIG_GRID_WALL_PENALTY",
    "CONFIG_GRID_WAYPOINT_RADIUS",
    "CONFIG_GRID_WAYPOINT_TOLERANCE",
    "CONFIG_GRID_ZOOM",
    "GridNavigationMixin",
]


#: 非绳索的跨层连接按"走过去"处理时，两端允许的最大水平距离（米）。
#: 实测数据里 7 条连接全在 1.0~2.5m；超过这个值说明"两端几乎在同一处"的前提不成立，
#: 本实现不会替角色搬动位置，只能告警（见 :meth:`GridNavigationMixin._cross_layered_link`）。
_GRID_LINK_WALK_MAX_SPAN_M = 10.0

#: 绳索跨越时"按住 W 走向落点/等高度变化"的轮询周期（秒）。
GRID_ROPE_POLL_S = 0.1
#: 绳索**下行**：按住 W 走向落点、等高度真正落到下一层的时间上限（秒）。
#: 实测两端水平只差 1.5m，正常一两次轮询就落地；超时说明没走下去，必须报出来。
GRID_ROPE_DESCENT_TIMEOUT_S = 6.0
#: 绳索**下行**但目标层没有标定高度区间时，用来收尾的最短水平位移（米）。
#: 没有高度就验不了"落到哪一层"，只能按"至少走过了落差段"收尾并记日志。
GRID_ROPE_DESCENT_MIN_SPAN_M = 3.0
#: 绳索**上行**：等 ``fL.gather_icon2`` 交互图标出现的时间上限（秒）。
GRID_ROPE_ICON_TIMEOUT_S = 5.0
#: 绳索**上行**：点完图标后等高度升到目标层的时间上限（秒）。
GRID_ROPE_RIDE_TIMEOUT_S = 6.0
#: 绳索下行时，实测方位与目标方位的夹角小于这个值就不重复转视角（免得每拍动鼠标）。
GRID_ROPE_REAIM_DEADBAND_DEG = 20.0
#: 绳索下行时，与落点的水平距离小于这个值就不再重新对准：近垂直的绳索在几乎正下方
#: 时方位会剧烈抖动，继续转视角只会让角色原地打转。
GRID_ROPE_AIM_MIN_SPAN_M = 2.0
#: 绳索下行"按住 W 但连着这么久没有任何位移"就单独报这个故障（秒）。
GRID_ROPE_STALL_S = 2.0
#: 判定"这一拍到底动没动"的三维位移阈值（米）。
GRID_ROPE_STALL_EPS_M = 0.05


def _rope_goes_up(
    source_range: tuple[float, float] | None,
    target_range: tuple[float, float] | None,
) -> bool | None:
    """绳索跨越是上行吗；``None`` 表示判不出来。

    判据是两端**所在层**已标定的高度区间（``floor_regions``）：连接端点本身没有 Y，
    这是唯一能自证的数据。区间缺失或重叠时返回 ``None`` 让调用方明确失败——猜错方向
    要么白等图标、要么在崖壁上一直撞墙。

    Args:
        source_range: 起点所在层的 ``(y_min, y_max)``；``None`` 表示该层没标定。
        target_range: 落点所在层的 ``(y_min, y_max)``。
    """
    if source_range is None or target_range is None:
        return None
    if target_range[0] > source_range[1]:
        return True
    if target_range[1] < source_range[0]:
        return False
    return None


#: 状态 → 处理器方法名。每个 :class:`NavState` 都必须在这里登记，主循环按状态取方法；
#: 新增状态却忘了登记时，``tests.TestGridNavigationMixin`` 里的完备性用例会直接失败。
_GRID_NAV_HANDLERS: dict[NavState, str] = {
    NavState.WAIT_POSE: "_grid_nav_state_wait_pose",
    NavState.WAIT_ANCHOR: "_grid_nav_state_wait_anchor",
    NavState.WAIT_TRUST: "_grid_nav_state_wait_trust",
    NavState.WAIT_ODOM: "_grid_nav_state_wait_odom",
    NavState.CALIBRATE: "_grid_nav_state_calibrate",
    NavState.WAIT_HEADING: "_grid_nav_state_wait_heading",
    NavState.PLAN: "_grid_nav_state_plan",
    NavState.ZIP_LINE_APPROACH: "_grid_nav_state_zip_line_approach",
    NavState.WAYPOINT_ARRIVED: "_grid_nav_state_waypoint_arrived",
    NavState.STOP_REACHED: "_grid_nav_state_stop_reached",
    NavState.REPLAN: "_grid_nav_state_replan",
    NavState.ZIP_LINE: "_grid_nav_state_zip_line",
    NavState.DONE: "_grid_nav_state_done",
    NavState.STUCK: "_grid_nav_state_stuck",
    NavState.NARROW_CALIBRATE: "_grid_nav_state_narrow_calibrate",
    NavState.TURN: "_grid_nav_state_turn",
    NavState.WALK: "_grid_nav_state_walk",
    NavState.WAIT: "_grid_nav_state_wait",
    NavState.FAILED: "_grid_nav_state_failed",
}

#: 跟随器动作 → 状态。只有走过定位闸门之后才会用到，``REPLAN`` 与 ``ZIP_LINE``
#: 的优先级更高，因此在这里由 :meth:`GridNavigationMixin._grid_nav_action_state`
#: 先判，再看这张表。
_GRID_NAV_ACTION_STATES: dict[str, NavState] = {
    REPLAN: NavState.REPLAN,
    ZIP_LINE: NavState.ZIP_LINE,
    DONE: NavState.DONE,
    STUCK: NavState.STUCK,
    NARROW_CALIBRATE: NavState.NARROW_CALIBRATE,
    TURN: NavState.TURN,
    WALK: NavState.WALK,
    WAIT: NavState.WAIT,
    FAILED: NavState.FAILED,
}

#: 需要先记"目标距离进展"和状态提示的状态：对应旧主循环里"到达航点"分支之后、
#: 动作分支之前的那段共享代码。滑索接近、重规划、滑索、到达航点都在它之前。
_GRID_NAV_TAIL_STATES = frozenset(
    {
        NavState.DONE,
        NavState.STUCK,
        NavState.NARROW_CALIBRATE,
        NavState.TURN,
        NavState.WALK,
        NavState.WAIT,
        NavState.FAILED,
    }
)


class StuckRecovery(StrEnum):
    """卡住脱困的一步动作。"""

    #: 按 ``S`` 后退。
    BACK = "back"
    #: 按 ``S`` 加一侧横移，绕开卡住的墙角。
    BACK_SIDE = "back_side"
    #: 对准目标后按住 ``W`` 加 ``Space`` 跳过去。
    AIM_JUMP = "aim_jump"


#: 卡住脱困的动作序列：按尝试次数依次使用，尝试次数超出序列长度时重复最后一步。
_GRID_STUCK_RECOVERY: tuple[StuckRecovery, ...] = (
    StuckRecovery.BACK,
    StuckRecovery.BACK_SIDE,
    StuckRecovery.AIM_JUMP,
)


class ZipLineBoardAttempt(StrEnum):
    """一次"按「登上滑索架」并用 ``fL.esc`` 确认"的结果。"""

    #: 确认已站上滑索架（``fL.esc`` 已消失）。
    BOARDED = "boarded"
    #: 没找到「登上滑索架」按钮。
    BUTTON_MISSING = "button_missing"
    #: 点了按钮但无法确认是否登上（``fL.esc`` 判定超时）。
    UNCONFIRMED = "unconfirmed"
    #: 确认没登上（``fL.esc`` 仍在），可以重新对准后重试。
    NOT_BOARDED = "not_boarded"


class GridNavigationMixin(RuntimeStateMixin):
    """在 ``*.grid.npz`` 上规划并驱动角色前往世界坐标 ``(x, z)``。

    定位由共享的 ``WorldPoseService`` 提供；本类只消费状态、执行动作和维护本次
    导航的重规划/脱困/校准计数。各动作的纯决策逻辑在 ``GridRouteFollower``。
    """

    @staticmethod
    def grid_navigation_default_config() -> dict:
        """返回网格导航默认值；运行时实际读取全局 ``Nav Config``。"""
        return dict(DEFAULT_GRID_NAV_CONFIG)

    @staticmethod
    def grid_navigation_config_description() -> dict:
        """返回网格导航说明；实值已在全局 ``Nav Config`` 中注册。"""
        return dict(GRID_NAV_CONFIG_DESCRIPTION)

    def _grid_nav_config(self):
        """返回全局导航配置；测试替身可覆盖此方法注入内存配置。"""
        return get_global_config(NAV_CONFIG_NAME)

    def _grid_config_get(self, key: str, default=None):
        """读取全局导航配置；兼容尚未完成迁移的旧任务配置。"""
        nav_config = self._grid_nav_config()
        if nav_config is not None and key in nav_config:
            return nav_config.get(key, default)
        return self.config.get(key, default)

    def _grid_cfg_float(self, key: str, default) -> float:
        try:
            return float(self._grid_config_get(key, default))
        except (TypeError, ValueError):
            return float(default)

    def _grid_cfg_int(self, key: str, default) -> int:
        try:
            return int(self._grid_config_get(key, default))
        except (TypeError, ValueError):
            return int(default)

    def _grid_cfg_bool(self, key: str, default) -> bool:
        raw = self._grid_config_get(key, default)
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "yes", "on", "是", "开启", "开")
        return bool(raw)

    def _init_grid_navigation_mixin(self) -> None:
        """初始化导航状态；不创建或启动任何定位、输入资源。"""
        self._init_runtime_state_mixin()
        self._grid_nav_follower: GridRouteFollower | None = None
        self._grid_nav_map_id = ""
        self._grid_nav_w_held = False
        self._grid_nav_sprint_active = False
        self._grid_nav_last_wait_log = 0.0
        self._grid_minimap_position_service = None
        self._grid_nav_last_info_key = None
        self._grid_nav_last_info_at = 0.0
        self._grid_nav_last_debug_key = None
        self._grid_nav_last_debug_at = 0.0
        self._grid_nav_zip_line_cache: dict[str, ZipLineGraph] = {}
        self._grid_nav_zip_line_empty: set[str] = set()
        self._grid_nav_zip_line_unavailable: set[str] = set()
        self._grid_nav_zip_line_start_hint: tuple[float, float] | None = None
        self._grid_nav_zip_line_failed_target_hint: tuple[float, float] | None = None
        self._grid_nav_zip_line_approach_entry_id: str | None = None
        self._grid_nav_zip_line_approach_started_at: float | None = None
        self._grid_nav_zip_line_approach_attempts = 0
        self._grid_nav_zip_line_walk_mode = False
        self._grid_nav_blocked_zip_connections: set[
            tuple[str, tuple[float, float, float], tuple[float, float, float]]
        ] = set()
        self._grid_nav_skip_board_node_id: str | None = None
        #: 分层地图缓存：键 (目录, 地图, zoom) -> LayeredMap；False 表示确认没有分层数据。
        #: 网格是子模块里的静态数据，同一次会话内不必反复读 npz。
        self._grid_nav_layered_cache: dict[tuple[str, str, str], LayeredMap | bool] = {}
        #: 分层规划器缓存；键取 LayeredMap 的对象身份，值持有它的层内分段缓存。
        self._grid_nav_layered_planner_cache: dict[int, LayeredPlanner] = {}
        #: 本次分层路线依据的 LayeredMap；换段时按它取当前层的网格。
        self._grid_nav_layered_active: LayeredMap | None = None

    def _reset_grid_navigation_run_state(self) -> None:
        """重置只对本次 ``navigate_grid_to`` 有效的运行状态。"""
        self._grid_nav_follower = None
        self._grid_nav_zip_line_start_hint = None
        self._grid_nav_zip_line_failed_target_hint = None
        self._grid_nav_zip_line_approach_entry_id = None
        self._grid_nav_zip_line_approach_started_at = None
        self._grid_nav_zip_line_approach_attempts = 0
        self._grid_nav_skip_board_node_id = None
        self._grid_nav_layered_active = None
        self._set_grid_walking(False)

    def pause(self) -> None:
        """暂停时立刻松开 ``W``：框架的暂停本身不会替任务释放按键。

        网格导航靠长按 ``W`` 前进（见 :meth:`_set_grid_walking`），暂停又不释放的话，
        用户点暂停后角色会一直走到下一次 ``unpause`` 被主循环纠正——期间可能已经走进
        水里、摔下平台或跑进战斗。脱困与滑索接近用的是 ``_hold_grid_keys``，它在
        ``finally`` 里自己放键，不受暂停影响。
        """
        if getattr(self, "_grid_nav_w_held", False):
            self._set_grid_walking(False)
        parent = getattr(super(), "pause", None)
        if callable(parent):
            parent()

    def load_grid_for_map(
        self,
        map_id: str = "",
        *,
        grid_path: str | None = None,
        grid_dir: str | None = None,
        zoom: str | None = None,
    ) -> DenseGrid | None:
        """为当前地图加载最匹配的 ``*.grid.npz``。

        显式 ``grid_path`` 优先；否则扫描目录并按地图、zoom 和文件元数据筛选。没有
        ``map_id`` 时只接受唯一候选。多个候选无法判定时应返回 ``None`` 并提示用户，
        不能静默挑一张可能错误的地图。
        """
        explicit = self._grid_config_get(CONFIG_GRID_FILE, "") if grid_path is None else grid_path
        explicit = str(explicit or "").strip()
        if explicit:
            path = Path(explicit)
            if not path.is_file():
                self.log_warning(f"导航网格文件不存在: {path}", notify=True)
                return None
            try:
                grid = load_grid(path)
            except (OSError, TypeError, ValueError) as exc:
                self.log_warning(f"导航网格加载失败 {path}: {exc}", notify=True)
                return None
            return grid

        default_dir = DEFAULT_GRID_NAV_CONFIG[CONFIG_GRID_DIR]
        directory = str(self._grid_config_get(CONFIG_GRID_DIR, default_dir) if grid_dir is None else grid_dir).strip()
        requested_zoom = str(self._grid_config_get(CONFIG_GRID_ZOOM, "") if zoom is None else zoom).strip()
        root = Path(directory)
        if not root.is_dir():
            self.log_warning(f"导航网格目录不存在: {root}", notify=True)
            return None

        map_id = str(map_id or "").strip()
        # glob 的 ``{map_id}_*`` 会**同时匹配分层文件** ``{map_id}__{tier}_{zoom}``：它们的
        # ``meta.map_name`` 就是 ``map_id``，光靠 meta 校验拦不住。所以这里先按文件名排除
        # 含 ``__`` 的——与编辑器的 ``_tier_grid_meta``、以及本模块的分层发现同一条口径。
        #
        # 不排除的后果不是"选错就走不动"那么轻：分层网格是一个小矩形（实测 tips_7 只有
        # 94x194），一旦被当主图选中，规划器会拿它当整张地图——起终点多半落在"界外"，
        # 表现为莫名其妙的"找不到通路"，而且日志里显示的是加载成功。
        candidates = (
            sorted(path for path in root.glob(f"{map_id}_*{GRID_SUFFIX}") if "__" not in path.name) if map_id else []
        )
        if not candidates:
            candidates = sorted(path for path in root.glob(f"*{GRID_SUFFIX}") if "__" not in path.name)

        matches: list[tuple[DenseGrid, Path]] = []
        for path in candidates:
            try:
                grid = load_grid(path)
            except (OSError, TypeError, ValueError) as exc:
                self.log_warning(f"忽略无法读取的导航网格 {path}: {exc}")
                continue
            if grid.meta.tier:
                # 双保险：文件名没带 ``__`` 但 meta 声明了 tier 的（别的工具写出的），同样不是主图
                continue
            if map_id and not self._grid_meta_matches_map(grid, path, map_id):
                continue
            if requested_zoom and str(grid.meta.zoom) != requested_zoom:
                continue
            matches.append((grid, path))

        if not matches:
            detail = f"map_id={map_id!r}" if map_id else "未提供 map_id"
            zoom_text = f"，zoom={requested_zoom!r}" if requested_zoom else ""
            self.log_warning(f"未找到匹配的导航网格（{detail}{zoom_text}）: {root}", notify=True)
            return None
        if not map_id and len(matches) > 1:
            self.log_warning(
                f"网格目录中存在 {len(matches)} 个候选网格，但当前没有 map_id；"
                f"请填写地图 id 或直接指定{CONFIG_GRID_FILE}",
                notify=True,
            )
            return None

        matches.sort(key=lambda item: self._grid_zoom_rank(item[0]), reverse=True)
        grid, path = matches[0]
        self.log_info(f"已加载导航网格 {path}：{grid.counts()}，cell_size={grid.meta.cell_size}")
        return grid

    def plan_grid_path(
        self,
        start_xz: tuple[float, float],
        goal_xz: tuple[float, float],
        *,
        map_id: str = "",
        grid_path: str | None = None,
        grid_dir: str | None = None,
        zoom: str | None = None,
    ) -> PlanResult:
        """只规划世界坐标路线，不执行任何游戏输入。"""
        self._refresh_grid_zip_lines()
        follower, result = self._create_grid_route(
            start_xz,
            goal_xz,
            map_id=map_id,
            grid_path=grid_path,
            grid_dir=grid_dir,
            zoom=zoom,
        )
        if follower is None:
            return result
        self._grid_nav_follower = follower
        if result.ok:
            self._log_grid_plan(result)
        return result

    def navigate_grid_to(
        self,
        goal_xz: tuple[float, float],
        *,
        timeout: float | None = None,
        map_id: str = "",
        grid_path: str | None = None,
        grid_dir: str | None = None,
        zoom: str | None = None,
        stop_distance: float | None = None,
        goal_y: float | None = None,
    ) -> bool:
        """规划并执行到世界坐标 ``(x, z)`` 的路线；``goal_y`` 给定时按三维目标导航。

        ``goal_y`` 是**分层导航的必需输入**：目标在第几层由它的高度决定（只看 Y，不看
        ``levelId``）。不给就按大地图处理——单层地图本来就只需要平面坐标，行为不变。

        每一拍只做三件事：

        1. :meth:`_grid_nav_poll` 取这一拍的输入快照（帧、融合定位、朝向质量）；
        2. :meth:`_grid_nav_advance` 判定状态并执行它 —— 先过定位就绪闸门，再规划，
           最后把 ``GridRouteFollower`` 的动作映射成状态；
        3. 处理器返回 ``True``/``False`` 就结束本次导航，返回 ``None`` 就进入下一拍。

        状态表见 :class:`src.nav.nav_state.NavState`，状态到处理器的映射见
        :data:`_GRID_NAV_HANDLERS`；每拍的会话状态收在
        :class:`src.nav.nav_state.NavSession` 里，不再散成局部变量。

        只有融合定位进入最终 ``goal_radius``（或 ``stop_distance``）才返回 True。
        任何退出路径都会松开 ``W`` 并恢复滑索运行模式。
        """
        request = GridRouteRequest(
            goal=(float(goal_xz[0]), float(goal_xz[1])),
            goal_y=None if goal_y is None else float(goal_y),
            map_id=str(map_id or ""),
            grid_path=grid_path,
            grid_dir=grid_dir,
            zoom=zoom,
            stop_distance=None if stop_distance is None else float(stop_distance),
        )
        limit = self._grid_cfg_float(CONFIG_GRID_TIMEOUT, 180.0) if timeout is None else float(timeout)
        limit = max(1.0, limit)
        tick = max(0.05, self._grid_cfg_float(CONFIG_GRID_TICK, 0.2))
        position_service = self.ensure_runtime_position_service(force_start=True)
        if position_service is None:
            return False
        # 抓包直通时先确认那一局的握手真的抓到了。导航是被"位置永远不来"坑得最直接的地方，
        # 而重建连接（回登录界面再进）是个会打断玩家的动作，所以只在确实需要时做，见
        # MinimapPositionMixin.ensure_capture_session。非抓包源没有这个方法，鸭子类型跳过。
        capture_check = getattr(position_service, "ensure_capture_session", None)
        if callable(capture_check):
            try:
                capture_check(reason="导航前检查抓包")
            except Exception as exc:  # 校验失败不该挡住导航本身
                self.log_warning(f"检查抓包会话失败，继续导航: {exc}")
        self._refresh_grid_zip_lines()
        session = NavSession(
            request=request,
            deadline=self.active_time() + limit,
            limit=limit,
            tick=tick,
        )
        self._reset_grid_navigation_run_state()

        try:
            while not self._grid_navigation_timed_out(session.deadline, session.limit):
                ctx = self._grid_nav_poll(session)
                outcome = self._grid_nav_advance(ctx)
                if outcome is not None:
                    return outcome
            return False
        finally:
            self._set_grid_walking(False)
            self._restore_grid_zip_line_run_mode()

    # ------------------------------------------------------------------ #
    # 状态机：取快照、判定、派发
    # ------------------------------------------------------------------ #
    def _grid_nav_poll(self, session: NavSession) -> NavTick:
        """取这一拍的输入快照：帧、定位、朝向，以及重锚带来的历史格失效。

        顺序与旧主循环一致：定位缺失时不记调试日志、也不读坐标；有定位时先记调试
        日志，再按校准序号决定要不要清空走过的已知格。朝向质量阈值留到
        :meth:`_grid_nav_classify` 里判，因为这属于"能不能走"的闸门。
        """
        frame = self.next_frame()
        pose = self.world_pose(
            frame=frame,
            max_age=self._grid_position_max_age(session.tick),
        )
        if pose is None:
            return NavTick(
                session=session,
                frame=frame,
                pose=None,
                x=None,
                z=None,
                map_id=session.request.map_id,
                sync_seq=session.last_sync_seq,
                heading=None,
                heading_score=None,
            )
        map_id = str(pose.get("map_id") or session.request.map_id or "")
        self._log_grid_navigation_debug(pose, map_id=map_id, tag="导航")
        sync_seq = int(pose.get("sync_seq") or 0)
        session.note_sync(sync_seq)
        return NavTick(
            session=session,
            frame=frame,
            pose=pose,
            x=pose.get("x"),
            z=pose.get("z"),
            map_id=map_id,
            sync_seq=sync_seq,
            heading=pose.get("heading"),
            heading_score=pose.get("heading_score"),
        )

    def _grid_nav_advance(self, ctx: NavTick) -> bool | None:
        """判定并执行这一拍：定位闸门优先，其次规划，最后路线跟随。

        返回 ``True``/``False`` 表示本次导航结束，``None`` 表示继续下一拍。
        """
        gate = self._grid_nav_classify(ctx)
        if gate is not None:
            return self._grid_nav_state_call(gate, ctx)
        return self._grid_nav_follow(ctx)

    def _grid_nav_classify(self, ctx: NavTick) -> NavState | None:
        """定位就绪闸门：按固定优先级返回这一拍应当先处理的状态。

        返回 ``None`` 表示位置与朝向都可用，可以进入规划与跟随阶段。判定顺序与旧主
        循环的 if 链逐条对应：无定位 -> 未锚定 -> 不可信 -> 无位移样本 -> 需校准
        -> 朝向不可用 -> 已进入终点接近范围。
        """
        session = ctx.session
        if ctx.pose is None:
            return NavState.WAIT_POSE
        if ctx.x is None or ctx.z is None:
            return NavState.WAIT_ANCHOR
        if not ctx.pose.get("position_trusted", True):
            return NavState.WAIT_TRUST
        if not ctx.pose.get("odom_ok"):
            return NavState.WAIT_ODOM
        if ctx.pose.get("sync_needed") and self.active_time() >= session.next_sync_retry_at:
            return NavState.CALIBRATE
        ctx.min_score = self._grid_heading_min_score()
        if ctx.heading_score is None or float(ctx.heading_score) < ctx.min_score:
            ctx.heading = None
        if ctx.heading is None:
            return NavState.WAIT_HEADING
        if session.stop_distance is not None:
            distance_to_goal = math.hypot(
                float(ctx.x) - session.goal[0],
                float(ctx.z) - session.goal[1],
            )
            if distance_to_goal <= max(0.0, float(session.stop_distance)) and self._grid_nav_stop_layer_matches(ctx):
                return NavState.STOP_REACHED
        return None

    def _grid_nav_stop_layer_matches(self, ctx: NavTick) -> bool:
        """终点接近判定只在**已经在目标层**时成立。

        ``stop_distance`` 只比 XZ 距离：目标在楼上/楼下且恰好同一 XZ 时会提前报成功
        （网格送货会同时传 ``goal_y`` 和 5m ``stop_distance``，实测跨层同 XZ 很常见），
        于是角色还在另一层就直接进入交付搜索。这里复用与规划同一套「只看 Y」的层判定
        确认当前层与目标层一致；没有分层数据、没有 ``goal_y`` 或取不到当前高度时保持
        原来的平面判定。
        """
        session = ctx.session
        if session.goal_y is None:
            return True
        pose = ctx.pose or {}
        y = pose.get("y")
        if y is None or ctx.x is None or ctx.z is None:
            return True
        try:
            position = (float(ctx.x), float(y), float(ctx.z))
            goal = (float(session.goal[0]), float(session.goal_y), float(session.goal[1]))
        except (TypeError, ValueError):
            return True
        layered = self._grid_layered_map(
            ctx.map_id,
            grid_dir=session.request.grid_dir,
            zoom=session.request.zoom,
        )
        if layered is None:
            return True
        current_tier, _ = self._grid_tier_for_point(
            layered,
            ctx.map_id,
            position,
            label="当前位置",
        )
        goal_tier, _ = self._grid_tier_for_point(
            layered,
            ctx.map_id,
            goal,
            label="终点",
        )
        if current_tier is None or goal_tier is None:
            # 判不出层时不提前结束：宁可继续规划（会明确失败），也不赌一个"已经到了"。
            return False
        return current_tier == goal_tier

    def _grid_nav_follow(self, ctx: NavTick) -> bool | None:
        """跟随阶段：没有可用路线就先规划，否则取跟随器动作并派发。"""
        if self._grid_nav_follower is None or ctx.map_id != self._grid_nav_map_id:
            return self._grid_nav_state_call(NavState.PLAN, ctx)
        if self._grid_nav_segment_tier_changed(ctx):
            return self._grid_nav_state_call(NavState.PLAN, ctx)
        return self._grid_nav_run_follow(ctx)

    def _grid_nav_segment_tier_changed(self, ctx: NavTick) -> bool:
        """当前高度落在**另一个确定的层**上时返回 True，用于中途换层后重规划。

        为什么需要它：层判定只在规划时算一次。玩家在路线中间自己走进另一层（下楼梯、
        坐电梯、被传送）之后，跟随器仍拿着旧那层的网格走，位置对不上会一路判偏航/卡住。
        对方在前端也踩到同一类问题——按"同一个 levelId 就不重算"去重会把换层整条丢掉。

        **只在判到唯一确定、且与当前分段不同的层时才触发**：``floor_regions`` 现在很稀疏
        （整张 map02 只有 2 段各 39 格），绝大多数位置会落到 ``main`` 兜底；拿兜底结果去
        判定"换层了"会每拍重规划。所以这里要求 ``status == "unique"``。
        """
        session = ctx.session
        route = session.route
        if route is None or not route.segments:
            return False
        layered = self._grid_nav_layered_active
        if layered is None:
            return False
        pose = ctx.pose or {}
        y = pose.get("y")
        if y is None or ctx.x is None or ctx.z is None:
            return False
        try:
            y_value = float(y)
        except (TypeError, ValueError):
            return False
        index = min(max(0, session.segment_index), len(route.segments) - 1)
        expected = route.segments[index].tier
        result = resolve_tier_by_height(
            layered,
            map_name=str(ctx.map_id or "").strip(),
            x=float(ctx.x),
            y=y_value,
            z=float(ctx.z),
        )
        if result["status"] != "unique" or result["tier"] == expected:
            return False
        self.log_info(
            f"分层导航：当前位置已不在本段的层（{expected or '大地图'} -> "
            f"{result['tier'] or '大地图'}，Y={y_value:.1f}），重新规划"
        )
        return True

    def _grid_nav_run_follow(self, ctx: NavTick) -> bool | None:
        """记录走过的已知格、取跟随器动作，然后派发到对应状态。

        旧主循环在这一步之后先处理滑索接近，再按动作分支；这里保留同样的顺序。
        """
        session = ctx.session
        self._record_grid_visited_cell(
            self._grid_nav_follower,
            ctx.position,
            session.visited_cells,
        )
        ctx.step = self._grid_nav_follower.update(
            ctx.position,
            float(ctx.heading),
            self.active_time(),
        )
        if ctx.step.skipped_waypoints:
            skipped = ", ".join(str(index + 1) for index in ctx.step.skipped_waypoints)
            self.log_info(
                f"路径点捷径：当前位置距下一段 {ctx.step.shortcut_distance:.2f}m，"
                f"跳过航点 {skipped}，直接前往航点 {ctx.step.waypoint_index + 1}"
            )
        ctx.pending_zip_step = self._grid_nav_pending_zip_step()
        if ctx.pending_zip_step is not None:
            return self._grid_nav_state_call(NavState.ZIP_LINE_APPROACH, ctx)
        return self._grid_nav_run_action(ctx)

    def _grid_nav_run_action(self, ctx: NavTick) -> bool | None:
        """普通动作派发：把跟随器动作映射成状态，先记进展再执行。"""
        state = self._grid_nav_action_state(ctx)
        if state in _GRID_NAV_TAIL_STATES:
            self._grid_nav_note_step(ctx)
        return self._grid_nav_state_call(state, ctx)

    def _grid_nav_action_state(self, ctx: NavTick) -> NavState:
        """动作 → 状态的优先级映射，顺序与旧主循环的 if 链一致。"""
        step = ctx.step
        if step.action == REPLAN:
            return NavState.REPLAN
        if step.action == ZIP_LINE:
            return NavState.ZIP_LINE
        if step.arrived_waypoint_index is not None:
            return NavState.WAYPOINT_ARRIVED
        return _GRID_NAV_ACTION_STATES.get(step.action, NavState.FAILED)

    def _grid_nav_state_call(self, state: NavState, ctx: NavTick) -> bool | None:
        """执行状态处理器；返回值直接作为主循环的终止结果。"""
        return getattr(self, _GRID_NAV_HANDLERS[state])(ctx)

    def _grid_nav_sync(self, ctx: NavTick, reason: SyncReason) -> SyncWait:
        """停车等一次静止校准，并判定本次导航是否已经超时。

        六个状态、七个调用点原来各自重复同样三件事：传同一组参数调
        :meth:`_wait_for_minimap_sync`、检查导航超时、再决定失败退避。集中到这里后
        调用点只关心 ``wait.synced`` 与 ``wait.timed_out``，"为什么校准"由 ``reason``
        一路带进状态提示和超时告警。

        数据源在 ``pose["sync_supported"]`` 上声明"静止校准这个概念不成立"时，直接当"已对齐"
        返回：那种源（目前的抓包直通）给的是绝对坐标，既没有可设的锚点、也没有可修的漂移，
        停车等静止校准只会白等。在这里拦一次就够了，八个调用点不必各自判断；整体超时仍然
        照常上报，导航该放弃时照样放弃。

        **判据是契约字段而不是 ``source`` 名称**：导航层依赖的是 pose 的接口，不该知道
        "capture" 是哪个定位实现的内部命名（``tests/TestRuntimeStateArchitecture`` 钉着这条）。
        字段缺省为 True，所以老的定位源行为不变。
        """

        if not (ctx.pose or {}).get("sync_supported", True):
            return SyncWait(
                synced=True,
                timed_out=self._grid_navigation_timed_out(ctx.deadline, ctx.limit),
            )
        synced = self._wait_for_minimap_sync(
            ctx.sync_seq,
            ctx.deadline,
            ctx.tick,
            reason=reason.label,
        )
        return SyncWait(
            synced=synced,
            timed_out=self._grid_navigation_timed_out(ctx.deadline, ctx.limit),
        )

    def _grid_nav_pending_zip_step(self):
        """跟随器当前待执行的滑索段；跟随器没有这个能力时返回 None。"""
        current_step_getter = getattr(
            self._grid_nav_follower,
            "current_zip_line_step",
            None,
        )
        return current_step_getter() if callable(current_step_getter) else None

    def _grid_nav_note_step(self, ctx: NavTick) -> None:
        """记录目标距离刷新的进展，并按 5 秒节流更新状态提示。"""
        session = ctx.session
        step = ctx.step
        if step.distance_to_goal is None:
            return
        session.note_progress(step.distance_to_goal)
        info_key = (
            step.action,
            step.waypoint_index,
            round(step.distance_to_goal, 0),
        )
        now = self.active_time()
        if info_key != self._grid_nav_last_info_key or now - self._grid_nav_last_info_at >= 5.0:
            self.info_set(
                "网格导航",
                f"{step.action} | 目标距离={step.distance_to_goal:.2f}m | "
                f"航点={step.waypoint_index + 1}/{step.waypoint_count}",
            )
            self._grid_nav_last_info_key = info_key
            self._grid_nav_last_info_at = now

    # ------------------------------------------------------------------ #
    # 状态处理器：定位就绪闸门
    # ------------------------------------------------------------------ #
    def _grid_nav_state_wait_pose(self, ctx: NavTick) -> bool | None:
        """还没有运行时定位状态：停车等下一拍。"""
        self._wait_for_grid_position("等待运行时定位状态")
        self.sleep(ctx.tick)
        return None

    def _grid_nav_state_wait_anchor(self, ctx: NavTick) -> bool | None:
        """还没有可用的平面坐标：停车等下一拍。

        等待原因取 pose 里的 ``trust_reason`` / ``odom_reason``，而不是写死一句"融合坐标尚未
        锚定"：抓包直通模式下没有"融合"可言，真正的原因是 ``capture_unavailable``（后端连不上
        或还没解出坐标），而这两种情况该做的事完全不同——一个去查全局导航配置和游戏窗口，另一个
        去查抓包后端。
        """

        self._wait_for_grid_position(self._grid_wait_reason(ctx.pose, "等待坐标"))
        self.sleep(ctx.tick)
        return None

    @staticmethod
    def _grid_wait_reason(pose: dict | None, prefix: str) -> str:
        """把 pose 里的原因拼进等待文案；没有原因时只用前缀。"""

        source = pose or {}
        reason = str(source.get("trust_reason") or source.get("odom_reason") or "").strip()
        return f"{prefix}: {reason}" if reason else prefix

    def _grid_nav_state_wait_trust(self, ctx: NavTick) -> bool | None:
        """重锚后绝对坐标不可信：停车，并在静止时请求一次静态校准。

        里程计"有位移样本"不等于绝对坐标可信。必须先停车等 WS 静校准恢复
        ``position_trusted``，再允许重新规划；校准失败退避 30 秒。
        """
        session = ctx.session
        self._wait_for_grid_position(f"等待定位重新校准: {ctx.pose.get('trust_reason') or 'untrusted'}")
        if ctx.pose.get("rest") and self.active_time() >= session.next_sync_retry_at:
            wait = self._grid_nav_sync(ctx, SyncReason.TRUST)
            if wait.timed_out:
                return False
            if not wait.synced:
                session.next_sync_retry_at = self.active_time() + 30.0
        self.sleep(ctx.tick)
        return None

    def _grid_nav_state_wait_odom(self, ctx: NavTick) -> bool | None:
        """这一拍没有有效位移样本：停车等下一拍。"""
        self._wait_for_grid_position(f"等待有效位移样本: {ctx.pose.get('odom_reason')}")
        self.sleep(ctx.tick)
        return None

    def _grid_nav_state_calibrate(self, ctx: NavTick) -> bool | None:
        """定位服务请求静态校准：停车等一次真实重锚。

        这是主动停车点；校准失败同样退避 30 秒，避免每拍都请求一次。
        """
        session = ctx.session
        wait = self._grid_nav_sync(ctx, SyncReason.SYNC_NEEDED)
        if wait.timed_out:
            return False
        if wait.synced:
            session.waypoints_since_calibration = 0
        if not wait.synced:
            session.next_sync_retry_at = self.active_time() + 30.0
        return None

    def _grid_nav_state_wait_heading(self, ctx: NavTick) -> bool | None:
        """朝向不可用或质量不足：停车等下一拍。"""
        self._wait_for_grid_position(f"等待可靠朝向: score={ctx.heading_score}")
        self.sleep(ctx.tick)
        return None

    def _grid_nav_state_stop_reached(self, ctx: NavTick) -> bool | None:
        """已进入终点接近范围：停车并按成功结束本次导航。"""
        session = ctx.session
        distance_to_goal = math.hypot(
            float(ctx.x) - session.goal[0],
            float(ctx.z) - session.goal[1],
        )
        self._set_grid_walking(False)
        self.log_info(f"进入终点接近范围（距目标 {distance_to_goal:.2f}m），停止网格导航")
        return True

    # ------------------------------------------------------------------ #
    # 状态处理器：路线与执行
    # ------------------------------------------------------------------ #
    def _grid_nav_state_plan(self, ctx: NavTick) -> bool | None:
        """规划路线（首次、换图或偏航后重规划），成功后同一拍继续跟随。"""
        session = ctx.session
        map_changed = self._grid_nav_follower is not None and ctx.map_id != self._grid_nav_map_id
        if session.had_plan:
            if session.replans >= self._grid_cfg_int(CONFIG_GRID_MAX_REPLANS, 8):
                self.log_warning("导航重规划次数已达上限", notify=True)
                return False
            session.replans += 1
        if map_changed:
            self.log_info(f"地图已切换 {self._grid_nav_map_id!r} -> {ctx.map_id!r}，重新规划")
            session.visited_cells.clear()
            session.recovery_goal = None
        route_goal = session.recovery_goal if session.recovery_goal is not None else session.goal
        route_start = self._resolve_grid_replan_start(
            ctx.map_id,
            ctx.position,
        )
        session.route = None
        layered_taken, layered_ok = self._try_layered_route_on_plan(ctx, route_start, route_goal)
        if layered_taken:
            session.had_plan = True
            if not layered_ok:
                return False
            if self._grid_navigation_timed_out(session.deadline, session.limit):
                return False
            return self._grid_nav_run_follow(ctx)
        follower, result = self._create_grid_route(
            route_start,
            route_goal,
            map_id=ctx.map_id,
            grid_path=session.request.grid_path,
            grid_dir=session.request.grid_dir,
            zoom=session.request.zoom,
            time_budget_s=max(0.0, session.deadline - self.active_time()),
            required_zip_line_start_id=self._grid_nav_skip_board_node_id,
        )
        session.had_plan = True
        if self._grid_navigation_timed_out(session.deadline, session.limit):
            return False
        if follower is None or not result.ok:
            # 单层走不通：起终点都在大地图时分层被跳过了，但主图的可行走区可能被切成互不
            # 相通的块，而"进一层再绕出来"只有分层图能表达。再给分层一次机会，失败才报错。
            self.log_info("单层路线规划失败，改用分层绕行重试")
            layered_taken, layered_ok = self._try_layered_route_on_plan(
                ctx,
                route_start,
                route_goal,
                allow_same_main=True,
            )
            if layered_taken:
                if not layered_ok:
                    return False
                if self._grid_navigation_timed_out(session.deadline, session.limit):
                    return False
                return self._grid_nav_run_follow(ctx)
            self._log_grid_plan_failure(result, "导航规划失败")
            return False
        self._grid_nav_follower = follower
        # 拓扑记录：单层路径用的就是主图网格，落到大地图。
        session.active_tier = self._grid_follower_tier(follower)
        self._record_grid_visited_cell(
            follower,
            ctx.position,
            session.visited_cells,
        )
        self._grid_nav_map_id = ctx.map_id
        self._log_grid_plan(result)
        # 旧主循环规划成功后不换拍：同一拍接着取跟随器动作并派发。
        return self._grid_nav_run_follow(ctx)

    def _grid_nav_state_zip_line_approach(self, ctx: NavTick) -> bool | None:
        """走向滑索入口：先接近到入口再登索，然后交给滑索执行。

        与旧主循环一致：已经明确要从滑索架下索步行、动作不是行走/转向/等待/滑索、
        或还没接近到入口时，直接落回普通动作派发。
        """
        session = ctx.session
        step = ctx.step
        route_step = ctx.pending_zip_step
        zip_entry_id = str(route_step.step.entry.node_id)
        zip_entry_distance = self._grid_zip_line_entry_distance(
            route_step,
            ctx.position,
        )
        approach_active = self._grid_nav_zip_line_approach_entry_id == zip_entry_id
        skip_board = self._grid_nav_skip_board_node_id == zip_entry_id
        if approach_active and zip_entry_distance > GRID_ZIP_LINE_APPROACH_DISTANCE_M:
            self._grid_nav_zip_line_approach_entry_id = None
            self._grid_nav_zip_line_approach_started_at = None
            approach_active = False
        if (
            not skip_board
            and step.action in (WALK, TURN, WAIT, ZIP_LINE)
            and (zip_entry_distance <= GRID_ZIP_LINE_APPROACH_DISTANCE_M or approach_active)
        ):
            approach_result = self._approach_grid_zip_line_for_boarding(
                route_step=route_step,
                frame=ctx.frame,
                min_score=ctx.min_score,
                deadline=session.deadline,
                tick=session.tick,
            )
            if approach_result is True:
                self._set_grid_walking(False)
                zip_result = self._handle_grid_zip_line_route_step(
                    route_step,
                    already_on_rack=True,
                )
                if zip_result == "failed":
                    return False
                if zip_result == "replan":
                    session.recovery_goal = None
                else:
                    session.note_waypoint_arrival()
                if self._grid_navigation_timed_out(session.deadline, session.limit):
                    return False
                return None
            if approach_result is False:
                return False
            self.sleep(session.tick)
            return None
        return self._grid_nav_run_action(ctx)

    def _grid_nav_state_waypoint_arrived(self, ctx: NavTick) -> bool | None:
        """到达一个航点：累计未校准航点数，必要时主动停车校准。"""
        session = ctx.session
        step = ctx.step
        arrived = session.note_waypoint_arrival()
        calibration_interval = max(
            0,
            self._grid_cfg_int(CONFIG_GRID_CALIBRATION_WAYPOINTS, 5),
        )
        if calibration_interval > 0 and arrived >= calibration_interval:
            # 航点间隔校准是主动停车点；WS 周期约 5 秒，等待函数会在
            # 停车期间持续采样，直到完成真实重锚或确认已对齐。
            wait = self._grid_nav_sync(ctx, SyncReason.WAYPOINT_INTERVAL)
            if wait.timed_out:
                return False
            session.waypoints_since_calibration = 0
            if not wait.synced:
                self.log_warning("到达航点后的静止校准未完成，继续导航")
        self.log_info(
            f"到达航点 {step.arrived_waypoint_index + 1}/"
            f"{step.waypoint_count}，累计未校准航点={session.waypoints_since_calibration}"
        )
        return None

    def _grid_nav_state_replan(self, ctx: NavTick) -> bool | None:
        """偏航或路径失效：停车，按需校准或回退，然后重新规划。"""
        session = ctx.session
        step = ctx.step
        self._set_grid_walking(False)
        self._grid_nav_follower.pause()
        self.log_warning(f"路径偏离，重新规划：{step.reason}")
        current_known = self._grid_position_is_known(
            self._grid_nav_follower,
            ctx.position,
        )
        recovery_target = None
        if not current_known:
            recovery_target = self._nearest_visited_grid_cell(
                ctx.position,
                session.visited_cells,
            )
        self._grid_nav_follower = None
        if current_known:
            # 人仍站在已知 free 格：优先原地静止校准，消除定位偏差后
            # 再从可信位置重新规划。
            session.recovery_goal = None
            wait = self._grid_nav_sync(ctx, SyncReason.OFF_ROUTE)
            if wait.timed_out:
                return False
            if not wait.synced:
                self.log_warning("偏航后的静止校准未完成，继续重规划")
        elif recovery_target is not None:
            # 已偏离已知区域：先回到本次实际走过的格，不能直接相信
            # 当前坐标去规划一条可能穿墙的新路线。
            session.recovery_goal = recovery_target
            self.log_info("当前位置不在已知 free 格，先导航回最近走过的已知格再校准")
        else:
            session.recovery_goal = None
            self.log_warning("当前位置不在已知 free 格，但没有已走过的已知格可返回；先原地校准后重新规划")
            wait = self._grid_nav_sync(ctx, SyncReason.OFF_ROUTE)
            if wait.timed_out:
                return False
            if not wait.synced:
                self.log_warning("偏航后的静止校准未完成，继续重规划")
        if self._grid_navigation_timed_out(session.deadline, session.limit):
            return False
        return None

    def _grid_nav_state_zip_line(self, ctx: NavTick) -> bool | None:
        """执行规划出的滑索段。"""
        session = ctx.session
        step = ctx.step
        self._set_grid_walking(False)
        route_entry_id = step.zip_line_step.step.entry.node_id
        skip_board = self._grid_nav_skip_board_node_id == route_entry_id
        self._grid_nav_skip_board_node_id = None
        zip_result = self._handle_grid_zip_line_route_step(
            step.zip_line_step,
            already_on_rack=skip_board,
        )
        if zip_result == "failed":
            return False
        if zip_result == "replan":
            session.recovery_goal = None
        else:
            session.note_waypoint_arrival()
        if self._grid_navigation_timed_out(session.deadline, session.limit):
            return False
        return None

    def _grid_nav_state_done(self, ctx: NavTick) -> bool | None:
        """跟随器报告到达：还在回退目标上就先校准，否则成功结束。"""
        session = ctx.session
        step = ctx.step
        self._set_grid_walking(False)
        if session.layered:
            route = session.route
            index = session.segment_index
            has_next_segment = index + 1 < len(route.segments)
            trailing = route.transitions[index] if 0 <= index < len(route.transitions) else ()
            if has_next_segment or trailing:
                # 这一段走完了，但整条分层路线还没完：跨过连接、切到下一段。
                # 末尾那段之后仍可能挂着连接——终点恰好压在连接落点上时，规划器会丢掉长度
                # 为 0 的收尾步行段，把连接留在最后一个槽位。那条连接同样必须执行，否则会
                # 把"还要跳一层"直接报成已经到达。
                return self._advance_layered_segment(ctx)
        if session.recovery_goal is not None:
            wait = self._grid_nav_sync(ctx, SyncReason.BACKTRACK_DONE)
            if wait.timed_out:
                return False
            session.recovery_goal = None
            self._grid_nav_follower = None
            if wait.synced:
                self.log_info("已返回最近走过的已知格并完成校准，继续原目标")
            else:
                self.log_warning("返回已知格后校准未完成，继续原目标")
            return None
        distance_text = f"{step.distance_to_goal:.2f}m" if step.distance_to_goal is not None else "未知"
        self.log_info(f"已到达目标（距目标 {distance_text}）", notify=True)
        return True

    def _grid_nav_state_stuck(self, ctx: NavTick) -> bool | None:
        """卡住：按后退、侧移、对准后跳跃的顺序脱困，用完额度就放弃。"""
        session = ctx.session
        step = ctx.step
        self._set_grid_walking(False)
        max_recoveries = self._grid_cfg_int(CONFIG_GRID_MAX_RECOVERIES, 3)
        if session.recoveries >= max_recoveries:
            self.log_warning(f"导航卡住: {step.reason}", notify=True)
            return False
        session.recoveries += 1
        self.log_warning(f"导航卡住，尝试脱困 {session.recoveries}/{max_recoveries}：{step.reason}")
        self._recover_grid_stuck(
            position=ctx.position,
            heading=float(ctx.heading),
            target_bearing=step.target_bearing,
            frame=ctx.frame,
            attempt=session.recoveries,
            deadline=session.deadline,
            max_attempts=max_recoveries,
        )
        if self._grid_navigation_timed_out(session.deadline, session.limit):
            return False
        self._grid_nav_follower = None
        return None

    def _grid_nav_state_narrow_calibrate(self, ctx: NavTick) -> bool | None:
        """窄道入口：先停车做一次静止校准，再对准入口航点。

        一格宽通路没有横向余量，带着累积定位误差进去会蹭墙。校准或对准任一没成功都
        不标记已校准，下一拍重新走完整个流程；连续失败到上限才放弃，防止卡死。
        """
        step = ctx.step
        self._set_grid_walking(False)
        self.info_set("网格导航", "窄道入口停车校准")
        wait = self._grid_nav_sync(ctx, SyncReason.NARROW_ENTRY)
        if wait.timed_out:
            return False
        aligned_ok = True
        entry_bearing = step.target_bearing
        if entry_bearing is not None:
            aligned = self.pose_turn_to_bearing(
                entry_bearing,
                tolerance=self._grid_turn_tolerance(),
                max_rounds=max(1, self._grid_cfg_int(CONFIG_GRID_MAX_TURN_ROUNDS, 2)),
                frame=ctx.frame,
                min_score=ctx.min_score,
            )
            aligned_ok = bool(aligned.get("ok"))
            if not aligned_ok:
                self.log_warning(
                    f"窄道入口对准未到位：目标={entry_bearing:.1f}°，"
                    f"实测={aligned.get('heading')}，误差={aligned.get('error')}"
                )
        follower = self._grid_nav_follower
        if wait.synced and aligned_ok:
            if follower is not None:
                follower.confirm_narrow_calibration()
            self.log_info("窄道入口前已完成静止校准并对准航点")
            return None
        attempts = follower.narrow_calibration_failed() if follower is not None else 0
        if attempts >= GRID_NARROW_CALIBRATION_MAX_ATTEMPTS:
            if follower is not None:
                follower.confirm_narrow_calibration()
            self.log_warning(
                f"窄道入口校准/对准连续 {attempts} 次未成功，按当前状态继续",
                notify=True,
            )
        else:
            reason = "静止校准未完成" if not wait.synced else "对准未到位"
            self.log_warning(f"窄道入口{reason}，将重新校准（第 {attempts} 次）")
        return None

    def _grid_nav_state_turn(self, ctx: NavTick) -> bool | None:
        """转向：大角度或接近航点时停车转，否则边走边转。"""
        session = ctx.session
        step = ctx.step
        if self._grid_cfg_bool(CONFIG_GRID_TURN_WHILE_MOVING, True) and not self._should_turn_grid_in_place(step):
            self._turn_grid_while_moving(step)
        else:
            self._set_grid_walking(False)
            turn = self.pose_turn_to_bearing(
                step.target_bearing,
                tolerance=self._grid_turn_tolerance(),
                max_rounds=max(1, self._grid_cfg_int(CONFIG_GRID_MAX_TURN_ROUNDS, 2)),
                frame=ctx.frame,
                min_score=ctx.min_score,
            )
            if not turn.get("ok"):
                self.log_warning(
                    f"转向未到位：目标={step.target_bearing:.1f}°，实测={turn.get('heading')}，误差={turn.get('error')}"
                )
        if self._grid_navigation_timed_out(session.deadline, session.limit):
            return False
        self.sleep(session.tick)
        return None

    def _grid_nav_state_walk(self, ctx: NavTick) -> bool | None:
        """按住 ``W`` 前进；长航段在按住 ``W`` 时顺带按一次冲刺。"""
        session = ctx.session
        if self._grid_nav_skip_board_node_id is not None:
            # A* 已选择从当前滑索架下索步行；若之后再次进入滑索，
            # 必须重新执行登索，不能沿用本次“已在上索点”的状态。
            self._grid_nav_skip_board_node_id = None
            self.log_info("已从滑索架下索步行，后续滑索将重新登索")
        self._set_grid_walking(True)
        self._maybe_start_grid_sprint(ctx.step)
        self.sleep(session.tick)
        return None

    def _grid_nav_state_wait(self, ctx: NavTick) -> bool | None:
        """跟随器判定等待状态稳定：松开 ``W`` 等下一拍。"""
        self._set_grid_walking(False)
        self.sleep(ctx.tick)
        return None

    def _grid_nav_state_failed(self, ctx: NavTick) -> bool | None:
        """执行失败：松开 ``W`` 并按失败结束本次导航。"""
        self._set_grid_walking(False)
        self.log_warning(f"导航执行失败: {ctx.step.reason}", notify=True)
        return False

    @staticmethod
    def _nearest_grid_zip_line_node_info(
        zip_lines: ZipLineGraph | None,
        position: tuple[float, float] | None,
        *,
        max_distance: float,
    ) -> ZipLineNode | None:
        """返回距离位置最近的滑索节点；超出范围或节点图不可用时返回 ``None``。"""
        if zip_lines is None or position is None or not zip_lines.nodes:
            return None
        use_xyz = len(position) >= 3

        def distance(node: ZipLineNode) -> float:
            if use_xyz:
                return math.dist(node.xyz, tuple(float(value) for value in position[:3]))
            return math.hypot(
                float(node.x) - float(position[0]),
                float(node.z) - float(position[1]),
            )

        nearest = min(
            zip_lines.nodes,
            key=distance,
        )
        return nearest if distance(nearest) <= max(0.0, float(max_distance)) else None

    @classmethod
    def _nearest_grid_zip_line_node(
        cls,
        zip_lines: ZipLineGraph | None,
        position: tuple[float, float],
        *,
        max_distance: float,
    ) -> tuple[float, float] | None:
        """返回距离位置最近的滑索节点世界坐标。"""
        nearest = cls._nearest_grid_zip_line_node_info(
            zip_lines,
            position,
            max_distance=max_distance,
        )
        return None if nearest is None else (float(nearest.x), float(nearest.z))

    @classmethod
    def _nearest_grid_zip_line_node_id(
        cls,
        zip_lines: ZipLineGraph | None,
        position: tuple[float, float] | None,
        *,
        max_distance: float,
    ) -> str | None:
        """返回距离位置最近的滑索节点 ID。"""
        nearest = cls._nearest_grid_zip_line_node_info(
            zip_lines,
            position,
            max_distance=max_distance,
        )
        return None if nearest is None else str(nearest.node_id)

    @staticmethod
    def _zip_line_node_coordinate_key(node: ZipLineNode) -> tuple[float, float, float]:
        """将滑索节点转换为跨快照稳定的三维坐标键。"""
        return round(float(node.x), 2), round(float(node.y), 2), round(float(node.z), 2)

    @classmethod
    def _zip_line_connection_key(
        cls,
        map_id: str,
        first: ZipLineNode,
        second: ZipLineNode,
    ) -> tuple[str, tuple[float, float, float], tuple[float, float, float]]:
        """返回与节点哈希无关、按端点坐标排序的无向连接键。"""
        endpoints = sorted(
            (
                cls._zip_line_node_coordinate_key(first),
                cls._zip_line_node_coordinate_key(second),
            )
        )
        return str(map_id or first.map_id or second.map_id), endpoints[0], endpoints[1]

    def _resolve_grid_replan_start(
        self,
        map_id: str,
        fallback: tuple[float, float],
    ) -> tuple[float, float]:
        """把错误滑索落点映射到当前滑索节点；无提示时返回定位坐标。"""
        failed_source = self._grid_nav_zip_line_start_hint
        if failed_source is None:
            return fallback

        actual_position = failed_source
        xyz_getter = getattr(self, "_zip_line_ws_position_xyz", None)
        if callable(xyz_getter):
            latest_xyz = xyz_getter()
            if latest_xyz is not None:
                actual_position = latest_xyz
        if actual_position is failed_source:
            position_getter = getattr(self, "_zip_line_ws_position", None)
            if callable(position_getter):
                latest_position = position_getter()
                if latest_position is not None:
                    actual_position = latest_position

        self._block_grid_zip_link_from_positions(
            failed_source,
            self._grid_nav_zip_line_failed_target_hint,
            map_id,
        )

        node = self._nearest_grid_zip_line_node_info(
            self._grid_zip_lines_for_map(map_id),
            actual_position,
            max_distance=12.0,
        )
        if node is None:
            self.log_warning("错误落点附近未匹配到滑索节点，按当前坐标重新规划")
        else:
            self._grid_nav_skip_board_node_id = str(node.node_id)
            self.log_info(f"错误落点已匹配滑索架节点，按当前滑索重新规划：({node.x:.2f}, {node.z:.2f})")

        self._grid_nav_zip_line_start_hint = None
        self._grid_nav_zip_line_failed_target_hint = None
        return node.xz if node is not None else fallback

    def _block_grid_zip_link_from_exception(
        self,
        exc: ZipLineReplanRequired,
        map_id: str,
    ) -> str | None:
        """把失败尝试对应的滑索连接标记为不可用。"""
        return self._block_grid_zip_link_from_positions(
            exc.current_position,
            exc.failed_target_position,
            map_id,
        )

    def _block_grid_zip_link_from_positions(
        self,
        current_position: tuple[float, float] | None,
        failed_target_position: tuple[float, float] | None,
        map_id: str,
    ) -> str | None:
        """按实际 WS 坐标标记失败连接，并返回当前滑索节点 ID。"""
        if current_position is None:
            return None
        zip_lines = self._grid_zip_lines_for_map(map_id)
        start_node = self._nearest_grid_zip_line_node_info(
            zip_lines,
            current_position,
            max_distance=4.0,
        )
        if start_node is None:
            return None
        start_id = str(start_node.node_id)
        if failed_target_position is None:
            return start_id
        target_node = self._nearest_grid_zip_line_node_info(
            zip_lines,
            failed_target_position,
            max_distance=4.0,
        )
        if target_node is None:
            return start_id
        target_id = str(target_node.node_id)
        if start_id == target_id:
            return start_id
        blocked = self._zip_line_connection_key(map_id, start_node, target_node)
        self._grid_nav_blocked_zip_connections.add(blocked)
        self.log_warning(
            "滑索连接不可用："
            f"({start_node.x:.2f}, {start_node.y:.2f}, {start_node.z:.2f}) <-> "
            f"({target_node.x:.2f}, {target_node.y:.2f}, {target_node.z:.2f})，"
            "后续导航将绕开",
            notify=True,
        )
        return start_id

    def _filter_blocked_grid_zip_lines(
        self,
        zip_lines: ZipLineGraph | None,
        map_id: str = "",
    ) -> ZipLineGraph | None:
        """返回移除了当前会话已确认不可用连接的滑索图。"""
        if zip_lines is None or not self._grid_nav_blocked_zip_connections:
            return zip_lines
        links = [
            link
            for link in zip_lines.links
            if self._zip_line_connection_key(
                map_id,
                zip_lines.node(link.first_id),
                zip_lines.node(link.second_id),
            )
            not in self._grid_nav_blocked_zip_connections
        ]
        if len(links) == len(zip_lines.links):
            return zip_lines
        return ZipLineGraph(zip_lines.nodes, links)

    # ------------------------------------------------------------------ #
    # 分层导航：层判定、分层规划、按分段驱动
    # ------------------------------------------------------------------ #
    def _grid_layered_map(
        self,
        map_id: str,
        *,
        grid_dir: str | None = None,
        zoom: str | None = None,
    ) -> LayeredMap | None:
        """为当前地图装配分层地图；**没有分层数据时返回 None**（调用方走原来的单层路径）。

        只有满足"确实有分层"（存在带 ``tier`` 的网格或存在 ``links.json``）才返回，
        否则返回 None——单层地图（实测 base01/map01 就没有分层）的行为因此与分层功能
        上线前逐字一致。
        """
        directory = str(
            (
                self._grid_config_get(CONFIG_GRID_DIR, DEFAULT_GRID_NAV_CONFIG[CONFIG_GRID_DIR])
                if grid_dir is None
                else grid_dir
            )
            or ""
        ).strip()
        map_id = str(map_id or "").strip()
        if not directory or not map_id:
            return None
        requested_zoom = str((self._grid_config_get(CONFIG_GRID_ZOOM, "") if zoom is None else zoom) or "").strip()
        cache_key = (directory, map_id, requested_zoom)
        if cache_key in self._grid_nav_layered_cache:
            cached = self._grid_nav_layered_cache[cache_key]
            return cached or None
        try:
            layered = load_layered_map(directory, map_id, zoom=requested_zoom)
        except (OSError, TypeError, ValueError) as exc:
            self.log_warning(f"分层网格加载失败，按单层导航处理 {directory}: {exc}")
            self._grid_nav_layered_cache[cache_key] = False
            return None
        # 至少要有一层网格才谈得上分层导航：只有 links 没有网格时无从落脚。
        relevant = bool(layered.layers) and (any(tier for _map, tier in layered.layers) or bool(layered.links))
        self._grid_nav_layered_cache[cache_key] = layered if relevant else False
        if relevant:
            summary = layered.summary()
            self.log_info(
                f"分层地图就绪：层={len(layered.layers)} 连接={summary['links']} "
                f"目录={'有' if layered.catalog_loaded else '无'}"
            )
        return layered if relevant else None

    def _grid_tier_for_point(
        self,
        layered: LayeredMap,
        map_id: str,
        position: tuple[float, float, float] | None,
        *,
        label: str,
    ) -> tuple[str | None, str]:
        """把三维坐标解析到层；返回 ``(tier, 说明)``，``tier is None`` 表示**判不出来**。

        **只看 Y**：``levelId`` 来自 WS 消息，既可能缺失也可能与实际不符；``floor_regions``
        的 Y 区间是用户在编辑器里逐处标定并落到网格文件里的，可自证。

        命中多层（``ambiguous``）时**不挑**：那正是会把角色送错层的场景，返回 None 让调用方
        明确失败，而不是赌一把。
        """
        if position is None:
            # 调用方没给高度：这不是"坏输入"，是"没提供判据"。终点缺 y 时按大地图处理
            # （单层地图本来就只需要平面坐标）；起点缺 y 时下面会与终点的判定自然对齐。
            return "", f"{label}没有三维坐标，按大地图处理"
        result = resolve_tier_by_height(
            layered,
            map_name=str(map_id or "").strip(),
            x=position[0],
            y=position[1],
            z=position[2],
        )
        status = result["status"]
        if status == "ambiguous":
            return None, (
                f"{label}的 Y 同时落在多层的高度区间里（{result['matched_ids']}），无法唯一确定层级；"
                "请在编辑器里把这些层重叠区的高度区间分开"
            )
        if status == "invalid":
            # **不能兜底成大地图**：invalid 是"这条位置本身不可用"，不是"在地表"。
            # 当成大地图会基于坏输入给出一个看起来正常的结论。
            return None, f"{label}的位置不可用（{result['reason']}），无法判定层级"
        tier = result["tier"]
        shown = tier or "大地图"
        if result["reason"] == "no_region_covers":
            note = f"{label}没有落在任何已标定的高度区间，按大地图处理（Y={position[1]:.1f}）"
        elif result["reason"] == "no_layers":
            note = f"{label}所在地图还没标过高度区间，按大地图处理"
        else:
            note = f"{label}判定在 {shown}（Y={position[1]:.1f}）"
        return tier, note

    def _grid_start_tier(
        self,
        layered: LayeredMap,
        map_id: str,
        position: tuple[float, float, float] | None,
        xz: tuple[float, float],
        *,
        label: str,
        known_tier: str | None,
    ) -> tuple[str | None, str]:
        """规划**起点**落在哪一层；``None`` 表示判不出来。

        判据按"越可信越靠前"排：

        1. **高度判到唯一一层**：玩家自己换过层（下楼梯、被传送）时以 Y 为准；
        2. **当前正在使用的网格**（``known_tier``，见 :attr:`NavSession.active_tier`）：
           跨过连接点之后当前网格自然变成落点那一层，重规划就该继续用它；
        3. **X/Z 上唯一有可走格的层**：同一处只有它标了可走格（"a 处 x 层有 free 格而
           y 层没有，就认为人在 x 层"）。人在分层场景里时主图那一格通常是未知格，
           于是不会被误判成大地图；
        4. 都不成立 -> 大地图兜底（与只按高度判定时的老行为一致）。

        **为什么不能只按高度兜底**：``floor_regions`` 是稀疏逐处标注的，实测 map02 上
        绝大多数位置会落到"没有区间覆盖 -> 大地图"，而角色此刻正站在某一层的网格上。
        拿这个兜底去重规划，就会把一个还在层上的角色丢回主图——主图在那一格是未知格，
        规划直接失败。
        """
        verdict = ""
        ambiguous = ""
        if position is not None:
            result = resolve_tier_by_height(
                layered,
                map_name=str(map_id or "").strip(),
                x=position[0],
                y=position[1],
                z=position[2],
            )
            status = result["status"]
            if status == "unique":
                return result["tier"], f"{label}高度判定在 {result['tier'] or '大地图'}（Y={position[1]:.1f}）"
            if status == "invalid":
                # **不能兜底成大地图**：invalid 是"这条位置本身不可用"，不是"在地表"。
                return None, f"{label}的位置不可用（{result['reason']}），无法判定层级"
            if status == "ambiguous":
                # 高度区间互相重叠是**数据问题**：拓扑记录与 X/Z 还能救回来，救不回来就得
                # 明确失败——这正是会把角色送错层的场景，不能赌一个。
                ambiguous = (
                    f"{label}的 Y 同时落在多层的高度区间里（{result['matched_ids']}），"
                    "无法唯一确定层级；请在编辑器里把这些层重叠区的高度区间分开"
                )
                verdict = f"{label}的高度区间无法唯一确定层级"
            else:
                verdict = f"{label}没有落在任何已标定的高度区间"
        # 2) 当前正在使用的网格（拓扑记录）
        if known_tier is not None:
            grid = layered.grid(map_id, known_tier)
            if grid is not None:
                i, j = grid.index_of_world(xz[0], xz[1])
                if grid.in_bounds(i, j):
                    return known_tier, f"{label}沿用当前正在使用的网格（{known_tier or '大地图'}）"
        # 3) X/Z 上唯一有可走格的层
        candidates = self._grid_tiers_with_walkable_cell(layered, map_id, xz)
        if len(candidates) == 1:
            tier = candidates[0]
            return tier, f"{label}所在 X/Z 上只有 {tier or '大地图'} 标了可走格"
        if candidates:
            shown = [tier or "大地图" for tier in candidates]
            verdict = f"{verdict}，而 X/Z 上有多层标了可走格（{shown}）"
        if ambiguous:
            return None, ambiguous
        return "", f"{verdict}，按大地图处理"

    def _grid_tiers_with_walkable_cell(
        self,
        layered: LayeredMap,
        map_id: str,
        xz: tuple[float, float],
    ) -> tuple[str, ...]:
        """该 X/Z 上**哪些层的网格有可走格**（按层 id 排序，主图是空串）。"""
        name = str(map_id or "").strip()
        found: list[str] = []
        for (layer_map, tier), grid in layered.layers.items():
            if layer_map != name:
                continue
            i, j = grid.index_of_world(xz[0], xz[1])
            if grid.in_bounds(i, j) and grid.is_free(i, j):
                found.append(tier)
        return tuple(sorted(found))

    def _plan_layered_route(
        self,
        ctx: NavTick,
        layered: LayeredMap,
        *,
        start_xz: tuple[float, float],
        goal_xz: tuple[float, float],
        allow_same_main: bool = False,
    ) -> tuple[LayeredRoute | None, str]:
        """按分层地图规划；返回 ``(路线, 说明)``。路线为 None 时说明里是原因。

        ``allow_same_main``：起终点**都在大地图**时是否也走分层。默认不走——同层行为要
        与分层功能上线前逐字一致（含用户滑索的代价模型）。但主图的可行走区实测会被切成
        互不相通的块，"借一层绕过去"只有分层图能表达，所以单层规划失败时会带着它重试
        一次（见 :meth:`_grid_nav_state_plan`）。
        """
        session = ctx.session
        start_y = (ctx.pose or {}).get("y")
        start_pos = None
        if start_y is not None:
            try:
                start_pos = (float(start_xz[0]), float(start_y), float(start_xz[1]))
            except (TypeError, ValueError):
                start_pos = None
        start_tier, start_note = self._grid_start_tier(
            layered,
            ctx.map_id,
            start_pos,
            start_xz,
            label="起点",
            known_tier=session.active_tier,
        )
        if start_tier is None:
            return None, start_note
        goal_pos = None
        if session.goal_y is not None:
            try:
                goal_pos = (float(goal_xz[0]), float(session.goal_y), float(goal_xz[1]))
            except (TypeError, ValueError):
                goal_pos = None
        goal_tier, goal_note = self._grid_tier_for_point(layered, ctx.map_id, goal_pos, label="终点")
        if goal_tier is None:
            return None, goal_note
        session.start_tier = start_tier
        session.goal_tier = goal_tier
        self.log_info(f"分层导航：{start_note}；{goal_note}")
        # **双方都在大地图**时才交回原来的单层路径（含用户滑索）：那条路径加载的就是主图
        # 网格，同层行为因此与分层功能上线前逐字一致，也不会与滑索的代价模型互相干扰。
        # 非主图的同层（例如起终点都在 lv1）必须继续走分层：单层回退加载的主图网格会忽略
        # 本层墙体，给出一条真实执行时过不去的路线。
        #
        # 例外是 ``allow_same_main``：主图自己的可行走区会被切成互不相通的几块（实测
        # 674 格 / 4757 格），从其中一块到另一块只有"进一层再出来"这条路，而那是单层
        # 网格表达不了的。单层规划失败时调用方会带这个开关再来一次。
        if start_tier == goal_tier == "" and not allow_same_main:
            return None, ""
        start_grid = layered.layers.get(layer_key(ctx.map_id, start_tier))
        goal_grid = layered.layers.get(layer_key(ctx.map_id, goal_tier))
        if start_grid is None or goal_grid is None:
            return None, (f"起点或终点所在的层没有网格文件（{start_tier or '大地图'} / {goal_tier or '大地图'}）")
        planner = self._grid_nav_layered_planner(layered)
        route = planner.plan(
            (start_grid.index_of_world(*start_xz),),
            (goal_grid.index_of_world(*goal_xz),),
            start_tier=start_tier,
            goal_tier=goal_tier,
            time_budget_s=max(0.0, session.deadline - self.active_time()),
        )
        if not route.ok:
            return None, route.reason or "分层路线不可达"
        return route, ""

    def _grid_nav_layered_planner(self, layered: LayeredMap) -> LayeredPlanner:
        """按当前 ``Nav Config`` 装配分层规划器（带缓存，层内分段结果可复用）。

        每层的层内搜索都注入与路线跟随器**同一套策略参数**——少一项就会让分段代价与
        跟随器实际怎么走口径分叉，层图上的最短路也就不再是真实最优。
        """
        planner = self._grid_nav_layered_planner_cache.get(id(layered))
        if planner is not None:
            return planner
        config = self._grid_follower_config()
        planner = LayeredPlanner(
            layered,
            planner_factory=lambda grid: GridPlanner(
                grid,
                risk_cost=config.risk_cost,
                diagonal=True,
                allow_unknown=config.allow_unknown,
                margin=config.margin,
                wall_penalty=config.wall_penalty,
                frontier_margin=config.frontier_margin,
                frontier_penalty=config.frontier_penalty,
                waypoint_tolerance=config.waypoint_tolerance,
                max_expand=config.max_expand,
            ),
        )
        self._grid_nav_layered_planner_cache[id(layered)] = planner
        return planner

    @staticmethod
    def _grid_follower_tier(follower) -> str:
        """跟随器当前用的层名（``grid.meta.tier``）；取不到就按主图算。

        测试里的替身跟随器不一定带 ``grid``，所以这里不能直接访问属性。
        """
        grid = getattr(follower, "grid", None)
        meta = getattr(grid, "meta", None)
        return str(getattr(meta, "tier", "") or "")

    def _activate_layered_segment(self, ctx: NavTick, *, reason: str = "") -> bool:
        """把跟随器切到当前分段；返回是否装配成功。

        这是分层导航的关键简化：**换段 = 换跟随器**。每段自带一张层网格与一份已算好的
        路线（``GridRouteFollower.adopt``），所以 TURN/WALK/WAIT/STUCK 那一整套状态机一行
        都不用改——它们始终只面对"当前这一段"。
        """
        session = ctx.session
        route = session.route
        if route is None or not route.segments:
            return False
        index = min(max(0, session.segment_index), len(route.segments) - 1)
        segment = route.segments[index]
        layered = self._grid_nav_layered_active
        grid = layered.layers.get(segment.key) if layered is not None else None
        if grid is None:
            self.log_warning(f"分层分段的层没有网格：{segment.key}")
            return False
        follower = GridRouteFollower(
            grid,
            self._grid_follower_config(),
            zip_lines=self._grid_zip_lines_for_map(ctx.map_id),
        )
        waypoints = segment.plan.waypoints
        segment_goal = tuple(waypoints[-1]) if waypoints else ctx.position
        follower.adopt(segment.plan, goal=segment_goal, start=ctx.position)
        self._grid_nav_follower = follower
        self._grid_nav_map_id = ctx.map_id
        # 拓扑记录：跨过连接点、切到这一段之后，当前网格就是这一段的层。
        session.active_tier = segment.tier
        tail = f"（{reason}）" if reason else ""
        self.log_info(
            f"分层导航：第 {index + 1}/{len(route.segments)} 段，层={segment.tier or '大地图'}，"
            f"航点={len(waypoints)}，代价={segment.cost:.1f}{tail}"
        )
        return True

    def _advance_layered_segment(self, ctx: NavTick) -> bool | None:
        """一段走完：跨过连接、切到下一段。返回主循环的终止结果（``None`` = 继续）。"""
        session = ctx.session
        route = session.route
        index = session.segment_index
        # 用 transitions 而不是扁平的 links：两段之间可能夹着多条连接（跨多层不走路时），
        # 扁平表的长度与段数不对应，按段号取会取错。
        group = route.transitions[index] if 0 <= index < len(route.transitions) else ()
        for link in group:
            if self._cross_layered_link(ctx, link) is False:
                return False
        session.segment_index = index + 1
        if session.segment_index >= len(route.segments):
            # 这次跨层之后没有分段了：连接的落点就是终点（终点恰好压在连接端点上时会出现）。
            # 不特判的话会把最后一段重新激活一遍，导航永远走不完。
            self.log_info("分层导航：跨层落点即目标，本次路线完成", notify=True)
            return True
        kinds = "/".join(link.kind for link in group) or "连接"
        if not self._activate_layered_segment(ctx, reason=f"经 {kinds} 跨层"):
            return False
        if self._grid_navigation_timed_out(session.deadline, session.limit):
            return False
        return None

    def _cross_layered_link(self, ctx: NavTick, link: TierLink) -> bool | None:
        """执行一次跨层连接；``False`` 表示失败（调用方结束本次导航）。

        - ``stairs`` / ``elevator`` / ``portal`` / ``door`` / ``other``：**走过去**。两端平面
          距离通常只有一两格（实测相邻），换层由游戏里的楼梯/门完成，所以这里只切换分段，
          物理过渡交给下一段的行走。
        - ``rope``：**不是滑索**，两套动作没有共同点，见 :meth:`_cross_layered_rope`。

        **关于距离：连接端点里没有 Y，所以这里只能算水平距离。** ``links.json`` 的 ``rect``
        只有 ``x0/z0/x1/z1``（编辑器侧同样如此），端点的实际高度**不在数据里**；两层的高度
        关系只能从各自的 ``floor_regions`` 区间**推断**（例如 tips_7 的 y[249,251] 与 tips_6
        的 y[239,241] 推出落差 ∈ [8,12]m），而那是区间不是点。落差只用于判断绳索的上下行
        （见 :func:`_rope_goes_up`），不参与定价与对准。
        """
        if link.kind == "rope":
            return self._cross_layered_rope(link)
        # "走过去"成立的前提是**两端几乎在同一处**（只差一层）：本实现不搬动角色，
        # 跨完就直接在下一段的起点上开始走。实测数据里 7 条连接的两端水平距离都是
        # 1.0~2.5m（相邻），前提成立；但这是**数据模型的隐含约定**，不是我们能校验的
        # ——端点里没有 Y，也看不出"走多远换层"。远端一旦出现跨度很大的非绳索连接，
        # 这里会退化成"朝下一段起点直线走"（可能穿墙），所以显式告警而不是静默。
        span = math.hypot(
            link.target.center[0] - link.source.center[0],
            link.target.center[1] - link.source.center[1],
        )
        if span > _GRID_LINK_WALK_MAX_SPAN_M:
            self.log_warning(
                f"跨层连接两端相距 {span:.1f}m（超过 {_GRID_LINK_WALK_MAX_SPAN_M:g}m），"
                f"而本实现按「走过去」处理、不会替角色搬动位置；"
                f"下一段会从当前位置直线走向它的起点，可能不可行。"
                f"连接={link.link_id[:8]} {link.kind}",
                notify=True,
            )
        self.log_info(f"跨层：经 {link.kind} 从 {link.source.tier or '大地图'} 到 {link.target.tier or '大地图'}")
        return True

    def _cross_layered_rope(self, link: TierLink) -> bool:
        """执行一次绳索跨越：**下行走过去，上行点交互图标**。

        绳索不是滑索，两套动作没有共同点——绳索既没有可登的滑索架，也没有距离序列可对：

        - **下行**（落点更低）：从起点朝落点**直接走过去**，落差由游戏自己完成。实测
          map02 那条绳索两端水平只差 1.5m，玩法上就是走下落差，不需要识别任何模板。
        - **上行**（落点更高）：走到低端之后屏幕上会出现绳索的交互图标
          （``fL.gather_icon2``），点一下才会被送上高端。

        方向由两端所在层的高度区间判定（见 :func:`_rope_goes_up`）；判不出来时按下行尝试
        （走一段后靠高度验证收尾），并把"数据不够"告警出来。
        """
        source_range = self._layer_y_range(link.source.key)
        target_range = self._layer_y_range(link.target.key)
        up = _rope_goes_up(source_range, target_range)
        if up is None:
            shown = f"{link.source.tier or '大地图'} / {link.target.tier or '大地图'}"
            self.log_warning(
                f"跨层绳索分不出上下行：两端所在层（{shown}）的高度区间缺失或重叠，"
                f"本次按「直接走过去」尝试，连接={link.link_id[:8]}",
                notify=True,
            )
            # 判不出方向时按下行处理：下行是"走过去 + 验证高度"，就算其实在上行也只是
            # 走一段后按超时失败；反过来赌上行会朝崖壁白等图标，代价更隐蔽。
            return self._rope_cross_down(link, target_range)
        if up:
            return self._rope_cross_up(link, target_range)
        return self._rope_cross_down(link, target_range)

    def _layer_y_range(self, key: LayerKey) -> tuple[float, float] | None:
        """某一层已标定的高度区间；该层没有网格、或没标 ``floor_regions`` 时返回 ``None``。"""
        layered = self._grid_nav_layered_active
        grid = layered.layers.get(key) if layered is not None else None
        regions = grid.meta.floor_regions if grid is not None else ()
        lows = [region.get("y_min") for region in regions if region.get("y_min") is not None]
        highs = [region.get("y_max") for region in regions if region.get("y_max") is not None]
        if not lows or not highs:
            return None
        try:
            return float(min(lows)), float(max(highs))
        except (TypeError, ValueError):
            return None

    def _rope_cross_down(self, link: TierLink, target_range: tuple[float, float] | None) -> bool:
        """绳索下行：朝落点按住 W 走过去，等高度真的落到下一层。

        方向按**当前位置 -> 落点中心**算，不是两个矩形中心的连线：角色的落点由上一段决定，
        站在源矩形里的哪一处是随机的，用矩形中心连线会走偏。起手先对准一次；之后只在
        方位偏出 ``GRID_ROPE_REAIM_DEADBAND_DEG`` 时才重新转视角，而已经贴到落点附近
        （水平 ``GRID_ROPE_AIM_MIN_SPAN_M`` 以内）就不再转——近垂直的绳索这时方位会抖。

        没有高度就没法验证落在哪一层（目标层没标 ``floor_regions`` 时），那种情况退化成
        "走过落差段就收尾"：判定依据从高度换成水平位移，并在日志里说明，而不是静默放行。

        **按住 W 却没有任何位移**会单独计时并单独报错：实测里"输入没进游戏 / 被墙挡住"
        与"走了但高度没变"是两种完全不同的故障，只报后者会把排查方向带偏。
        """
        start = link.source.center
        end = link.target.center
        span = math.hypot(end[0] - start[0], end[1] - start[1])
        self.log_info(f"跨层：绳索下行，直接走向落点（水平 {span:.1f}m）")
        walked_enough = max(GRID_ROPE_DESCENT_MIN_SPAN_M, span + 2.0)
        if target_range is None:
            self.log_info(
                f"跨层：{link.target.tier or '大地图'} 没有标定高度区间，本次按走过 {walked_enough:.1f}m 收尾"
            )
        deadline = self.active_time() + GRID_ROPE_DESCENT_TIMEOUT_S
        aimed_bearing: float | None = None
        previous: tuple[float, float, float] | None = None
        stalled = 0.0
        self._set_grid_walking(True)
        try:
            while True:
                point = self._grid_nav_pose_point()
                if point is None:
                    self.log_warning("绳索下行：读不到当前坐标，停止前进", notify=True)
                    return False
                stalled = (
                    0.0
                    if previous is None or math.dist(point, previous) >= GRID_ROPE_STALL_EPS_M
                    else stalled + GRID_ROPE_POLL_S
                )
                previous = point
                bearing = bearing_to_point(point[0], point[2], end[0], end[1])
                if aimed_bearing is None:
                    # 起手必须对准（这一步就是本方法相对"按矩形中心连线走"的修正）
                    self._aim_grid_view_to_bearing(bearing, reason="绳索下行")
                    aimed_bearing = bearing
                elif distance_xz((point[0], point[2]), end) >= GRID_ROPE_AIM_MIN_SPAN_M and (
                    abs(angle_delta(bearing, aimed_bearing)) > GRID_ROPE_REAIM_DEADBAND_DEG
                ):
                    self._aim_grid_view_to_bearing(bearing, reason="绳索下行", log_success=False)
                    aimed_bearing = bearing
                if target_range is not None and target_range[0] <= point[1] <= target_range[1]:
                    self.log_info(f"绳索下行完成：高度 {point[1]:.1f} 已落到 {link.target.tier or '大地图'}")
                    return True
                if target_range is None:
                    walked = distance_xz((point[0], point[2]), start)
                    if walked >= walked_enough:
                        self.log_info(f"绳索下行完成：已走过 {walked:.1f}m（无高度区间可验证）")
                        return True
                if stalled >= GRID_ROPE_STALL_S:
                    self.log_warning(
                        f"绳索下行失败：按住 W 已 {stalled:.1f}s 没有任何位移"
                        f"（按住的键没进游戏，或被挡住），连接={link.link_id[:8]}",
                        notify=True,
                    )
                    return False
                if self.active_time() >= deadline:
                    self.log_warning(
                        f"绳索下行超时：按住 W 走了 {GRID_ROPE_DESCENT_TIMEOUT_S:g}s，高度仍未落到 "
                        f"{link.target.tier or '大地图'}（连接={link.link_id[:8]}）",
                        notify=True,
                    )
                    return False
                self.sleep(GRID_ROPE_POLL_S)
        finally:
            self._set_grid_walking(False)

    def _rope_cross_up(self, link: TierLink, target_range: tuple[float, float]) -> bool:
        """绳索上行：对准绳索、点 ``fL.gather_icon2`` 交互图标，等高度升到目标层。"""
        start = link.source.center
        end = link.target.center
        point = self._grid_nav_pose_point()
        origin = (point[0], point[2]) if point is not None else start
        bearing = bearing_to_point(origin[0], origin[1], end[0], end[1])
        span = math.hypot(end[0] - start[0], end[1] - start[1])
        self.log_info(f"跨层：绳索上行，找交互图标登索（水平 {span:.1f}m，方位 {bearing:.1f}°）")
        self._aim_grid_view_to_bearing(bearing, reason="绳索上行")
        if not self._click_grid_rope_icon():
            self.log_warning(
                f"跨层绳索上行失败：没找到绳索交互图标 fL.gather_icon2（连接={link.link_id[:8]}）",
                notify=True,
            )
            return False
        return self._wait_rope_ascent(link, target_range)

    def _click_grid_rope_icon(self) -> bool:
        """找绳索的交互图标并点击；找不到返回 ``False``。

        走 ok-script 的 ``wait_click_feature``：它自己刷新帧、按标定的模板找、找到就点。
        ``alt`` 用开放世界交互的点击方式，与其它野外图标一致。
        """
        click = getattr(self, "wait_click_feature", None)
        if not callable(click):
            self.log_warning("当前任务没有 wait_click_feature，无法点击绳索交互图标", notify=True)
            return False
        return bool(
            click(
                feature=fL.gather_icon2,
                time_out=GRID_ROPE_ICON_TIMEOUT_S,
                settle_time=1,
                alt=True,
                raise_if_not_found=False,
            )
        )

    def _wait_rope_ascent(self, link: TierLink, target_range: tuple[float, float]) -> bool:
        """等角色被绳索送到目标层；超时说明点击没生效，告警并判失败。"""
        deadline = self.active_time() + GRID_ROPE_RIDE_TIMEOUT_S
        while True:
            point = self._grid_nav_pose_point()
            if point is not None and target_range[0] <= point[1] <= target_range[1]:
                self.log_info(f"绳索上行完成：高度 {point[1]:.1f} 已进入 {link.target.tier or '大地图'}")
                return True
            if self.active_time() >= deadline:
                self.log_warning(
                    f"绳索上行超时：已点击交互图标，但 {GRID_ROPE_RIDE_TIMEOUT_S:g}s 内高度没有进入 "
                    f"{link.target.tier or '大地图'}（连接={link.link_id[:8]}）",
                    notify=True,
                )
                return False
            self.sleep(GRID_ROPE_POLL_S)

    def _grid_nav_pose_point(self) -> tuple[float, float, float] | None:
        """读一帧最新的世界坐标（含高度）；读不到返回 ``None``。

        绳索的上下行都要靠高度验证，所以这里不能拿旧快照凑合，也不把缺失的 Y 当成 0。
        """
        pose = self.world_pose(frame=self.next_frame(), max_age=2.0)
        if not pose:
            return None
        try:
            return float(pose["x"]), float(pose["y"]), float(pose["z"])
        except (KeyError, TypeError, ValueError):
            return None

    def _aim_grid_view_to_bearing(self, bearing: float, *, reason: str, log_success: bool = True) -> bool:
        """只转视角对准某个世界方位；未到位只告警，不阻断调用方。

        走路过程中会反复调用，``log_success=False`` 用来避免每拍刷一条"已对准"。
        """
        result = self.pose_aim_view_to_bearing(
            bearing,
            tolerance=self._grid_turn_tolerance(),
            max_rounds=2,
            min_score=self._grid_heading_min_score(),
            verify_heading=False,
        )
        if not result.get("ok"):
            self.log_warning(
                f"{reason}：对准 {bearing:.1f}° 未到位（实测={result.get('heading')}，误差={result.get('error')}）"
            )
            return False
        if log_success:
            self.log_info(f"{reason}：已对准 {bearing:.1f}°")
        return True

    def _try_layered_route_on_plan(
        self,
        ctx: NavTick,
        start_xz: tuple[float, float],
        goal_xz: tuple[float, float],
        *,
        allow_same_main: bool = False,
    ) -> tuple[bool, bool]:
        """规划时先试分层路线；返回 ``(是否已接管, 是否成功)``。

        - ``(False, _)``：**不用分层**——这张图没有分层数据，或者起终点本来就同层。
          调用方继续走原来的单层路径（含用户滑索），行为与分层功能上线前一致。
        - ``(True, True)``：分层路线已装配好、第一段已激活，调用方直接进入跟随。
        - ``(True, False)``：**确实需要跨层但走不通**（判定不出层、层图不可达……）。
          这时**不退回单层**：单层路线会无视层级，可能给出一条"物理上过不去"的路线，
          比明确失败更糟。原因已经记进日志，调用方按失败结束本次导航。

        ``allow_same_main=True`` 时，起终点都在大地图也会交给分层规划器（见
        :meth:`_plan_layered_route`）——只给"单层已经走不通"的重试用。
        """
        layered = self._grid_layered_map(
            ctx.map_id,
            grid_dir=ctx.session.request.grid_dir,
            zoom=ctx.session.request.zoom,
        )
        if layered is None:
            return False, False
        if layered is not self._grid_nav_layered_active:
            self._grid_nav_layered_active = layered
            # 换了地图/数据：旧的层内分段缓存不再适用
            self._grid_nav_layered_planner_cache.pop(id(layered), None)
        route, reason = self._plan_layered_route(
            ctx,
            layered,
            start_xz=start_xz,
            goal_xz=goal_xz,
            allow_same_main=allow_same_main,
        )
        if route is None:
            if not reason:
                # 起终点同层：交回单层路径
                return False, False
            self.log_warning(f"分层导航不可用：{reason}", notify=True)
            return True, False
        ctx.session.route = route
        ctx.session.segment_index = 0
        # 第一段之前可能还有连接要跨：起点本身就压在某个连接端点上时，那段"步行"长度为零
        # 被规划器丢掉，跨越就落在所有分段之前。漏执行它等于少跳一层。
        for link in route.prefix:
            self.log_info(f"分层导航：起手先跨 {link.kind}（起点已在连接端点上）")
            if self._cross_layered_link(ctx, link) is False:
                return True, False
        if not self._activate_layered_segment(ctx, reason="起始段"):
            return True, False
        self.log_info(
            f"分层路线：{len(route.segments)} 段 / {len(route.links)} 次跨层，"
            f"代价={route.cost:.1f}，层序={[tier or '大地图' for tier in route.tiers]}"
        )
        return True, True

    def _create_grid_route(
        self,
        start_xz: tuple[float, float],
        goal_xz: tuple[float, float],
        *,
        map_id: str,
        grid_path: str | None,
        grid_dir: str | None,
        zoom: str | None,
        time_budget_s: float | None = None,
        required_zip_line_start_id: str | None = None,
    ) -> tuple[GridRouteFollower | None, PlanResult]:
        grid = self.load_grid_for_map(
            map_id,
            grid_path=grid_path,
            grid_dir=grid_dir,
            zoom=zoom,
        )
        if grid is None:
            return None, PlanResult(False, f"未找到地图 {map_id!r} 的导航网格")
        zip_lines = self._filter_blocked_grid_zip_lines(
            self._grid_zip_lines_for_map(map_id),
            map_id,
        )
        follower = GridRouteFollower(
            grid,
            self._grid_follower_config(),
            zip_lines=zip_lines,
        )
        if zip_lines:
            stats = follower.zip_line_stats()
            self.log_info(
                f"滑索接入网格：原始节点={stats['nodes']}，可映射节点={stats['mapped_nodes']}，"
                f"有效有向边={stats['directed_edges']}"
            )
            scale = float(stats.get("heuristic_scale", 1.0))
            if scale < 1.0:
                # 这一项直接决定规划耗时：滑索越便宜，A* 就越接近均匀代价搜索。
                # 用户报"规划很慢/搜索规模超限"时，先看这行就知道是不是滑索带来的。
                self.log_info(
                    f"滑索启发式缩放={scale:g}：滑索单价低于步行，A* 会退得更接近均匀代价搜索，"
                    "扩展节点数比没有滑索时明显偏多（路径仍然有效）"
                )
            if stats["directed_edges"] == 0:
                self.log_warning(
                    "已读取用户滑索，但没有任何滑索端点能在当前网格附近映射为上下索点；本次只能使用普通网格路线"
                )
        result = follower.plan(
            start_xz,
            goal_xz,
            time_budget_s=time_budget_s,
            required_zip_line_start_id=required_zip_line_start_id,
        )
        return follower, result

    def _grid_zip_lines_for_map(self, map_id: str) -> ZipLineGraph | None:
        """读取当前账号的滑索图；认证或接口不可用时静默退回纯网格路线。"""
        if not self._grid_cfg_bool(CONFIG_GRID_USE_ZIP_LINES, True):
            return None
        if not callable(getattr(self, "zip_line_list_go", None)):
            return None
        map_id = str(map_id or "").strip()
        if not map_id:
            return None
        if map_id in self._grid_nav_zip_line_cache:
            return self._grid_nav_zip_line_cache[map_id]
        if map_id in self._grid_nav_zip_line_empty:
            return None

        service = self._get_minimap_position_service()
        capture_enabled = getattr(service, "experimental_capture_enabled", None)
        if callable(capture_enabled):
            try:
                if capture_enabled():
                    return self._grid_capture_zip_lines_for_map(service, map_id)
            except Exception as exc:
                self.log_warning(f"实验技术：读取抓包开关失败，本次不使用滑索（{type(exc).__name__}: {exc}）")
                return None

        api_get = getattr(service, "_map_api_get", None)
        if not callable(api_get):
            return None

        account = getattr(service, "_map_ws_account", None)
        if not isinstance(account, dict) or not account:
            resolver = getattr(service, "_resolve_map_account_from_cred", None)
            if not callable(resolver):
                return None
            try:
                account = resolver()
            except Exception as exc:
                self.log_info(f"读取用户滑索失败，继续使用普通网格路线: {exc}")
                return None
        if not isinstance(account, dict) or not account:
            return None

        try:
            response = api_get(
                "/web/v1/game/endfield/map/mark/list",
                {"mapId": map_id, **account},
            )
        except Exception as exc:
            self.log_info(f"读取用户滑索失败，继续使用普通网格路线: {exc}")
            return None
        if not isinstance(response, dict) or response.get("code") not in (None, 0):
            self.log_info("地图滑索接口未返回有效数据，继续使用普通网格路线")
            return None

        user_map_mark_store.persist_user_marks(
            response,
            map_id=map_id,
            account_id=str(getattr(self, "current_account_id", "") or "").strip(),
            map_user_id=str(getattr(service, "_map_ws_user_id", "") or "").strip(),
            role_id=str(account.get("roleId") or "").strip(),
            server_id=str(account.get("serverId") or "").strip(),
        )

        user_items = map_mark_query.extract_mark_points(response, map_id=map_id, user_only=True)
        power_points = [point for name in ZIP_LINE_POWER_FACILITY_NAMES for point in user_items.get(name, [])]
        include_shared = self._grid_cfg_bool(CONFIG_GRID_USE_SHARED_ZIP_LINES, False)
        graph = ZipLineGraph.from_mark_payloads(
            [response],
            map_id=map_id,
            shared_facility_points=power_points,
            include_shared_zip_lines=include_shared,
        )
        if graph:
            self._grid_nav_zip_line_cache[map_id] = graph
            summary = graph.summary()
            self.log_info(
                f"已加载用户滑索：节点={summary['nodes']}，连接={summary['links']}，类型={summary['by_name']}"
            )
            return graph
        # 接口成功但没有滑索属于稳定结果，缓存空结果；认证/网络失败则不缓存，
        # 下一次重规划或下一轮任务仍可重试。
        self._grid_nav_zip_line_empty.add(map_id)
        return None

    def _grid_capture_zip_lines_for_map(self, service, map_id: str) -> ZipLineGraph | None:
        """实验抓包模式：按需读取完整滑索快照，不混用官方地图 mark 数据。"""
        if map_id in self._grid_nav_zip_line_unavailable:
            return None
        loader = getattr(service, "capture_travel_pole_graph", None)
        if not callable(loader):
            self._grid_nav_zip_line_unavailable.add(map_id)
            self.log_warning("实验技术：当前定位服务不支持抓包滑索接口，本次导航继续使用普通网格路线")
            return None

        include_shared = self._grid_cfg_bool(CONFIG_GRID_USE_SHARED_ZIP_LINES, False)
        try:
            result = loader(map_id=map_id, include_shared=include_shared)
        except Exception as exc:
            self._grid_nav_zip_line_unavailable.add(map_id)
            self.log_warning(f"实验技术：读取抓包滑索失败（{type(exc).__name__}: {exc}），本次导航继续使用普通网格路线")
            return None

        graph = getattr(result, "graph", None)
        reason = str(getattr(result, "reason", "") or "unknown")
        cacheable = bool(getattr(result, "cacheable", False))
        if isinstance(graph, ZipLineGraph) and graph:
            self._grid_nav_zip_line_cache[map_id] = graph
            summary = graph.summary()
            self.log_info(
                f"已加载抓包滑索：节点={summary['nodes']}，连接={summary['links']}，"
                f"类型={summary['by_name']}，chapter={graph.nodes[0].level_id}"
            )
            return graph
        if cacheable:
            self._grid_nav_zip_line_empty.add(map_id)
            self.log_info(f"抓包滑索快照已同步，但当前地图没有可用滑索（{reason}）")
            return None
        self._grid_nav_zip_line_unavailable.add(map_id)
        self.log_info(f"抓包滑索暂不可用（{reason}），本次导航继续使用普通网格路线")
        return None

    def _refresh_grid_zip_lines(self, map_id: str = "") -> None:
        """清理滑索快照，使下一次规划重新读取当前账号数据。

        每次新的 ``navigate_grid_to`` / ``plan_grid_path`` 调用都会刷新；同一次导航
        内部的偏航重规划继续复用本次快照，避免重复请求接口。
        """
        map_id = str(map_id or "").strip()
        if map_id:
            self._grid_nav_zip_line_cache.pop(map_id, None)
            self._grid_nav_zip_line_empty.discard(map_id)
            self._grid_nav_zip_line_unavailable.discard(map_id)
            return
        self._grid_nav_zip_line_cache.clear()
        self._grid_nav_zip_line_empty.clear()
        self._grid_nav_zip_line_unavailable.clear()

    def _get_minimap_position_service(self):
        if self._grid_minimap_position_service is not None:
            return self._grid_minimap_position_service
        service = self.get_runtime_position_service()
        self._grid_minimap_position_service = service
        return service

    @staticmethod
    def _grid_position_is_known(follower: GridRouteFollower, position: tuple[float, float]) -> bool:
        grid = getattr(follower, "grid", None)
        if grid is None:
            return False
        cell = grid.index_of_world(position[0], position[1])
        return bool(grid.is_free(*cell))

    @staticmethod
    def _record_grid_visited_cell(
        follower: GridRouteFollower,
        position: tuple[float, float],
        visited_cells: dict[tuple[int, int], tuple[float, float]],
    ) -> None:
        """记录当前定位所属的已知 free 格，供偏航恢复使用。"""
        grid = getattr(follower, "grid", None)
        if grid is None:
            return
        cell = grid.index_of_world(position[0], position[1])
        if grid.is_free(*cell):
            visited_cells[cell] = grid.world_of_index(*cell)

    @staticmethod
    def _nearest_visited_grid_cell(
        position: tuple[float, float],
        visited_cells: dict[tuple[int, int], tuple[float, float]],
    ) -> tuple[float, float] | None:
        """返回距离当前位置最近的历史已知格中心。"""
        if not visited_cells:
            return None
        return min(
            visited_cells.values(),
            key=lambda point: math.hypot(
                point[0] - position[0],
                point[1] - position[1],
            ),
        )

    def _grid_follower_config(self) -> FollowerConfig:
        """把全局 ``Nav Config`` 的规划/行走参数装成 :class:`FollowerConfig`。

        兜底值统一从 :data:`DEFAULT_GRID_NAV_CONFIG` 取，不再逐项重复字面量：这些兜底
        只在"全局配置里连这个键都没有"时才会用到，重复写死只会让它们和真实默认值悄悄
        分叉（``离墙安全边距`` 就在这里漂过一次 1 vs 3）。
        """
        defaults = DEFAULT_GRID_NAV_CONFIG
        return FollowerConfig(
            arrive_radius=max(
                0.0, self._grid_cfg_float(CONFIG_GRID_WAYPOINT_RADIUS, defaults[CONFIG_GRID_WAYPOINT_RADIUS])
            ),
            goal_radius=max(0.0, self._grid_cfg_float(CONFIG_GRID_GOAL_RADIUS, defaults[CONFIG_GRID_GOAL_RADIUS])),
            heading_tolerance=self._grid_heading_tolerance(),
            stuck_window_s=max(0.1, self._grid_cfg_float(CONFIG_GRID_STUCK_WINDOW, defaults[CONFIG_GRID_STUCK_WINDOW])),
            stuck_min_distance=max(
                0.0, self._grid_cfg_float(CONFIG_GRID_STUCK_DISTANCE, defaults[CONFIG_GRID_STUCK_DISTANCE])
            ),
            risk_cost=max(0.1, self._grid_cfg_float(CONFIG_GRID_RISK_COST, defaults[CONFIG_GRID_RISK_COST])),
            shortcut_radius=max(
                0.0, self._grid_cfg_float(CONFIG_GRID_SHORTCUT_RADIUS, defaults[CONFIG_GRID_SHORTCUT_RADIUS])
            ),
            allow_unknown=self.allow_grid_unknown(),
            margin=max(0, self._grid_cfg_int(CONFIG_GRID_MARGIN, defaults[CONFIG_GRID_MARGIN])),
            wall_penalty=max(0.0, self._grid_cfg_float(CONFIG_GRID_WALL_PENALTY, defaults[CONFIG_GRID_WALL_PENALTY])),
            frontier_margin=max(
                0, self._grid_cfg_int(CONFIG_GRID_FRONTIER_MARGIN, defaults[CONFIG_GRID_FRONTIER_MARGIN])
            ),
            frontier_penalty=max(
                0.0, self._grid_cfg_float(CONFIG_GRID_FRONTIER_PENALTY, defaults[CONFIG_GRID_FRONTIER_PENALTY])
            ),
            waypoint_tolerance=max(
                0.0, self._grid_cfg_float(CONFIG_GRID_WAYPOINT_TOLERANCE, defaults[CONFIG_GRID_WAYPOINT_TOLERANCE])
            ),
            max_expand=max(1, self._grid_cfg_int(CONFIG_GRID_MAX_EXPAND, defaults[CONFIG_GRID_MAX_EXPAND])),
            narrow_calibration=self._grid_cfg_bool(
                CONFIG_GRID_NARROW_CALIBRATION, defaults[CONFIG_GRID_NARROW_CALIBRATION]
            ),
            narrow_stop_radius=max(
                0.0, self._grid_cfg_float(CONFIG_GRID_NARROW_STOP_RADIUS, defaults[CONFIG_GRID_NARROW_STOP_RADIUS])
            ),
        )

    def _grid_heading_tolerance(self) -> float:
        """读取行走朝向容差，并限制在安全上限以内。"""
        configured = self._grid_cfg_float(
            CONFIG_GRID_HEADING_TOLERANCE,
            GRID_HEADING_TOLERANCE_DEG,
        )
        return min(GRID_HEADING_TOLERANCE_DEG, max(0.0, configured))

    def _grid_turn_tolerance(self) -> float:
        """读取闭环转向容差，并限制在安全上限以内。"""
        configured = self._grid_cfg_float(
            CONFIG_GRID_TURN_TOLERANCE,
            GRID_TURN_TOLERANCE_DEG,
        )
        return min(GRID_TURN_TOLERANCE_DEG, max(0.0, configured))

    def _grid_zip_line_esc_visible(self) -> bool | None:
        """检测大世界 ESC 模板；存在表示尚未成功登上滑索。"""
        find_feature = getattr(self, "find_feature", None)
        if not callable(find_feature):
            self.log_warning("当前任务无法检测 fL.esc 模板，不能确认是否登上滑索", notify=True)
            return None
        try:
            results = find_feature(
                feature_name=fL.esc,
                frame=self.next_frame(),
                threshold=GRID_ZIP_LINE_ESC_THRESHOLD,
            )
        except Exception as exc:
            self.log_warning(f"检测 fL.esc 模板失败: {exc}", notify=True)
            return None
        return bool(results)

    def _find_grid_zip_line_board_prompt(self, frame) -> bool:
        """接近滑索时检测「登上滑索架」交互模板。"""
        find_feature = getattr(self, "find_feature", None)
        if not callable(find_feature):
            return False
        box = getattr(getattr(self, "box", None), "bottom_right", None)
        try:
            results = find_feature(
                feature_name=fL.climb_the_zip_line,
                frame=frame,
                box=box,
                threshold=GRID_ZIP_LINE_ESC_THRESHOLD,
            )
        except Exception as exc:
            self.log_warning(f"检测「登上滑索架」模板失败: {exc}", notify=True)
            return False
        return bool(results)

    @staticmethod
    def _grid_zip_line_entry_distance(
        route_step,
        position: tuple[float, float],
    ) -> float:
        entry = route_step.step.entry
        return math.hypot(
            float(entry.x) - float(position[0]),
            float(entry.z) - float(position[1]),
        )

    def _enter_grid_zip_line_walk_mode(self) -> None:
        """接近滑索时只切换一次步行模式。"""
        if self._grid_nav_zip_line_walk_mode:
            return
        self.press_key("ctrl", after_sleep=0.05)
        self._grid_nav_zip_line_walk_mode = True
        self.log_info("距滑索约 5m，切换步行模式接近并搜索登上滑索架")

    def _restore_grid_zip_line_run_mode(self) -> None:
        """登索成功后恢复默认奔跑模式。"""
        if not self._grid_nav_zip_line_walk_mode:
            return
        self.press_key("ctrl", after_sleep=0.01)
        self._grid_nav_zip_line_walk_mode = False
        self.log_info("登索结束，恢复奔跑模式")

    def _retreat_from_grid_zip_line(
        self,
        deadline: float,
        *,
        reason: str,
    ) -> bool:
        """按 S 远离滑索，下一轮重新按最新 WS 计算入口方位。"""
        self._set_grid_walking(False)
        remaining = deadline - self.active_time()
        if remaining <= 0:
            return False
        duration = min(GRID_ZIP_LINE_RETREAT_DURATION_S, remaining)
        self.log_info(f"{reason}，按 S 远离滑索 {duration:.1f}s 后重试")
        self._hold_grid_keys(("s",), duration)
        if self._grid_nav_follower is not None:
            self._grid_nav_follower.pause()
        self._grid_nav_zip_line_approach_entry_id = None
        self._grid_nav_zip_line_approach_started_at = None
        self._grid_nav_zip_line_approach_attempts = 0
        return True

    def _press_f_for_grid_zip_line(
        self,
        deadline: float,
    ) -> bool | None:
        """发现模板后立即按 F；失败则远离滑索，返回外层重试。"""
        self._set_grid_walking(False)
        self.log_info("搜索到「登上滑索架」，立即按 F 登索")
        self.press_key("f", after_sleep=0.2)
        esc_visible = self._wait_grid_zip_line_esc_clear()
        if esc_visible is False:
            self._grid_nav_zip_line_approach_attempts = 0
            self._restore_grid_zip_line_run_mode()
            self.log_info("F 登索成功")
            return True
        if esc_visible is None:
            self._restore_grid_zip_line_run_mode()
            return False
        if not self._retreat_from_grid_zip_line(
            deadline,
            reason="按 F 后仍未登上滑索",
        ):
            self._restore_grid_zip_line_run_mode()
            return False
        return None

    def _walk_grid_zip_line_until_prompt(
        self,
        *,
        duration: float,
        deadline: float,
    ) -> bool:
        """保持 W 连续前进，同时轮询登索模板；命中后立即停车。"""
        end = min(
            self.active_time() + max(0.0, float(duration)),
            deadline,
        )
        if end <= self.active_time():
            return False
        self._set_grid_walking(True)
        try:
            while self.active_time() < end:
                if self._find_grid_zip_line_board_prompt(self.next_frame()):
                    return True
                remaining = end - self.active_time()
                if remaining <= 0:
                    return False
                self.sleep(min(GRID_ZIP_LINE_PROMPT_POLL_S, remaining))
            return False
        finally:
            self._set_grid_walking(False)

    def _search_grid_zip_line_board_prompt_locally(
        self,
        *,
        deadline: float,
    ) -> bool:
        """在入口附近用短距 W/A/D/S 搜索模板，避免直接长距离后退。"""
        for key in ("w", "a", "d", "s"):
            remaining = deadline - self.active_time()
            if remaining <= 0:
                return False
            self._hold_grid_keys(
                (key,),
                min(GRID_ZIP_LINE_LOCAL_SEARCH_STEP_S, remaining),
            )
            if self._find_grid_zip_line_board_prompt(self.next_frame()):
                return True
        return False

    def _approach_grid_zip_line_for_boarding(
        self,
        *,
        route_step,
        frame,
        min_score: float,
        deadline: float,
        tick: float,
    ) -> bool | None:
        """仅按最新 WS 与入口坐标算方位，走过去并同步搜索登索模板。

        Returns:
            True: 已成功按 F 登索。
            False: 无法确认登索或导航时间耗尽。
            None: 本轮尚未完成，外层继续下一拍。
        """
        self._enter_grid_zip_line_walk_mode()
        entry_id = str(route_step.step.entry.node_id)
        if self._grid_nav_zip_line_approach_entry_id != entry_id:
            self._grid_nav_zip_line_approach_entry_id = entry_id
            self._grid_nav_zip_line_approach_started_at = self.active_time()
            self._grid_nav_zip_line_approach_attempts = 0

        if self._find_grid_zip_line_board_prompt(frame):
            return self._press_f_for_grid_zip_line(deadline)

        entry = route_step.steps[0].entry
        ws_position = self._zip_line_ws_position()
        if ws_position is None:
            self._set_grid_walking(False)
            self.sleep(tick)
            return None

        distance_to_entry = math.hypot(
            float(entry.x) - float(ws_position[0]),
            float(entry.z) - float(ws_position[1]),
        )
        if distance_to_entry > GRID_ZIP_LINE_WS_STOP_DISTANCE_M:
            target_bearing = bearing_to_point(
                float(ws_position[0]),
                float(ws_position[1]),
                float(entry.x),
                float(entry.z),
            )
            result = self.pose_aim_view_to_bearing(
                target_bearing,
                tolerance=self._grid_turn_tolerance(),
                max_rounds=1,
                min_score=min_score,
                verify_heading=False,
            )
            if result.get("ok"):
                step_duration = min(
                    GRID_ZIP_LINE_WS_STEP_DURATION_S,
                    max(
                        GRID_ZIP_LINE_WS_MIN_STEP_DURATION_S,
                        distance_to_entry / 4.0,
                    ),
                )
                if self._walk_grid_zip_line_until_prompt(
                    duration=step_duration,
                    deadline=deadline,
                ):
                    return self._press_f_for_grid_zip_line(deadline)
            else:
                self._set_grid_walking(False)
                self.log_warning(
                    f"按 WS 到滑索入口的方位调整视角失败：目标={target_bearing:.1f}°，实测={result.get('heading')}"
                )
        else:
            self._set_grid_walking(False)

        now = self.active_time()
        started_at = self._grid_nav_zip_line_approach_started_at
        search_timed_out = started_at is not None and now - started_at >= GRID_ZIP_LINE_APPROACH_TIMEOUT_S
        if search_timed_out:
            self.log_info(f"接近滑索搜索超时（距入口 {distance_to_entry:.2f}m），在入口附近短距搜索，不先后退")
            if self._search_grid_zip_line_board_prompt_locally(deadline=deadline):
                return self._press_f_for_grid_zip_line(deadline)
            self._grid_nav_zip_line_approach_attempts += 1
            if self._grid_nav_zip_line_approach_attempts < GRID_ZIP_LINE_APPROACH_MAX_ATTEMPTS:
                self._grid_nav_zip_line_approach_started_at = now
                return None
            if not self._retreat_from_grid_zip_line(
                deadline,
                reason="多次短距搜索仍未找到登上滑索架",
            ):
                self._restore_grid_zip_line_run_mode()
                return False
            self._grid_nav_zip_line_approach_attempts = 0
        return None

    def _handle_grid_zip_line_route_step(
        self,
        route_step,
        *,
        already_on_rack: bool = False,
    ) -> str:
        """执行滑索步骤并返回 completed/replan/failed。"""
        try:
            if not self._execute_grid_zip_line(
                route_step,
                already_on_rack=already_on_rack,
            ):
                return "failed"
        except ZipLineReplanRequired as exc:
            self.log_warning(
                f"{exc}，从当前滑索架重新规划",
                notify=True,
            )
            self._grid_nav_zip_line_start_hint = exc.current_position
            self._grid_nav_zip_line_failed_target_hint = exc.failed_target_position
            self._grid_nav_follower = None
            return "replan"
        if self._grid_nav_follower is None or not self._grid_nav_follower.complete_zip_line():
            self.log_warning("滑索完成后路线状态无效，重新规划", notify=True)
            self._grid_nav_follower = None
            return "replan"
        return "completed"

    def _reaim_grid_zip_line_for_boarding(
        self,
        route_step,
        *,
        notify: bool = True,
        log_prefix: str = "登索失败后",
    ) -> bool:
        """按当前融合坐标到入口滑索架的方位重新对准，不移动角色。"""
        position = None
        world_pose_getter = getattr(self, "world_pose", None)
        if callable(world_pose_getter):
            try:
                state = world_pose_getter(
                    frame=self.next_frame(),
                    max_age=2.0,
                )
            except Exception:
                state = None
            if state is not None:
                x = state.get("x")
                z = state.get("z")
                if x is not None and z is not None:
                    position = (float(x), float(z))

        if position is None:
            position_getter = getattr(self, "_zip_line_ws_position", None)
            if callable(position_getter):
                try:
                    position = position_getter()
                except Exception:
                    position = None
        if position is None:
            self.log_warning("无法读取当前坐标，不能重新对准入口滑索架", notify=notify)
            return False

        first = route_step.steps[0]
        entry = first.entry
        distance_to_entry = math.hypot(
            float(entry.x) - float(position[0]),
            float(entry.z) - float(position[1]),
        )
        if distance_to_entry > 0.2:
            target_bearing = bearing_to_point(
                float(position[0]),
                float(position[1]),
                float(entry.x),
                float(entry.z),
            )
        else:
            target_bearing = bearing_to_point(
                float(entry.x),
                float(entry.z),
                float(first.exit.x),
                float(first.exit.z),
            )

        result = self.pose_aim_view_to_bearing(
            target_bearing,
            tolerance=self._grid_turn_tolerance(),
            max_rounds=2,
            min_score=self._grid_heading_min_score(),
            verify_heading=False,
        )
        if not result.get("ok"):
            self.log_warning(
                f"{log_prefix}重新对正未到位：目标={target_bearing:.1f}°，"
                f"实测={result.get('heading')}，误差={result.get('error')}",
                notify=notify,
            )
            return False
        self.log_info(
            f"{log_prefix}已发送重新对准视角位移：目标={target_bearing:.1f}°，"
            f"估算当前朝向={result.get('heading')}，估算误差={result.get('error')}，"
            f"距入口滑索架={distance_to_entry:.2f}m"
        )
        return True

    def _wait_grid_zip_line_esc_clear(
        self,
        *,
        timeout: float = GRID_ZIP_LINE_BOARD_CONFIRM_TIMEOUT_S,
        poll_interval: float = GRID_ZIP_LINE_BOARD_CONFIRM_POLL_S,
    ) -> bool | None:
        """等待大世界 ESC 模板消失，避免把登索过渡帧误判为失败。"""
        deadline = self.active_time() + max(0.0, float(timeout))
        interval = max(0.05, float(poll_interval))
        while True:
            visible = self._grid_zip_line_esc_visible()
            if visible is False:
                return False
            if visible is None:
                return None
            if self.active_time() >= deadline:
                return True
            self.sleep(interval)

    def _execute_grid_zip_line(
        self,
        route_step,
        *,
        already_on_rack=False,
    ) -> bool:
        """执行一次规划出的滑索移动。

        三个阶段：校验与准备分段数据 → 登上滑索架（已在架上则跳过，见
        :meth:`_grid_zip_line_enter_rack`）→ 交给 ``zip_line_list_go`` 依次乘坐。

        具体对齐与交互复用任务已有的 ``ZipLineMixin.zip_line_list_go``，避免在导航
        层复制 OCR 和按键流程。没有混入滑索能力的任务会明确失败，而不是把滑索
        航点误当成普通步行目标。
        """
        if route_step is None:
            return False
        execute = getattr(self, "zip_line_list_go", None)
        board = getattr(self, "board_zip_line", None)
        if not callable(execute) or (not already_on_rack and not callable(board)):
            self.log_warning(
                "规划路线需要乘坐滑索，但当前任务未接入滑索执行能力",
                notify=True,
            )
            return False
        chain = route_step.steps
        distances = [round(float(step.distance_m)) for step in chain]
        if not distances or any(distance <= 0 for distance in distances):
            self.log_warning(f"滑索距离无效: {route_step.distance_m!r}", notify=True)
            return False
        distance_tolerances = [max(2, round(float(step.distance_m) * 0.08)) for step in chain]
        target_bearings = [
            bearing_to_point(
                step.entry.x,
                step.entry.z,
                step.exit.x,
                step.exit.z,
            )
            for step in chain
        ]
        target_positions = [(step.exit.x, step.exit.z) for step in chain]
        source_positions_xyz = [step.entry.xyz for step in chain]
        target_positions_xyz = [step.exit.xyz for step in chain]
        scroll_enabled = getattr(self, "zip_line_scroll_enabled", None)
        need_scroll = bool(scroll_enabled()) if callable(scroll_enabled) else False
        chain_text = " -> ".join(f"{step.entry.name}({step.distance_m:.1f}m)" for step in chain)
        chain_text += f" -> {chain[-1].exit.name}"
        self.log_info(
            f"执行滑索链：{chain_text}，总距离={route_step.distance_m:.1f}m，"
            f"OCR 目标={distances}，世界方位={[round(b, 1) for b in target_bearings]}"
        )
        try:
            if already_on_rack:
                self.log_info("当前已在滑索架上，跳过登上操作，直接对准下一目标")
            else:
                switch_walk_mode = not self._grid_nav_zip_line_walk_mode
                if not self._grid_zip_line_enter_rack(
                    route_step,
                    board,
                    switch_walk_mode=switch_walk_mode,
                ):
                    return False
            self._restore_grid_zip_line_run_mode()
            execute(
                distances,
                need_scroll=need_scroll,
                need_v=False,
                distance_tolerance=distance_tolerances,
                target_bearing=target_bearings,
                target_positions=target_positions,
                source_positions_xyz=source_positions_xyz,
                target_positions_xyz=target_positions_xyz,
            )
        except ZipLineReplanRequired:
            raise
        except Exception as exc:
            self.log_warning(f"滑索执行失败: {exc}", notify=True)
            return False
        return True

    def _grid_zip_line_attempt_board(self, board, *, switch_walk_mode) -> ZipLineBoardAttempt:
        """按一次「登上滑索架」并用 ``fL.esc`` 确认结果。

        只做动作与判定、不写日志：同一个动作在首轮与"重新对正后重试"轮要落到不同的
        告警文案上，措辞由调用方 :meth:`_grid_zip_line_enter_rack` 决定。``board``
        由调用方保证可调用。
        """
        if not board(
            direct_wait=5.0,
            total_time_out=30.0,
            switch_walk_mode=switch_walk_mode,
        ):
            return ZipLineBoardAttempt.BUTTON_MISSING
        esc_visible = self._wait_grid_zip_line_esc_clear()
        if esc_visible is None:
            return ZipLineBoardAttempt.UNCONFIRMED
        if esc_visible:
            return ZipLineBoardAttempt.NOT_BOARDED
        return ZipLineBoardAttempt.BOARDED

    def _grid_zip_line_enter_rack(self, route_step, board, *, switch_walk_mode) -> bool:
        """登上滑索架；确认没登上时重新对准一次再试，仍失败才放弃。

        重试策略只由 :class:`ZipLineBoardAttempt` 的结果决定：找不到按钮或无法确认
        都不重试（重对准也解决不了），只有"确认没登上"才值得重新对正一次。
        """
        attempt = self._grid_zip_line_attempt_board(board, switch_walk_mode=switch_walk_mode)
        if attempt is ZipLineBoardAttempt.BOARDED:
            self.log_info("fL.esc 不存在，已成功登上滑索")
            return True
        if attempt is ZipLineBoardAttempt.BUTTON_MISSING:
            self.log_warning("未找到「登上滑索架」按钮", notify=True)
            return False
        if attempt is ZipLineBoardAttempt.UNCONFIRMED:
            return False
        self.log_warning(
            "点击登上滑索后仍检测到 fL.esc，判定未登上滑索，重新对正后重试",
            notify=True,
        )
        if not self._reaim_grid_zip_line_for_boarding(route_step):
            return False
        attempt = self._grid_zip_line_attempt_board(board, switch_walk_mode=switch_walk_mode)
        if attempt is ZipLineBoardAttempt.BOARDED:
            self.log_info("fL.esc 不存在，已成功登上滑索")
            return True
        if attempt is ZipLineBoardAttempt.BUTTON_MISSING:
            self.log_warning("重新对正后仍未找到「登上滑索架」按钮", notify=True)
            return False
        self.log_warning("重新对正后仍检测到 fL.esc，登上滑索失败", notify=True)
        return False

    def _grid_heading_min_score(self) -> float:
        """朝向质量阈值属于共享定位服务，网格任务只读取，不另存一份。"""
        try:
            return max(0.0, min(1.0, float(self.pose_heading_min_score())))
        except (AttributeError, TypeError, ValueError):
            return 0.6

    def _log_grid_plan(self, result: PlanResult) -> None:
        self.log_info(
            f"规划完成：{len(result.cells)} 格 / {len(result.waypoints)} 航点，"
            f"代价={result.cost:.1f}，未知格={result.risk_cells}，"
            f"滑索={len(result.zip_line_steps)}"
        )
        if not result.zip_line_steps and self._grid_nav_follower is not None:
            stats_getter = getattr(self._grid_nav_follower, "zip_line_stats", None)
            stats = stats_getter() if callable(stats_getter) else {}
            if stats.get("directed_edges", 0) > 0:
                self.log_info(
                    f"本次路线未采用滑索：已接入 {stats['directed_edges']} 条有向边，"
                    "但普通路线代价更低或滑索不在可达路径上"
                )
        path_text = " -> ".join(f"({x:.3f}, {z:.3f})" for x, z in result.waypoints)
        self.log_info(f"路径点：{path_text}")
        for note in result.notes:
            self.log_warning(note)

    def _log_grid_plan_failure(self, result: PlanResult, prefix: str = "路径规划失败") -> None:
        """规划失败时连诊断一起打：为什么失败、搜索了多深、起终点有没有被挪动。

        只打一句 reason 不够用：`map01_4` 那次失败真正的原因（起点落在未知格、被挪到
        5 格外的另一个可行走孤岛、只搜索了 1225 格就穷尽）全在 notes 和 expanded 里，
        不看这两项根本判断不出该改什么。
        """
        self.log_warning(f"{prefix}: {result.reason}", notify=True)
        if result.timed_out:
            self.log_warning(f"{prefix}诊断: 本次导航剩余时间不足，未完成路径搜索")
        elif result.cap_exceeded:
            self.log_warning(f"{prefix}诊断: 扩展节点已达上限，未搜索完；可调大「{CONFIG_GRID_MAX_EXPAND}」")
        else:
            self.log_warning(f"{prefix}诊断: 起点可达区域已穷尽（共 {result.expanded} 格），不是搜索深度不够")
        if not self.allow_grid_unknown():
            self.log_warning(f"{prefix}诊断: 当前「{CONFIG_GRID_ALLOW_UNKNOWN}」关闭，只能在已知可行走区内搜索")
        for note in result.notes:
            self.log_warning(f"{prefix}诊断: {note}")

    def allow_grid_unknown(self) -> bool:
        """当前是否允许穿越未知格（用任务配置，不是库默认）。"""
        return self._grid_cfg_bool(CONFIG_GRID_ALLOW_UNKNOWN, False)

    def _maybe_start_grid_sprint(self, step: FollowerStep) -> None:
        """长航段保持 W 时按一次 shift 进入冲刺，避免每拍重复切换。

        航段长度由跟随器在 :class:`FollowerStep` 里给出（``leg_distance_m``），
        这里不再去读 ``plan_result.waypoints``——跟随器的航点索引只有它自己维护得对。
        """
        if self._grid_nav_sprint_active or not self._grid_nav_w_held:
            return
        leg_distance = float(step.leg_distance_m or 0.0)
        if leg_distance <= GRID_SPRINT_MIN_SEGMENT_METERS:
            return
        self.press_key("shift", after_sleep=0.05)
        self._grid_nav_sprint_active = True
        self.log_info(f"当前航段 {leg_distance:.1f}m > {GRID_SPRINT_MIN_SEGMENT_METERS:g}m，按住 W 时按 shift 进入冲刺")

    def _set_grid_walking(self, held: bool) -> None:
        """按住或松开 ``W``；本地记录乐观状态，避免重复发送没有返回值的按键事件。"""
        held = bool(held)
        if held == self._grid_nav_w_held:
            return
        if held:
            self.send_key_down("w")
        else:
            self.send_key_up("w")
            self._grid_nav_sprint_active = False
        self._grid_nav_w_held = held

    def _should_turn_grid_in_place(self, step: FollowerStep) -> bool:
        """大角度转向或接近航点时停车转向，避免画弧越过错过的航点。"""
        heading_error = step.heading_error
        if heading_error is not None:
            max_start = max(
                1.0,
                self._grid_cfg_float(CONFIG_GRID_MOVING_TURN_MAX_START_DEG, 45.0),
            )
            if abs(float(heading_error)) > max_start:
                self.log_info(f"转向角 {abs(float(heading_error)):.1f}° > {max_start:.1f}°，改为停车转向")
                return True
        distance = step.distance_to_waypoint
        if distance is not None:
            min_distance = max(
                0.0,
                self._grid_cfg_float(CONFIG_GRID_MOVING_TURN_MIN_DISTANCE, 5.0),
            )
            if float(distance) < min_distance:
                self.log_info(f"距目标航点 {float(distance):.2f}m < {min_distance:.2f}m，改为停车转向")
                return True
        return False

    def _turn_grid_while_moving(self, step: FollowerStep) -> None:
        """保持 ``W`` 前进，同时用限幅鼠标位移连续修正朝向。"""
        heading_error = step.heading_error
        if heading_error is None:
            return
        if not self.pose_can_turn():
            self._set_grid_walking(False)
            return
        per_px = self.pose_yaw_per_pixel()
        if per_px == 0:
            self.log_warning("定位接口未提供有效旋转比例，无法移动转向")
            self._set_grid_walking(False)
            return
        gain = max(0.1, min(1.0, self._grid_cfg_float(CONFIG_GRID_MOVING_TURN_GAIN, 0.8)))
        max_angle = max(1.0, self._grid_cfg_float(CONFIG_GRID_MOVING_TURN_MAX_DEG, 25.0))
        delta = max(-max_angle, min(max_angle, float(heading_error) * gain))
        dx = round(delta / per_px)
        if dx == 0:
            return
        self._set_grid_walking(True)
        self.pose_send_rotation(dx)

    def _log_grid_navigation_debug(self, state: dict, *, map_id: str, tag: str) -> None:
        """调试模式下节流输出单帧定位状态，便于关联规划、里程计与静止判定。"""
        if not getattr(self, "debug", False):
            return
        debug_key = (
            tag,
            map_id,
            bool(state.get("rest")),
            bool(state.get("anchor_set")),
            bool(state.get("odom_ok")),
            state.get("odom_reason"),
            int(state.get("sync_seq") or 0),
            state.get("heading") is None,
        )
        now = self.active_time()
        if debug_key == self._grid_nav_last_debug_key and now - self._grid_nav_last_debug_at < 1.0:
            return
        self._grid_nav_last_debug_key = debug_key
        self._grid_nav_last_debug_at = now
        x, z = state.get("x"), state.get("z")
        pos_text = f"({float(x):.3f}, {float(z):.3f})" if x is not None and z is not None else "未锚定"
        ws = state.get("ws")
        ws_text = f"({float(ws[0]):.3f}, {float(ws[1]):.3f})" if ws is not None else "-"
        error = state.get("error")
        error_text = f"{float(error):.3f}m" if error is not None else "-"
        dmap = state.get("dmap_px") or (0.0, 0.0)
        world_delta = state.get("world_delta") or (0.0, 0.0)
        heading = state.get("heading")
        heading_text = f"{float(heading):.1f}" if heading is not None else "-"
        score = state.get("heading_score")
        score_text = f"{float(score):.3f}" if score is not None else "-"
        self.log_debug(
            f"[导航定位] {tag} map={map_id or '-'} 推算={pos_text} WS={ws_text} "
            f"误差={error_text} 像素位移=({float(dmap[0]):.1f}, {float(dmap[1]):.1f}) "
            f"世界位移=({float(world_delta[0]):.3f}, {float(world_delta[1]):.3f}) "
            f"朝向={heading_text} score={score_text} 静止={bool(state.get('rest'))} "
            f"锚定={bool(state.get('anchor_set'))} "
            f"里程计={bool(state.get('odom_ok'))}:{state.get('odom_reason')} "
            f"可信={bool(state.get('position_trusted', True))}:{state.get('trust_reason')} "
            f"校准检查={bool(state.get('sync_checked'))} "
            f"校准序号={int(state.get('sync_seq') or 0)}"
        )

    def _recover_grid_stuck(
        self,
        *,
        position: tuple[float, float],
        heading: float,
        target_bearing: float | None,
        frame,
        attempt: int,
        deadline: float | None = None,
        max_attempts: int | None = None,
    ) -> None:
        """按 :data:`_GRID_STUCK_RECOVERY` 的序列脱困：后退、侧向绕开、对准后跳跃。

        ``max_attempts`` 只用于把"第几次/共几次"写对；尝试次数超出序列长度时重复
        最后一步，所以把「卡住脱困次数」调大不会越界。
        """
        duration = self._grid_recovery_duration(deadline)
        if duration is None:
            return
        total = len(_GRID_STUCK_RECOVERY) if max_attempts is None else max(1, int(max_attempts))
        step, label = self._grid_stuck_recovery_step(attempt, total)
        if step is StuckRecovery.BACK:
            self.log_info(f"{label}：按 S 后退")
            self.press_key("s", down_time=duration)
            return
        if step is StuckRecovery.BACK_SIDE:
            side = self._choose_grid_strafe_side(position, heading)
            side_name = "A 左移" if side == "a" else "D 右移"
            self.log_info(f"{label}：按 S + {side_name}")
            duration = self._grid_recovery_duration(deadline)
            if duration is not None:
                self._hold_grid_keys(("s", side), duration)
            return

        self.log_info(f"{label}：对准目标后按住 W + Space 跳跃")
        if target_bearing is not None:
            self.pose_turn_to_bearing(
                target_bearing,
                tolerance=min(
                    GRID_TURN_TOLERANCE_DEG,
                    max(
                        0.0,
                        self._grid_cfg_float(
                            CONFIG_GRID_TURN_TOLERANCE,
                            GRID_TURN_TOLERANCE_DEG,
                        ),
                    ),
                ),
                max_rounds=1,
                frame=frame,
                min_score=self._grid_heading_min_score(),
            )
        duration = self._grid_recovery_duration(deadline, multiplier=1.5)
        if duration is None:
            return
        self._set_grid_walking(True)
        try:
            self.press_key("space", down_time=duration)
        finally:
            self._set_grid_walking(False)

    @staticmethod
    def _grid_stuck_recovery_step(attempt: int, total: int) -> tuple[StuckRecovery, str]:
        """返回第 ``attempt`` 次脱困该做哪一步，以及"第几次/共几次"的提示文案。

        下标夹在序列范围内：尝试次数超出序列长度时重复最后一步（对准后跳远），而不是
        越界或悄悄回到第一步。
        """
        index = min(max(1, int(attempt)), len(_GRID_STUCK_RECOVERY)) - 1
        return _GRID_STUCK_RECOVERY[index], f"脱困尝试 {int(attempt)}/{int(total)}"

    def _grid_recovery_duration(
        self,
        deadline: float | None,
        *,
        multiplier: float = 1.0,
    ) -> float | None:
        duration = max(
            0.1,
            self._grid_cfg_float(CONFIG_GRID_RECOVERY_TIME, 0.45) * max(0.1, float(multiplier)),
        )
        if deadline is not None:
            remaining = deadline - self.active_time()
            if remaining <= 0:
                return None
            duration = min(duration, remaining)
        return duration

    def _hold_grid_keys(self, keys: tuple[str, ...], duration: float) -> None:
        self._set_grid_walking(False)
        for key in keys:
            self.send_key_down(key)
        try:
            self.sleep(duration)
        finally:
            for key in reversed(keys):
                self.send_key_up(key)

    def _choose_grid_strafe_side(
        self,
        position: tuple[float, float],
        heading: float,
    ) -> str:
        """选择左右侧可行空间更多的一边，默认优先 D。"""
        follower = self._grid_nav_follower
        if follower is None:
            return "d"
        grid = follower.grid
        planner = follower.planner
        radians = math.radians(float(heading))
        right = (math.cos(radians), -math.sin(radians))
        left = (-right[0], -right[1])
        cell_size = max(0.25, float(grid.meta.cell_size))

        def score(direction: tuple[float, float]) -> int:
            total = 0
            for distance_index in range(1, 6):
                distance = cell_size * distance_index
                i, j = grid.index_of_world(
                    position[0] + direction[0] * distance,
                    position[1] + direction[1] * distance,
                )
                if planner.passable(i, j):
                    total += 6 - distance_index
            return total

        left_score = score(left)
        right_score = score(right)
        chosen = "d" if right_score >= left_score else "a"
        self.log_info(f"脱困侧向选择：左={left_score} 右={right_score}，选择 {chosen.upper()}")
        return chosen

    def _wait_for_grid_position(self, reason: str) -> None:
        self._set_grid_walking(False)
        if self._grid_nav_follower is not None:
            self._grid_nav_follower.pause()
        now = self.active_time()
        if now - self._grid_nav_last_wait_log >= 5.0:
            self._grid_nav_last_wait_log = now
            diagnostic = self.pose_rest_diag() or {}
            self.log_info(
                f"网格导航暂停：{reason}"
                f"（rest_reason={diagnostic.get('reason')} "
                f"speed={diagnostic.get('map_speed_m_s')} "
                f"ws_moved={diagnostic.get('ws_moved_m')}）"
            )
            self.info_set("网格导航", reason)

    def _wait_for_minimap_sync(
        self,
        start_sync_seq: int,
        deadline: float,
        tick: float,
        *,
        reason: str | None = None,
    ) -> bool:
        """停车采样，直到定位服务完成一次静止校准。

        返回 True 包含两种情况：发生了真实重锚（``sync_seq`` 增加），或当前估计
        已与 WS 对齐（``sync_checked``）。超时返回 False，并记录静止判定诊断。

        ``reason`` 是发起这次校准的原因（见 :class:`src.nav.nav_state.SyncReason`），
        只用于状态提示与超时告警，不参与判定。
        """
        self._set_grid_walking(False)
        if self._grid_nav_follower is not None:
            self._grid_nav_follower.pause()
        self.info_set("网格导航", f"等待静止定位校准（{reason}）" if reason else "等待静止定位校准")
        started = self.active_time()
        # WS 真值约每 5 秒推送一次，至少覆盖一个完整推送周期再判定失败。
        timeout = min(8.0, max(0.0, deadline - started))
        while self.active_time() - started < timeout:
            frame = self.next_frame()
            state = self.world_pose(
                frame=frame,
                max_age=self._grid_position_max_age(tick),
            )
            if state is None:
                self.sleep(tick)
                continue
            if int(state.get("sync_seq") or 0) > start_sync_seq or bool(state.get("sync_checked")):
                self.log_info("导航中静止定位校准完成")
                return True
            self.sleep(tick)
        diagnostic = self.pose_rest_diag() or {}
        prefix = f"导航中静止定位校准超时（{reason}）：" if reason else "导航中静止定位校准超时："
        self.log_warning(
            f"{prefix}"
            f"reason={diagnostic.get('reason')} "
            f"speed={diagnostic.get('map_speed_m_s')} "
            f"ws_moved={diagnostic.get('ws_moved_m')}"
        )
        return False

    @staticmethod
    def _grid_position_max_age(tick: float) -> float:
        """按导航控制周期计算可接受的位置快照时效。"""

        return max(0.5, float(tick) * 2.5)

    def _grid_navigation_timed_out(self, deadline: float, limit: float) -> bool:
        if self.active_time() < deadline:
            return False
        self.log_warning(f"导航超时（{limit:.1f}s）", notify=True)
        return True

    @staticmethod
    def _grid_meta_matches_map(grid: DenseGrid, path: Path, map_id: str) -> bool:
        if grid.meta.map_name == map_id:
            return True
        return path.name.startswith(f"{map_id}_")

    @staticmethod
    def _grid_zoom_rank(grid: DenseGrid) -> tuple[int, float, str]:
        zoom = str(grid.meta.zoom or "")
        try:
            return (1, float(zoom), zoom)
        except ValueError:
            return (0, 0.0, zoom)
