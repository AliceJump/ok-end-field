"""网格规划与游戏内导航执行。

网格存 ``0=未知 / 1=可行走 / 2=阻挡``，世界换算为
``world = origin + (i, j) * cell_size``：行沿世界 Z、列沿世界 X。
规划航点因此统一使用 ``(x, z)`` 世界坐标。

本 mixin 只负责编排，不拥有定位源：

- ``MinimapPositionTask`` 提供共享的实时 ``(x, z)``、朝向和可信状态；
- ``GridPlanner`` 在 ``*.grid.npz`` 上做 A*；
- ``GridRouteFollower`` 输出 ``TURN/WALK/WAIT/STUCK/REPLAN/DONE``；
- 本层把动作转换为鼠标、键盘输入，并处理校准、重规划和脱困。

导航主循环的信任顺序不可颠倒：

1. 等绝对坐标锚定；
2. 等 ``position_trusted=True``，重锚后必须先做静止校准；
3. 等本拍有有效里程计样本；
4. 再允许 follower 规划或继续行走。

偏航恢复优先原地校准；若当前位置不属于已知 free 格，则先返回本次实际走过的最近
已知格，校准后再重新规划。
"""

from __future__ import annotations

import math
from pathlib import Path

from src.nav.grid_io import GRID_SUFFIX, DenseGrid, load_grid
from src.nav.grid_planner import PlanResult
from src.nav.route_follower import (
    DONE,
    FAILED,
    REPLAN,
    STUCK,
    TURN,
    WAIT,
    WALK,
    FollowerConfig,
    FollowerStep,
    GridRouteFollower,
)
from src.tasks.mixin.minimap_heading_mixin import (
    CONFIG_MIN_SCORE,
    CONFIG_YAW_PER_PIXEL,
    MinimapHeadingMixin,
)
from src.tasks.mixin.minimap_position_mixin import MinimapPositionMixin
from src.tasks.trigger.MinimapPositionTask import MinimapPositionTask

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
    "CONFIG_GRID_WALL_PENALTY",
    "CONFIG_GRID_WAYPOINT_RADIUS",
    "CONFIG_GRID_WAYPOINT_TOLERANCE",
    "CONFIG_GRID_ZOOM",
    "GridNavigationMixin",
]

CONFIG_GRID_DIR = "网格目录"
CONFIG_GRID_FILE = "网格文件(可选)"
CONFIG_GRID_ZOOM = "网格Zoom(可选)"
CONFIG_GRID_CALIBRATION_WAYPOINTS = "航点校准间隔(个)"
CONFIG_GRID_FRONTIER_MARGIN = "未知边缘安全距离(格)"
CONFIG_GRID_FRONTIER_PENALTY = "未知边缘不足代价(每格)"
CONFIG_GRID_GOAL_RADIUS = "到达目标半径(米)"
CONFIG_GRID_WAYPOINT_RADIUS = "航点到达半径(米)"
CONFIG_GRID_HEADING_TOLERANCE = "行走朝向容差(度)"
CONFIG_GRID_TURN_TOLERANCE = "转向到位容差(度)"
CONFIG_GRID_MAX_TURN_ROUNDS = "最大转向轮数"
CONFIG_GRID_TURN_WHILE_MOVING = "航点移动转向"
CONFIG_GRID_MOVING_TURN_GAIN = "移动转向增益"
CONFIG_GRID_MOVING_TURN_MAX_DEG = "移动转向单拍最大角度(度)"
CONFIG_GRID_MOVING_TURN_MAX_START_DEG = "移动转向最大启用角度(度)"
CONFIG_GRID_MOVING_TURN_MIN_DISTANCE = "移动转向最小目标距离(米)"
CONFIG_GRID_MARGIN = "离墙安全边距(格)"
CONFIG_GRID_WALL_PENALTY = "离墙不足代价(每格)"
CONFIG_GRID_WAYPOINT_TOLERANCE = "航点简化容差(米)"
CONFIG_GRID_MAX_EXPAND = "搜索节点上限"
CONFIG_GRID_ALLOW_UNKNOWN = "允许穿越未知格"
CONFIG_GRID_RISK_COST = "未知格风险代价"
CONFIG_GRID_SHORTCUT_RADIUS = "路径点捷径半径(米)"
CONFIG_GRID_STUCK_WINDOW = "卡住判定时间(秒)"
CONFIG_GRID_STUCK_DISTANCE = "卡住判定位移(米)"
CONFIG_GRID_MAX_RECOVERIES = "卡住脱困次数"
CONFIG_GRID_RECOVERY_TIME = "脱困按键时长(秒)"
CONFIG_GRID_MAX_REPLANS = "最大重规划次数"
CONFIG_GRID_TIMEOUT = "导航超时(秒)"
CONFIG_GRID_TICK = "控制周期(秒)"


class GridNavigationMixin(MinimapHeadingMixin):
    """在 ``*.grid.npz`` 上规划并驱动角色前往世界坐标 ``(x, z)``。

    定位由共享的 ``MinimapPositionTask`` 提供；本类只消费状态、执行动作和维护本次
    导航的重规划/脱困/校准计数。各动作的纯决策逻辑在 ``GridRouteFollower``。
    """

    @staticmethod
    def grid_navigation_default_config() -> dict:
        """返回网格导航配置的默认值。"""
        return {
            # 旧定位配置暂时保留，供 MinimapPositionTask 首次启动时迁移。
            **MinimapPositionMixin.minimap_position_default_config(),
            CONFIG_GRID_DIR: "assets/nav",
            CONFIG_GRID_FILE: "",
            CONFIG_GRID_ZOOM: "",
            CONFIG_GRID_CALIBRATION_WAYPOINTS: 5,
            CONFIG_GRID_FRONTIER_MARGIN: 2,
            CONFIG_GRID_FRONTIER_PENALTY: 1.0,
            CONFIG_GRID_WAYPOINT_TOLERANCE: 2.0,
            CONFIG_GRID_MAX_EXPAND: 400_000,
            CONFIG_GRID_GOAL_RADIUS: 2.0,
            CONFIG_GRID_WAYPOINT_RADIUS: 1.0,
            CONFIG_GRID_HEADING_TOLERANCE: 8.0,
            CONFIG_GRID_TURN_TOLERANCE: 5.0,
            CONFIG_GRID_MAX_TURN_ROUNDS: 2,
            CONFIG_GRID_TURN_WHILE_MOVING: True,
            CONFIG_GRID_MOVING_TURN_GAIN: 0.8,
            CONFIG_GRID_MOVING_TURN_MAX_DEG: 25.0,
            CONFIG_GRID_MOVING_TURN_MAX_START_DEG: 45.0,
            CONFIG_GRID_MOVING_TURN_MIN_DISTANCE: 5.0,
            CONFIG_GRID_MARGIN: 1,
            CONFIG_GRID_WALL_PENALTY: 1.0,
            CONFIG_GRID_ALLOW_UNKNOWN: False,
            CONFIG_GRID_RISK_COST: 5.0,
            CONFIG_GRID_SHORTCUT_RADIUS: 1.0,
            CONFIG_GRID_STUCK_WINDOW: 2.5,
            CONFIG_GRID_STUCK_DISTANCE: 0.35,
            CONFIG_GRID_MAX_RECOVERIES: 3,
            CONFIG_GRID_RECOVERY_TIME: 0.45,
            CONFIG_GRID_MAX_REPLANS: 8,
            CONFIG_GRID_TIMEOUT: 180.0,
            CONFIG_GRID_TICK: 0.2,
        }

    @staticmethod
    def grid_navigation_config_description() -> dict:
        """返回导航配置键的用户说明。"""
        return {
            **MinimapPositionMixin.minimap_position_config_description(),
            CONFIG_GRID_DIR: "导航网格目录，默认 assets/nav",
            CONFIG_GRID_FILE: "可选。直接指定 *.grid.npz；填写后不再按地图 id 查找",
            CONFIG_GRID_ZOOM: "可选。地图存在多个 zoom 网格时指定，例如 4",
            CONFIG_GRID_CALIBRATION_WAYPOINTS: "每经过多少个航点暂停一次，等待小地图静止自动校准；0=关闭",
            CONFIG_GRID_FRONTIER_MARGIN: "已知自由格期望远离未知边缘的距离（格）",
            CONFIG_GRID_FRONTIER_PENALTY: "自由格距未知边缘每缺一格增加的代价，减少贴着未探索区域边缘行走",
            CONFIG_GRID_WAYPOINT_TOLERANCE: "规划后允许合并航点的最大横向误差；越大航点越少",
            CONFIG_GRID_MAX_EXPAND: "A* 扩展节点数上限。触顶以『搜索规模超限』失败（与真的不可达区分），"
                                    "大图或未探索图上需调大",
            CONFIG_GRID_GOAL_RADIUS: "距最终目标小于该值即判定到达（世界 XZ 平面，米）",
            CONFIG_GRID_WAYPOINT_RADIUS: "距中间航点小于该值即切到下一个航点（米）",
            CONFIG_GRID_HEADING_TOLERANCE: "朝向误差小于该值才持续按 W，否则先转向",
            CONFIG_GRID_TURN_TOLERANCE: "闭环转向要求达到的方位误差（度）",
            CONFIG_GRID_MAX_TURN_ROUNDS: "每个目标方位最多转几轮；每轮会按一次 W 让角色转身",
            CONFIG_GRID_TURN_WHILE_MOVING: "到达航点后保持 W 前进并连续转向，不再先停车转向",
            CONFIG_GRID_MOVING_TURN_GAIN: "移动转向每拍使用的角度残差比例，过大可能画弧过弯",
            CONFIG_GRID_MOVING_TURN_MAX_DEG: "移动转向每拍最多修正的角度",
            CONFIG_GRID_MOVING_TURN_MAX_START_DEG: "朝向误差超过该角度时禁止按住 W 画弧，改为停车转向",
            CONFIG_GRID_MOVING_TURN_MIN_DISTANCE: "距目标航点小于该距离时禁止移动转向，避免弧线越过近航点",
            CONFIG_GRID_MARGIN: "期望离墙距离（格）。不足时只增加规划代价，不会封死窄路；0=关闭偏好",
            CONFIG_GRID_WALL_PENALTY: "离墙距离每缺一格增加的代价；越大越偏向安全路线",
            CONFIG_GRID_ALLOW_UNKNOWN: "是否允许穿越未知格。默认关闭：未知格视同阻挡格，只在已知可行走区内寻路"
                                       "（找不到路时会明确提示是该开关导致，而不是数据坏了）。"
                                       "只有在这张图已充分探索、且你确实要冒险走未探明区域时才打开",
            CONFIG_GRID_RISK_COST: "穿越未知格相对可行走格的代价倍数",
            CONFIG_GRID_SHORTCUT_RADIUS: "当前位置落到下一段航点路径附近该距离内时，跳过当前航点直接前往下一点",
            CONFIG_GRID_STUCK_WINDOW: "持续行走该时长但位移不足，判定卡住并重规划",
            CONFIG_GRID_STUCK_DISTANCE: "卡住判定时间窗内要求的最小位移（米）",
            CONFIG_GRID_MAX_RECOVERIES: "连续卡住时可尝试脱困的最多次数",
            CONFIG_GRID_RECOVERY_TIME: "每次脱困时 S/A/D 各按下的时长（秒）",
            CONFIG_GRID_MAX_REPLANS: "未取得新进展时允许的最大重规划次数",
            CONFIG_GRID_TIMEOUT: "整次导航的最长运行时间（秒）",
            CONFIG_GRID_TICK: "控制循环固定节拍（秒）",
        }

    def _init_grid_navigation_mixin(self) -> None:
        """Initialize navigation state without owning minimap position sources."""
        self._init_minimap_heading_mixin()
        self._grid_nav_follower: GridRouteFollower | None = None
        self._grid_nav_map_id = ""
        self._grid_nav_w_held = False
        self._grid_nav_last_wait_log = 0.0
        self._grid_minimap_position_service: MinimapPositionTask | None = None
        self._grid_nav_last_info_key = None
        self._grid_nav_last_info_at = 0.0
        self._grid_nav_last_debug_key = None
        self._grid_nav_last_debug_at = 0.0

    def load_grid_for_map(
        self,
        map_id: str = "",
        *,
        grid_path: str | None = None,
        grid_dir: str | None = None,
        zoom: str | None = None,
    ) -> DenseGrid | None:
        """Load the best matching ``*.grid.npz`` for the current map id.

        An explicit ``grid_path`` wins. Otherwise files named
        ``<map_id>_<zoom>.grid.npz`` are preferred, with metadata used to verify
        the map name. If no map id is available, a unique/latest grid is accepted.
        """
        explicit = self.config.get(CONFIG_GRID_FILE, "") if grid_path is None else grid_path
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

        directory = str(
            self.config.get(CONFIG_GRID_DIR, "assets/nav") if grid_dir is None else grid_dir
        ).strip()
        requested_zoom = str(
            self.config.get(CONFIG_GRID_ZOOM, "") if zoom is None else zoom
        ).strip()
        root = Path(directory)
        if not root.is_dir():
            self.log_warning(f"导航网格目录不存在: {root}", notify=True)
            return None

        map_id = str(map_id or "").strip()
        candidates = sorted(root.glob(f"{map_id}_*{GRID_SUFFIX}")) if map_id else []
        if not candidates:
            candidates = sorted(root.glob(f"*{GRID_SUFFIX}"))

        matches: list[tuple[DenseGrid, Path]] = []
        for path in candidates:
            try:
                grid = load_grid(path)
            except (OSError, TypeError, ValueError) as exc:
                self.log_warning(f"忽略无法读取的导航网格 {path}: {exc}")
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
        self.log_info(
            f"已加载导航网格 {path}：{grid.counts()}，cell_size={grid.meta.cell_size}"
        )
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
    ) -> bool:
        """规划并执行到世界坐标 ``(x, z)`` 的路线。

        只有融合定位进入最终 ``goal_radius`` 才返回 True。任何退出路径都会松开
        ``W``。循环内按以下优先级处理：

        - 坐标未锚定、定位不可信、没有有效里程计样本时停车等待；
        - 累计移动或到达航点触发周期校准时停车等待静止校准；
        - 偏航时根据当前位置决定原地校准或返回最近走过的已知格；
        - 卡住时按后退、侧移、跳跃的顺序脱困；
        - 其余动作交给 ``GridRouteFollower`` 产生。
        """
        goal = (float(goal_xz[0]), float(goal_xz[1]))
        limit = self._cfg_float(CONFIG_GRID_TIMEOUT, 180.0) if timeout is None else float(timeout)
        limit = max(1.0, limit)
        tick = max(0.05, self._cfg_float(CONFIG_GRID_TICK, 0.2))
        position_service = self._get_minimap_position_service()
        if position_service is None:
            self.log_warning("导航需要「小地图定位」触发任务，但当前任务未注册", notify=True)
            return False
        if not getattr(position_service, "enabled", True):
            self.log_warning("导航需要启用「小地图定位」触发任务", notify=True)
            return False
        position_service.start_minimap_position(wait_stable=False)
        started_at = self.active_time()
        deadline = started_at + limit
        best_goal_distance = math.inf
        recovery_attempts = 0
        replans = 0
        had_plan = False
        current_map = str(map_id or "")
        next_sync_retry_at = 0.0
        waypoints_since_calibration = 0
        recovery_goal: tuple[float, float] | None = None
        # 本次导航实际走过的已知格。只用于偏航后寻找安全返回点；每次 WS 重锚后
        # 清空，因为历史坐标可能带着重锚前的定位误差。
        visited_cells: dict[tuple[int, int], tuple[float, float]] = {}
        last_sync_seq = 0

        self._grid_nav_follower = None
        self._set_grid_walking(False)

        try:
            while not self._grid_navigation_timed_out(deadline, limit):
                frame = self.next_frame()
                state = position_service.minimap_position(frame=frame, now=self.active_time())
                x = state.get("x")
                z = state.get("z")
                actual_map = str(state.get("map_id") or current_map or "")
                self._log_grid_navigation_debug(state, map_id=actual_map, tag="导航")
                sync_seq = int(state.get("sync_seq") or 0)
                if sync_seq != last_sync_seq:
                    # 真实静校准会整体平移世界坐标，旧的历史格不再是可靠的返回目标。
                    visited_cells.clear()
                    last_sync_seq = sync_seq
                if x is None or z is None:
                    self._wait_for_grid_position("等待融合坐标锚定")
                    self.sleep(tick)
                    continue
                if not state.get("position_trusted", True):
                    # 重锚后里程计“有位移样本”不等于绝对坐标可信。必须先停车，
                    # 等 WS 静校准恢复 position_trusted，再允许重新规划。
                    self._wait_for_grid_position(
                        f"等待定位重新校准: {state.get('trust_reason') or 'untrusted'}"
                    )
                    if state.get("rest") and self.active_time() >= next_sync_retry_at:
                        synced = self._wait_for_minimap_sync(
                            position_service,
                            sync_seq,
                            deadline,
                            tick,
                        )
                        if self._grid_navigation_timed_out(deadline, limit):
                            return False
                        if not synced:
                            next_sync_retry_at = self.active_time() + 30.0
                    self.sleep(tick)
                    continue
                if not state.get("odom_ok"):
                    self._wait_for_grid_position(f"等待有效位移样本: {state.get('odom_reason')}")
                    self.sleep(tick)
                    continue
                if state.get("sync_needed") and self.active_time() >= next_sync_retry_at:
                    synced = self._wait_for_minimap_sync(
                        position_service,
                        int(state.get("sync_seq") or 0),
                        deadline,
                        tick,
                    )
                    if self._grid_navigation_timed_out(deadline, limit):
                        return False
                    if synced:
                        waypoints_since_calibration = 0
                    if not synced:
                        next_sync_retry_at = self.active_time() + 30.0
                    continue
                heading = state.get("heading")
                heading_score = state.get("heading_score")
                min_score = max(0.0, min(1.0, self._cfg_float(CONFIG_MIN_SCORE, 0.6)))
                if heading_score is None or float(heading_score) < min_score:
                    heading = None
                if heading is None:
                    self._wait_for_grid_position(f"等待可靠朝向: score={heading_score}")
                    self.sleep(tick)
                    continue

                if self._grid_nav_follower is None or actual_map != self._grid_nav_map_id:
                    map_changed = (
                        self._grid_nav_follower is not None
                        and actual_map != self._grid_nav_map_id
                    )
                    if had_plan:
                        if replans >= self._cfg_int(CONFIG_GRID_MAX_REPLANS, 8):
                            self.log_warning("导航重规划次数已达上限", notify=True)
                            return False
                        replans += 1
                    if map_changed:
                        self.log_info(f"地图已切换 {self._grid_nav_map_id!r} -> {actual_map!r}，重新规划")
                        visited_cells.clear()
                        recovery_goal = None
                    route_goal = recovery_goal if recovery_goal is not None else goal
                    follower, result = self._create_grid_route(
                        (float(x), float(z)),
                        route_goal,
                        map_id=actual_map,
                        grid_path=grid_path,
                        grid_dir=grid_dir,
                        zoom=zoom,
                        time_budget_s=max(0.0, deadline - self.active_time()),
                    )
                    had_plan = True
                    if self._grid_navigation_timed_out(deadline, limit):
                        return False
                    if follower is None or not result.ok:
                        self._log_grid_plan_failure(result, "导航规划失败")
                        return False
                    self._grid_nav_follower = follower
                    self._record_grid_visited_cell(
                        follower,
                        (float(x), float(z)),
                        visited_cells,
                    )
                    self._grid_nav_map_id = actual_map
                    self._log_grid_plan(result)

                self._record_grid_visited_cell(
                    self._grid_nav_follower,
                    (float(x), float(z)),
                    visited_cells,
                )
                step = self._grid_nav_follower.update(
                    (float(x), float(z)),
                    float(heading),
                    self.active_time(),
                )
                if step.skipped_waypoints:
                    skipped = ", ".join(str(index + 1) for index in step.skipped_waypoints)
                    self.log_info(
                        f"路径点捷径：当前位置距下一段 {step.shortcut_distance:.2f}m，"
                        f"跳过航点 {skipped}，直接前往航点 {step.waypoint_index + 1}"
                    )
                if step.action == REPLAN:
                    self._set_grid_walking(False)
                    self._grid_nav_follower.pause()
                    self.log_warning(f"路径偏离，重新规划：{step.reason}")
                    current_known = self._grid_position_is_known(
                        self._grid_nav_follower,
                        (float(x), float(z)),
                    )
                    recovery_target = None
                    if not current_known:
                        recovery_target = self._nearest_visited_grid_cell(
                            (float(x), float(z)),
                            visited_cells,
                        )
                    self._grid_nav_follower = None
                    if current_known:
                        # 人仍站在已知 free 格：优先原地静止校准，消除定位偏差后
                        # 再从可信位置重新规划。
                        recovery_goal = None
                        synced = self._wait_for_minimap_sync(
                            position_service,
                            int(state.get("sync_seq") or 0),
                            deadline,
                            tick,
                        )
                        if self._grid_navigation_timed_out(deadline, limit):
                            return False
                        if not synced:
                            self.log_warning("偏航后的静止校准未完成，继续重规划")
                    elif recovery_target is not None:
                        # 已偏离已知区域：先回到本次实际走过的格，不能直接相信
                        # 当前坐标去规划一条可能穿墙的新路线。
                        recovery_goal = recovery_target
                        self.log_info(
                            "当前位置不在已知 free 格，先导航回最近走过的已知格再校准"
                        )
                    else:
                        recovery_goal = None
                        self.log_warning(
                            "当前位置不在已知 free 格，但没有已走过的已知格可返回；"
                            "先原地校准后重新规划"
                        )
                        synced = self._wait_for_minimap_sync(
                            position_service,
                            int(state.get("sync_seq") or 0),
                            deadline,
                            tick,
                        )
                        if self._grid_navigation_timed_out(deadline, limit):
                            return False
                        if not synced:
                            self.log_warning("偏航后的静止校准未完成，继续重规划")
                    if self._grid_navigation_timed_out(deadline, limit):
                        return False
                    continue
                if step.arrived_waypoint_index is not None:
                    waypoints_since_calibration += 1
                    calibration_interval = max(
                        0,
                        self._cfg_int(CONFIG_GRID_CALIBRATION_WAYPOINTS, 5),
                    )
                    if (
                        calibration_interval > 0
                        and waypoints_since_calibration >= calibration_interval
                    ):
                        # 航点间隔校准是主动停车点；WS 周期约 5 秒，等待函数会
                        # 在停车期间持续采样，直到完成真实重锚或确认已对齐。
                        synced = self._wait_for_minimap_sync(
                            position_service,
                            int(state.get("sync_seq") or 0),
                            deadline,
                            tick,
                        )
                        if self._grid_navigation_timed_out(deadline, limit):
                            return False
                        waypoints_since_calibration = 0
                        if not synced:
                            self.log_warning("到达航点后的静止校准未完成，继续导航")
                    self.log_info(
                        f"到达航点 {step.arrived_waypoint_index + 1}/"
                        f"{step.waypoint_count}，累计未校准航点={waypoints_since_calibration}"
                    )
                    continue
                if step.distance_to_goal is not None:
                    if step.distance_to_goal + 0.5 < best_goal_distance:
                        best_goal_distance = step.distance_to_goal
                        replans = 0
                        recovery_attempts = 0
                    info_key = (
                        step.action,
                        step.waypoint_index,
                        round(step.distance_to_goal, 0),
                    )
                    now = self.active_time()
                    if (
                        info_key != self._grid_nav_last_info_key
                        or now - self._grid_nav_last_info_at >= 5.0
                    ):
                        self.info_set(
                            "网格导航",
                            f"{step.action} | 目标距离={step.distance_to_goal:.2f}m | "
                            f"航点={step.waypoint_index + 1}/{step.waypoint_count}",
                        )
                        self._grid_nav_last_info_key = info_key
                        self._grid_nav_last_info_at = now

                if step.action == DONE:
                    self._set_grid_walking(False)
                    if recovery_goal is not None:
                        synced = self._wait_for_minimap_sync(
                            position_service,
                            int(state.get("sync_seq") or 0),
                            deadline,
                            tick,
                        )
                        if self._grid_navigation_timed_out(deadline, limit):
                            return False
                        recovery_goal = None
                        self._grid_nav_follower = None
                        if synced:
                            self.log_info("已返回最近走过的已知格并完成校准，继续原目标")
                        else:
                            self.log_warning("返回已知格后校准未完成，继续原目标")
                        continue
                    distance_text = (
                        f"{step.distance_to_goal:.2f}m"
                        if step.distance_to_goal is not None else "未知"
                    )
                    self.log_info(f"已到达目标（距目标 {distance_text}）", notify=True)
                    return True
                if step.action == STUCK:
                    self._set_grid_walking(False)
                    if recovery_attempts >= self._cfg_int(CONFIG_GRID_MAX_RECOVERIES, 3):
                        self.log_warning(f"导航卡住: {step.reason}", notify=True)
                        return False
                    recovery_attempts += 1
                    self.log_warning(
                        f"导航卡住，尝试脱困 {recovery_attempts}/"
                        f"{self._cfg_int(CONFIG_GRID_MAX_RECOVERIES, 3)}：{step.reason}"
                    )
                    self._recover_grid_stuck(
                        position=(float(x), float(z)),
                        heading=float(heading),
                        target_bearing=step.target_bearing,
                        frame=frame,
                        attempt=recovery_attempts,
                        deadline=deadline,
                    )
                    if self._grid_navigation_timed_out(deadline, limit):
                        return False
                    self._grid_nav_follower = None
                    continue
                if step.action == TURN:
                    if (
                        self._cfg_bool(CONFIG_GRID_TURN_WHILE_MOVING, True)
                        and not self._should_turn_grid_in_place(step)
                    ):
                        self._turn_grid_while_moving(step)
                    else:
                        self._set_grid_walking(False)
                        turn = self.turn_to_bearing(
                            step.target_bearing,
                            tolerance=self._cfg_float(CONFIG_GRID_TURN_TOLERANCE, 5.0),
                            max_rounds=max(1, self._cfg_int(CONFIG_GRID_MAX_TURN_ROUNDS, 2)),
                            frame=frame,
                        )
                        if not turn.get("ok"):
                            self.log_warning(
                                f"转向未到位：目标={step.target_bearing:.1f}°，"
                                f"实测={turn.get('heading')}，误差={turn.get('error')}"
                            )
                    if self._grid_navigation_timed_out(deadline, limit):
                        return False
                    self.sleep(tick)
                    continue
                if step.action == WALK:
                    self._set_grid_walking(True)
                elif step.action == WAIT:
                    self._set_grid_walking(False)
                elif step.action == FAILED:
                    self._set_grid_walking(False)
                    self.log_warning(f"导航执行失败: {step.reason}", notify=True)
                    return False

                self.sleep(tick)

            return False
        finally:
            self._set_grid_walking(False)

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
    ) -> tuple[GridRouteFollower | None, PlanResult]:
        grid = self.load_grid_for_map(
            map_id,
            grid_path=grid_path,
            grid_dir=grid_dir,
            zoom=zoom,
        )
        if grid is None:
            return None, PlanResult(False, f"未找到地图 {map_id!r} 的导航网格")
        follower = GridRouteFollower(grid, self._grid_follower_config())
        result = follower.plan(start_xz, goal_xz, time_budget_s=time_budget_s)
        return follower, result

    def _get_minimap_position_service(self) -> MinimapPositionTask | None:
        if self._grid_minimap_position_service is not None:
            return self._grid_minimap_position_service
        override = getattr(self, "_minimap_position_service", None)
        if override is not None:
            self._grid_minimap_position_service = override
            return override
        getter = getattr(self, "get_task_by_class", None)
        if callable(getter):
            self._grid_minimap_position_service = getter(MinimapPositionTask)
        return self._grid_minimap_position_service

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
        return FollowerConfig(
            arrive_radius=max(0.0, self._cfg_float(CONFIG_GRID_WAYPOINT_RADIUS, 1.0)),
            goal_radius=max(0.0, self._cfg_float(CONFIG_GRID_GOAL_RADIUS, 2.0)),
            heading_tolerance=max(0.0, self._cfg_float(CONFIG_GRID_HEADING_TOLERANCE, 8.0)),
            stuck_window_s=max(0.1, self._cfg_float(CONFIG_GRID_STUCK_WINDOW, 2.5)),
            stuck_min_distance=max(0.0, self._cfg_float(CONFIG_GRID_STUCK_DISTANCE, 0.35)),
            risk_cost=max(0.1, self._cfg_float(CONFIG_GRID_RISK_COST, 5.0)),
            shortcut_radius=max(0.0, self._cfg_float(CONFIG_GRID_SHORTCUT_RADIUS, 1.0)),
            allow_unknown=self.allow_grid_unknown(),
            margin=max(0, self._cfg_int(CONFIG_GRID_MARGIN, 1)),
            wall_penalty=max(0.0, self._cfg_float(CONFIG_GRID_WALL_PENALTY, 1.0)),
            frontier_margin=max(0, self._cfg_int(CONFIG_GRID_FRONTIER_MARGIN, 2)),
            frontier_penalty=max(0.0, self._cfg_float(CONFIG_GRID_FRONTIER_PENALTY, 1.0)),
            waypoint_tolerance=max(0.0, self._cfg_float(CONFIG_GRID_WAYPOINT_TOLERANCE, 2.0)),
            max_expand=max(1, self._cfg_int(CONFIG_GRID_MAX_EXPAND, 400_000)),
        )

    def _log_grid_plan(self, result: PlanResult) -> None:
        self.log_info(
            f"规划完成：{len(result.cells)} 格 / {len(result.waypoints)} 航点，"
            f"代价={result.cost:.1f}，未知格={result.risk_cells}"
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
            self.log_warning(
                f"{prefix}诊断: 起点可达区域已穷尽（共 {result.expanded} 格），不是搜索深度不够")
        if not self.allow_grid_unknown():
            self.log_warning(
                f"{prefix}诊断: 当前「{CONFIG_GRID_ALLOW_UNKNOWN}」关闭，只能在已知可行走区内搜索")
        for note in result.notes:
            self.log_warning(f"{prefix}诊断: {note}")

    def allow_grid_unknown(self) -> bool:
        """当前是否允许穿越未知格（用任务配置，不是库默认）。"""
        return self._cfg_bool(CONFIG_GRID_ALLOW_UNKNOWN, True)

    def _set_grid_walking(self, held: bool) -> None:
        """Hold/release W using optimistic state because ok-script returns no key-down result."""
        held = bool(held)
        if held == self._grid_nav_w_held:
            return
        if held:
            self.send_key_down("w")
        else:
            self.send_key_up("w")
        self._grid_nav_w_held = held

    def _should_turn_grid_in_place(self, step: FollowerStep) -> bool:
        """Large turns and nearby waypoints require a stationary turn."""
        heading_error = step.heading_error
        if heading_error is not None:
            max_start = max(
                1.0,
                self._cfg_float(CONFIG_GRID_MOVING_TURN_MAX_START_DEG, 45.0),
            )
            if abs(float(heading_error)) > max_start:
                self.log_info(
                    f"转向角 {abs(float(heading_error)):.1f}° > {max_start:.1f}°，改为停车转向"
                )
                return True
        distance = step.distance_to_waypoint
        if distance is not None:
            min_distance = max(
                0.0,
                self._cfg_float(CONFIG_GRID_MOVING_TURN_MIN_DISTANCE, 5.0),
            )
            if float(distance) < min_distance:
                self.log_info(
                    f"距目标航点 {float(distance):.2f}m < {min_distance:.2f}m，改为停车转向"
                )
                return True
        return False

    def _turn_grid_while_moving(self, step: FollowerStep) -> None:
        """Rotate toward the next waypoint while keeping W held for a curved turn."""
        heading_error = step.heading_error
        if heading_error is None:
            return
        if not self._can_turn():
            self._set_grid_walking(False)
            return
        per_px = self.yaw_per_pixel()
        if per_px <= 0:
            self.log_warning(f"{CONFIG_YAW_PER_PIXEL} 无效，无法移动转向")
            self._set_grid_walking(False)
            return
        gain = max(0.1, min(1.0, self._cfg_float(CONFIG_GRID_MOVING_TURN_GAIN, 0.8)))
        max_angle = max(1.0, self._cfg_float(CONFIG_GRID_MOVING_TURN_MAX_DEG, 25.0))
        delta = max(-max_angle, min(max_angle, float(heading_error) * gain))
        dx = round(delta / per_px)
        if dx == 0:
            return
        self._set_grid_walking(True)
        self._send_rotation(dx)

    def _log_grid_navigation_debug(self, state: dict, *, map_id: str, tag: str) -> None:
        """Emit one detailed positioning line per frame when debug mode is enabled."""
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
        if (
            debug_key == self._grid_nav_last_debug_key
            and now - self._grid_nav_last_debug_at < 1.0
        ):
            return
        self._grid_nav_last_debug_key = debug_key
        self._grid_nav_last_debug_at = now
        x, z = state.get("x"), state.get("z")
        pos_text = (
            f"({float(x):.3f}, {float(z):.3f})"
            if x is not None and z is not None
            else "未锚定"
        )
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
    ) -> None:
        """三段式脱困：后退、侧向绕开、对准目标后跳跃。"""
        duration = self._grid_recovery_duration(deadline)
        if duration is None:
            return
        if attempt <= 1:
            self.log_info("脱困尝试 1/3：按 S 后退")
            self.press_key("s", down_time=duration)
            return
        if attempt == 2:
            side = self._choose_grid_strafe_side(position, heading)
            side_name = "A 左移" if side == "a" else "D 右移"
            self.log_info(f"脱困尝试 2/3：按 S + {side_name}")
            duration = self._grid_recovery_duration(deadline)
            if duration is not None:
                self._hold_grid_keys(("s", side), duration)
            return

        self.log_info("脱困尝试 3/3：对准目标后按住 W + Space 跳跃")
        if target_bearing is not None:
            self.turn_to_bearing(
                target_bearing,
                tolerance=self._cfg_float(CONFIG_GRID_TURN_TOLERANCE, 5.0),
                max_rounds=1,
                frame=frame,
            )
        duration = self._grid_recovery_duration(deadline, multiplier=1.5)
        if duration is None:
            return
        self._set_grid_walking(True)
        try:
            self.press_key("space", down_time=duration)
        finally:
            self._set_grid_walking(False)

    def _grid_recovery_duration(
        self,
        deadline: float | None,
        *,
        multiplier: float = 1.0,
    ) -> float | None:
        duration = max(
            0.1,
            self._cfg_float(CONFIG_GRID_RECOVERY_TIME, 0.45) * max(0.1, float(multiplier)),
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
        right = (math.cos(radians), math.sin(radians))
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
        self.log_info(
            f"脱困侧向选择：左={left_score} 右={right_score}，选择 {chosen.upper()}"
        )
        return chosen

    def _wait_for_grid_position(self, reason: str) -> None:
        self._set_grid_walking(False)
        if self._grid_nav_follower is not None:
            self._grid_nav_follower.pause()
        now = self.active_time()
        if now - self._grid_nav_last_wait_log >= 5.0:
            self._grid_nav_last_wait_log = now
            self.log_info(f"网格导航暂停：{reason}")
            self.info_set("网格导航", reason)

    def _wait_for_minimap_sync(
        self,
        position_service,
        start_sync_seq: int,
        deadline: float,
        tick: float,
    ) -> bool:
        """停车采样，直到定位服务完成一次静止校准。

        返回 True 包含两种情况：发生了真实重锚（``sync_seq`` 增加），或当前估计
        已与 WS 对齐（``sync_checked``）。超时返回 False，并记录静止判定诊断。
        """
        self._set_grid_walking(False)
        if self._grid_nav_follower is not None:
            self._grid_nav_follower.pause()
        self.info_set("网格导航", "等待静止定位校准")
        started = self.active_time()
        # WS 真值约每 5 秒推送一次，至少覆盖一个完整推送周期再判定失败。
        timeout = min(8.0, max(0.0, deadline - started))
        while self.active_time() - started < timeout:
            frame = self.next_frame()
            state = position_service.minimap_position(frame=frame, now=self.active_time())
            if (
                int(state.get("sync_seq") or 0) > start_sync_seq
                or bool(state.get("sync_checked"))
            ):
                self.log_info("导航中静止定位校准完成")
                return True
            self.sleep(tick)
        diagnostic = getattr(position_service, "minimap_rest_diag", lambda: None)() or {}
        self.log_warning(
            "导航中静止定位校准超时："
            f"reason={diagnostic.get('reason')} "
            f"speed={diagnostic.get('map_speed_m_s')} "
            f"ws_moved={diagnostic.get('ws_moved_m')}"
        )
        return False

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
