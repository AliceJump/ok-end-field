"""Grid path planning and in-game route execution.

The grid stores ``0=未知 / 1=可行走 / 2=阻挡``. Its world mapping is
``world = origin + (i, j) * cell_size``: rows advance along world Z and columns
advance along world X. Planned waypoints are therefore ``(x, z)`` cell centers.

This mixin joins three existing pieces:

- ``MinimapPositionMixin`` provides the live world ``(x, z)`` and compass heading;
- ``GridPlanner`` provides an A* route over ``*.grid.npz``;
- ``GridRouteFollower`` turns the route into turn/walk/wait/stuck decisions.

The game-facing layer only executes those decisions through mouse/keyboard input.
"""

from __future__ import annotations

import math
from pathlib import Path

from src.nav.grid_io import GRID_SUFFIX, DenseGrid, load_grid
from src.nav.grid_planner import PlanResult
from src.nav.route_follower import (
    DONE,
    FAILED,
    STUCK,
    TURN,
    WAIT,
    WALK,
    FollowerConfig,
    FollowerStep,
    GridRouteFollower,
)
from src.tasks.mixin.minimap_heading_mixin import CONFIG_MIN_SCORE, CONFIG_YAW_PER_PIXEL
from src.tasks.mixin.minimap_position_mixin import MinimapPositionMixin

__all__ = [
    "CONFIG_GRID_ALLOW_UNKNOWN",
    "CONFIG_GRID_CALIBRATION_DISTANCE",
    "CONFIG_GRID_CALIBRATION_SETTLE",
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
    "CONFIG_GRID_WAYPOINT_CALIBRATION",
    "CONFIG_GRID_WAYPOINT_RADIUS",
    "CONFIG_GRID_WAYPOINT_TOLERANCE",
    "CONFIG_GRID_ZOOM",
    "GridNavigationMixin",
]

CONFIG_GRID_DIR = "网格目录"
CONFIG_GRID_FILE = "网格文件(可选)"
CONFIG_GRID_ZOOM = "网格Zoom(可选)"
CONFIG_GRID_CALIBRATION_DISTANCE = "航点校准最小距离(米)"
CONFIG_GRID_CALIBRATION_SETTLE = "锚定前静止确认(秒)"
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
CONFIG_GRID_WAYPOINT_CALIBRATION = "航点校准停留(秒)"


class GridNavigationMixin(MinimapPositionMixin):
    """Plan on ``*.grid.npz`` and drive the character toward a world ``(x, z)``."""

    @staticmethod
    def grid_navigation_default_config() -> dict:
        return {
            **MinimapPositionMixin.minimap_position_default_config(),
            CONFIG_GRID_DIR: "assets/nav",
            CONFIG_GRID_FILE: "",
            CONFIG_GRID_ZOOM: "",
            CONFIG_GRID_CALIBRATION_DISTANCE: 100.0,
            CONFIG_GRID_CALIBRATION_SETTLE: 1.0,
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
            CONFIG_GRID_WAYPOINT_CALIBRATION: 3.0,
        }

    @staticmethod
    def grid_navigation_config_description() -> dict:
        return {
            **MinimapPositionMixin.minimap_position_config_description(),
            CONFIG_GRID_DIR: "导航网格目录，默认 assets/nav",
            CONFIG_GRID_FILE: "可选。直接指定 *.grid.npz；填写后不再按地图 id 查找",
            CONFIG_GRID_ZOOM: "可选。地图存在多个 zoom 网格时指定，例如 4",
            CONFIG_GRID_CALIBRATION_DISTANCE: "累计行走该距离后，在下一个到达的中间航点停留校准；未达到则跳过航点",
            CONFIG_GRID_CALIBRATION_SETTLE: "小地图和 WS 连续稳定达到该时长后才允许重锚，避免使用尚未排空延迟的 WS 样本",
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
            CONFIG_GRID_WAYPOINT_CALIBRATION: "普通到达每个中间航点后停止移动并持续采样的秒数，用于等待小地图+WS自动静止校准",
        }

    def _init_grid_navigation_mixin(self) -> None:
        """Initialize navigation state without starting position sources."""
        self._init_minimap_position_mixin()
        self._grid_nav_follower: GridRouteFollower | None = None
        self._grid_nav_map_id = ""
        self._grid_nav_w_held = False
        self._grid_nav_position_active = False
        self._grid_nav_last_wait_log = 0.0
        self._grid_nav_distance_since_calibration = 0.0
        self._grid_nav_last_position: tuple[float, float] | None = None
        self._grid_nav_last_position_map = ""
        self._grid_nav_start_calibrated = False

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
        """Plan a world-space route without controlling the game."""
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
        """Plan and follow a route to a world ``(x, z)`` target.

        Returns ``True`` only after the fused minimap position enters the final
        goal radius. All keyboard input is released on every exit path.
        """
        goal = (float(goal_xz[0]), float(goal_xz[1]))
        limit = self._cfg_float(CONFIG_GRID_TIMEOUT, 180.0) if timeout is None else float(timeout)
        limit = max(1.0, limit)
        tick = max(0.05, self._cfg_float(CONFIG_GRID_TICK, 0.2))
        owns_position = not self._grid_nav_position_active
        started_at = self.active_time()
        best_goal_distance = math.inf
        recovery_attempts = 0
        replans = 0
        had_plan = False
        current_map = str(map_id or "")

        if owns_position:
            self.start_minimap_position(wait_stable=False)
            self._grid_nav_position_active = True
        self._grid_nav_follower = None
        self._grid_nav_distance_since_calibration = 0.0
        self._grid_nav_last_position = None
        self._grid_nav_last_position_map = ""
        self._grid_nav_start_calibrated = False
        self._set_grid_walking(False)

        try:
            while self.active_time() - started_at < limit:
                frame = self.next_frame()
                state = self.minimap_position(frame=frame)
                x = state.get("x")
                z = state.get("z")
                actual_map = str(state.get("map_id") or current_map or "")
                self._log_grid_navigation_debug(state, map_id=actual_map, tag="导航")
                if x is None or z is None:
                    self._wait_for_grid_position("等待融合坐标锚定")
                    self.sleep(tick)
                    continue
                if not state.get("odom_ok"):
                    self._set_grid_walking(False)
                    self._wait_for_grid_position(f"等待有效位移样本: {state.get('odom_reason')}")
                    self.sleep(tick)
                    continue
                self._update_grid_nav_travel((float(x), float(z)), actual_map)

                if not self._grid_nav_start_calibrated:
                    self._set_grid_walking(False)
                    calibrated, _position = self._calibrate_grid_position("开始")
                    if calibrated:
                        self._grid_nav_start_calibrated = True
                    continue

                heading = state.get("heading")
                heading_score = state.get("heading_score")
                min_score = max(0.0, min(1.0, self._cfg_float(CONFIG_MIN_SCORE, 0.6)))
                if heading_score is None or float(heading_score) < min_score:
                    heading = None
                if heading is None:
                    self._set_grid_walking(False)
                    self._wait_for_grid_position(f"等待可靠朝向: score={heading_score}")
                    self.sleep(tick)
                    continue

                if self._grid_nav_follower is None or actual_map != self._grid_nav_map_id:
                    if self._grid_nav_follower is not None:
                        self.log_info(f"地图已切换 {self._grid_nav_map_id!r} -> {actual_map!r}，重新规划")
                    elif had_plan:
                        # 只有 follower 被清空（卡住/到达未确认等）才算"重规划"。首次规划
                        # 不受上限约束，否则「最大重规划次数」设为 0 会在规划前就返回失败。
                        if replans >= self._cfg_int(CONFIG_GRID_MAX_REPLANS, 8):
                            self.log_warning("导航重规划次数已达上限", notify=True)
                            return False
                        replans += 1
                    follower, result = self._create_grid_route(
                        (float(x), float(z)),
                        goal,
                        map_id=actual_map,
                        grid_path=grid_path,
                        grid_dir=grid_dir,
                        zoom=zoom,
                    )
                    had_plan = True
                    if follower is None or not result.ok:
                        self._log_grid_plan_failure(result, "导航规划失败")
                        return False
                    self._grid_nav_follower = follower
                    self._grid_nav_map_id = actual_map
                    self._log_grid_plan(result)

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
                if step.arrived_waypoint_index is not None:
                    if self._grid_calibration_due():
                        self._calibrate_grid_waypoint(
                            step.arrived_waypoint_index,
                            step.waypoint_count,
                        )
                    else:
                        minimum = max(
                            0.0,
                            self._cfg_float(CONFIG_GRID_CALIBRATION_DISTANCE, 100.0),
                        )
                        self.log_info(
                            f"到达航点 {step.arrived_waypoint_index + 1}/{step.waypoint_count}，"
                            f"距上次校准仅 {self._grid_nav_distance_since_calibration:.1f}m"
                            f"（阈值 {minimum:.1f}m），跳过校准停留"
                        )
                    continue
                if step.distance_to_goal is not None:
                    if step.distance_to_goal + 0.5 < best_goal_distance:
                        best_goal_distance = step.distance_to_goal
                        replans = 0
                        recovery_attempts = 0
                    self.info_set(
                        "网格导航",
                        f"{step.action} | 目标距离={step.distance_to_goal:.2f}m | "
                        f"航点={step.waypoint_index + 1}/{step.waypoint_count}",
                    )

                if step.action == DONE:
                    self._set_grid_walking(False)
                    goal_radius = max(0.0, self._cfg_float(CONFIG_GRID_GOAL_RADIUS, 2.0))
                    calibrated, position = self._calibrate_grid_position("目标")
                    if calibrated and position is not None:
                        goal_distance = math.hypot(position[0] - goal[0], position[1] - goal[1])
                        if goal_distance <= goal_radius:
                            self.log_info(
                                f"校准后确认到达: ({position[0]:.2f}, {position[1]:.2f})，"
                                f"误差 {goal_distance:.2f}m",
                                notify=True,
                            )
                            return True
                        self.log_warning(
                            f"目标校准后仍在到达半径外：距离 {goal_distance:.2f}m > "
                            f"{goal_radius:.2f}m，从校准位置重新规划",
                            notify=True,
                        )
                    else:
                        # 目标处没等到新的 WS 坐标 → 静止校准没完成。这不等于"没到"：follower
                        # 是按同一套融合坐标判定 DONE 的，已经落在 goal_radius 内。丢弃这个到达
                        # 去重规划只会原地打转（重规划后仍立刻 DONE、又没有新 WS 触发校准），
                        # 最后在目标圈里报"重规划次数已达上限"。所以按 follower 的判定接受。
                        distance_text = (
                            f"{step.distance_to_goal:.2f}m"
                            if step.distance_to_goal is not None else "未知"
                        )
                        self.log_warning(
                            f"目标处未完成静止校准，按 follower 判定接受到达（距目标 {distance_text}）",
                            notify=True,
                        )
                        return True
                    self._grid_nav_follower = None
                    continue
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
                    self._recover_grid_stuck()
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

            self.log_warning(f"导航超时（{limit:.1f}s）", notify=True)
            return False
        finally:
            self._set_grid_walking(False)
            if owns_position:
                self.stop_minimap_position()
                self._grid_nav_position_active = False

    def _create_grid_route(
        self,
        start_xz: tuple[float, float],
        goal_xz: tuple[float, float],
        *,
        map_id: str,
        grid_path: str | None,
        grid_dir: str | None,
        zoom: str | None,
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
        result = follower.plan(start_xz, goal_xz)
        return follower, result

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
        if result.cap_exceeded:
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

    def _calibrate_grid_waypoint(self, waypoint_index: int, waypoint_count: int) -> bool:
        """Stop at an intermediate waypoint and keep sampling for automatic calibration."""
        calibrated, _position = self._calibrate_grid_position(
            f"航点 {waypoint_index + 1}/{waypoint_count}"
        )
        return calibrated

    def _calibrate_grid_position(self, tag: str) -> tuple[bool, tuple[float, float] | None]:
        """Stop and sample until an automatic static calibration completes."""
        self._set_grid_walking(False)
        duration = max(0.0, self._cfg_float(CONFIG_GRID_WAYPOINT_CALIBRATION, 3.0))
        settle_required = max(
            0.0,
            self._cfg_float(CONFIG_GRID_CALIBRATION_SETTLE, 1.0),
        )
        duration = max(duration, settle_required + 1.0)
        if duration <= 0:
            return False, None

        tick = max(0.05, self._cfg_float(CONFIG_GRID_TICK, 0.2))
        started = self.active_time()
        stable_since = None
        synced = False
        checked = False
        last_position = None
        last_residual = None
        self.log_info(
            f"{tag}：停留 {duration:.1f}s 等待小地图自动校准"
        )
        while self.active_time() - started < duration:
            remaining = duration - (self.active_time() - started)
            wait = min(tick, max(0.0, remaining))
            if wait <= 0:
                break
            self.sleep(wait)
            now = self.active_time()
            allow_sync = (
                stable_since is not None
                and now - stable_since >= settle_required
            )
            state = self.minimap_position(
                frame=self.next_frame(),
                now=now,
                allow_sync=allow_sync,
            )
            self._log_grid_navigation_debug(
                state,
                map_id=str(state.get("map_id") or self._grid_nav_map_id or ""),
                tag=f"{tag}校准",
            )
            if state.get("rest") and state.get("odom_ok"):
                if stable_since is None:
                    stable_since = now
            else:
                stable_since = None
            synced = synced or bool(state.get("just_synced"))
            checked = checked or bool(state.get("sync_checked"))
            if state.get("sync_residual") is not None:
                last_residual = state["sync_residual"]
            if state.get("x") is not None and state.get("z") is not None:
                last_position = (float(state["x"]), float(state["z"]))
            elapsed = min(duration, self.active_time() - started)
            self.info_set(
                "网格导航",
                f"{tag} 校准中 {elapsed:.1f}/{duration:.1f}s",
            )

        if synced:
            self.log_info(f"{tag} 自动校准完成")
            if last_residual is not None:
                self.log_info(
                    f"{tag} 静止校准偏差："
                    f"小地图推算=({last_residual['map_x']:.3f}, {last_residual['map_z']:.3f}) "
                    f"WS=({last_residual['ws_x']:.3f}, {last_residual['ws_z']:.3f}) "
                    f"偏差=({last_residual['dx']:+.3f}, {last_residual['dz']:+.3f}) "
                    f"距离={last_residual['dist']:.3f}m"
                )
        elif checked:
            self.log_info(f"{tag} 已静止且与 WS 对齐，无需重锚")
        else:
            diag = self.minimap_rest_diag() or {}
            self.log_warning(
                f"{tag} 停留结束但未触发自动校准："
                f"reason={diag.get('reason')} speed={diag.get('map_speed_m_s')} "
                f"ws_moved={diag.get('ws_moved_m')}"
            )
        if synced or checked:
            self._grid_nav_distance_since_calibration = 0.0
            if last_position is not None:
                self._grid_nav_last_position = last_position
        return synced or checked, last_position

    def _log_grid_navigation_debug(self, state: dict, *, map_id: str, tag: str) -> None:
        """Emit one detailed positioning line per frame when debug mode is enabled."""
        if not getattr(self, "debug", False):
            return
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
            f"校准检查={bool(state.get('sync_checked'))} "
            f"距上次校准={self._grid_nav_distance_since_calibration:.2f}m"
        )

    def _update_grid_nav_travel(self, position: tuple[float, float], map_id: str) -> None:
        """Accumulate plausible movement since the last successful calibration."""
        if (
            self._grid_nav_last_position is not None
            and map_id == self._grid_nav_last_position_map
        ):
            moved = math.hypot(
                position[0] - self._grid_nav_last_position[0],
                position[1] - self._grid_nav_last_position[1],
            )
            if moved <= 10.0:
                self._grid_nav_distance_since_calibration += moved
        self._grid_nav_last_position = position
        self._grid_nav_last_position_map = map_id

    def _grid_calibration_due(self) -> bool:
        minimum = max(0.0, self._cfg_float(CONFIG_GRID_CALIBRATION_DISTANCE, 100.0))
        return minimum <= 0 or self._grid_nav_distance_since_calibration >= minimum

    def _recover_grid_stuck(self) -> None:
        duration = max(0.1, self._cfg_float(CONFIG_GRID_RECOVERY_TIME, 0.45))
        for key in ("s", "a", "d"):
            self.press_key(key, down_time=duration)

    def _wait_for_grid_position(self, reason: str) -> None:
        now = self.active_time()
        if now - self._grid_nav_last_wait_log >= 5.0:
            self._grid_nav_last_wait_log = now
            self.log_info(f"网格导航暂停：{reason}")
            self.info_set("网格导航", reason)

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
