"""把规划航点转换为离散执行动作。

本模块不读画面、不发按键，只根据当前位置、朝向和时间输出下一步动作。任务层负责执行
``TURN / WALK / WAIT``，并在收到 ``REPLAN / STUCK`` 时重新规划或脱困。

状态机大致顺序::

    到达终点 -> DONE
    偏航持续超限 -> REPLAN
    朝向不可用 -> WAIT
    朝向误差过大 -> TURN
    持续行走但位移不足 -> STUCK
    其余情况 -> WALK

坐标均为世界系 ``(x, z)``，距离单位为米；角度为罗盘方位角，正北 ``0°``、正东
``90°``，顺时针增大。偏航使用“超限半径 + 持续时间”双重判定，避免单拍定位抖动触发
重规划。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.nav.grid_io import DenseGrid
from src.nav.grid_planner import (
    DEFAULT_ZIP_LINE_ACCESS_RADIUS,
    DEFAULT_ZIP_LINE_BOARDING_COST,
    DEFAULT_ZIP_LINE_COST_FACTOR,
    GridPlanner,
    PlanResult,
    ZipLineRouteStep,
)
from src.nav.zip_line_graph import ZipLineGraph

WAIT = "wait"
TURN = "turn"
WALK = "walk"
ZIP_LINE = "zip_line"
NARROW_CALIBRATE = "narrow_calibrate"
STUCK = "stuck"
REPLAN = "replan"
DONE = "done"
FAILED = "failed"


def angle_delta(target: float, current: float) -> float:
    """返回两个角度之间的最短有向差，范围为 ``(-180, 180]``。"""
    return (float(target) - float(current) + 180.0) % 360.0 - 180.0


def bearing_to_point(x: float, z: float, target_x: float, target_z: float) -> float:
    """返回从 ``(x, z)`` 指向 ``(target_x, target_z)`` 的罗盘方位角。

    世界 ``+x`` 为东、``+z`` 为北，因此方位角从正北顺时针增大。
    """
    dx = float(target_x) - float(x)
    dz = float(target_z) - float(z)
    return math.degrees(math.atan2(dx, dz)) % 360.0


def distance_xz(a: tuple[float, float], b: tuple[float, float]) -> float:
    """返回世界 XZ 平面上的欧氏距离。"""
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def point_segment_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> tuple[float, float]:
    """返回点到线段的距离，以及夹在 ``[0, 1]`` 内的投影比例。"""
    px, pz = (float(point[0]), float(point[1]))
    ax, az = (float(start[0]), float(start[1]))
    bx, bz = (float(end[0]), float(end[1]))
    dx, dz = bx - ax, bz - az
    length_sq = dx * dx + dz * dz
    if length_sq <= 1e-12:
        return distance_xz(point, start), 0.0
    ratio = ((px - ax) * dx + (pz - az) * dz) / length_sq
    ratio = min(1.0, max(0.0, ratio))
    closest = (ax + ratio * dx, az + ratio * dz)
    return distance_xz(point, closest), ratio


@dataclass(frozen=True)
class FollowerConfig:
    """路线跟随参数。

    距离均为世界米。``arrive_radius`` 控制中间航点切换，且切换还要求到下一航点的
    连线可通行；``goal_radius`` 控制最终到达；
    ``off_route_radius`` 与 ``off_route_hold_s`` 同时满足才判定偏航，用于过滤定位抖动。
    其余墙距、未知格风险和航点合并参数会原样传给 :class:`GridPlanner`。

    这些默认值是**本模块单独使用时的取值**，与全局 ``Nav Config`` 里的默认保持一致
    （``margin`` 曾在这里留成 1、在配置里是 3，属于历史手调留下的漂移，已对齐）。
    应用侧不依赖它们：``GridNavigationMixin._grid_follower_config`` 每一项都显式从
    ``Nav Config`` 取值，所以改配置不需要动这里。
    """

    arrive_radius: float = 1.0
    goal_radius: float = 2.0
    heading_tolerance: float = 4.0
    stuck_window_s: float = 2.5
    stuck_min_distance: float = 0.35
    shortcut_radius: float = 1.0
    risk_cost: float = 5.0
    allow_unknown: bool = True
    margin: int = 3
    wall_penalty: float = 1.0
    frontier_margin: int = 2
    frontier_penalty: float = 1.0
    waypoint_tolerance: float = 2.0
    max_expand: int = 400_000
    off_route_radius: float = 6.0
    off_route_hold_s: float = 1.5
    narrow_calibration: bool = True
    narrow_stop_radius: float = 3.0
    zip_line_cost_factor: float = DEFAULT_ZIP_LINE_COST_FACTOR
    zip_line_boarding_cost: float = DEFAULT_ZIP_LINE_BOARDING_COST
    zip_line_access_radius: int = DEFAULT_ZIP_LINE_ACCESS_RADIUS


@dataclass(frozen=True)
class FollowerStep:
    """一次跟随决策。

    ``action`` 是 ``WAIT/TURN/WALK/STUCK/REPLAN/DONE/FAILED`` 之一。其余字段是该动作的
    诊断快照，调用方不应自行推导状态，避免与跟随器内部航点索引不一致。
    """

    action: str
    waypoint: tuple[float, float] | None = None
    target_bearing: float | None = None
    heading_error: float | None = None
    distance_to_waypoint: float | None = None
    distance_to_goal: float | None = None
    waypoint_index: int = 0
    waypoint_count: int = 0
    #: 本段的"值不值得冲刺"判据（米）：``waypoint_index > 0`` 时是上一个航点到当前航点的
    #: **规划**长度，首航点退回到到当前航点的剩余距离。调用方据此决定长航段要不要冲刺，
    #: 不需要再去读 ``plan_result.waypoints``。
    leg_distance_m: float = 0.0
    arrived_waypoint_index: int | None = None
    skipped_waypoints: tuple[int, ...] = ()
    shortcut_distance: float | None = None
    zip_line_step: ZipLineRouteStep | None = None
    reason: str = ""


class GridRouteFollower:
    """跟随 :class:`~src.nav.grid_planner.PlanResult`，不直接操作游戏。"""

    def __init__(
        self,
        grid: DenseGrid,
        config: FollowerConfig | None = None,
        *,
        zip_lines: ZipLineGraph | None = None,
    ):
        """创建跟随器；配置缺省时使用 :class:`FollowerConfig` 默认值。"""
        self.grid = grid
        self.config = config or FollowerConfig()
        self.planner = GridPlanner(
            grid,
            risk_cost=self.config.risk_cost,
            diagonal=True,
            allow_unknown=self.config.allow_unknown,
            margin=self.config.margin,
            wall_penalty=self.config.wall_penalty,
            frontier_margin=self.config.frontier_margin,
            frontier_penalty=self.config.frontier_penalty,
            waypoint_tolerance=self.config.waypoint_tolerance,
            max_expand=self.config.max_expand,
            zip_lines=zip_lines,
            zip_line_cost_factor=self.config.zip_line_cost_factor,
            zip_line_boarding_cost=self.config.zip_line_boarding_cost,
            zip_line_access_radius=self.config.zip_line_access_radius,
        )
        self.plan_result: PlanResult | None = None
        self.goal: tuple[float, float] | None = None
        self.waypoint_index = 0
        self._zip_line_index = 0
        self._move_started_at: float | None = None
        self._move_start_pos: tuple[float, float] | None = None
        self._route_start: tuple[float, float] | None = None
        self._off_route_since: float | None = None
        self._narrow_calibrated: set[int] = set()
        self._narrow_attempts: dict[int, int] = {}

    def pause(self) -> None:
        """暂停跟随状态；外层停止控制输入时应调用，避免把停走误判为卡住。"""
        self._reset_motion()
        self._off_route_since = None

    def zip_line_stats(self) -> dict[str, int]:
        """返回滑索图接入网格后的统计（节点 / 连接 / 可映射节点 / 有效有向边）。

        转发 :meth:`~src.nav.grid_planner.GridPlanner.zip_line_stats`：任务层要回答
        "读到了滑索却没走滑索"时用得到，不必再去拿跟随器内部的 planner。
        """
        return self.planner.zip_line_stats()

    def plan(
        self,
        start: tuple[float, float],
        goal: tuple[float, float],
        *,
        time_budget_s: float | None = None,
        required_zip_line_start_id: str | None = None,
    ) -> PlanResult:
        """重新规划并重置航点进度、卡住窗口与偏航计时。"""
        self.goal = (float(goal[0]), float(goal[1]))
        self._route_start = (float(start[0]), float(start[1]))
        self.plan_result = self.planner.plan(
            start,
            goal,
            time_budget_s=time_budget_s,
            required_zip_line_start_id=required_zip_line_start_id,
        )
        self.waypoint_index = 0
        self._zip_line_index = 0
        self._narrow_calibrated = set()
        self._narrow_attempts = {}
        self._reset_motion()
        self._off_route_since = None
        if self.plan_result.ok:
            self._advance_initial_waypoints((float(start[0]), float(start[1])))
        return self.plan_result

    def adopt(
        self,
        plan_result: PlanResult,
        *,
        goal: tuple[float, float],
        start: tuple[float, float],
    ) -> PlanResult:
        """直接采用一份**已经算好**的 :class:`PlanResult`，不重新规划。

        分层导航按分段驱动时用得上：每段的路线由 :class:`~src.nav.layered_planner.LayeredPlanner`
        算好（它在层图上组合，还会缓存），跟随器只负责把这一段走完。逐段各建一个跟随器、
        用本方法装配，状态机（TURN/WALK/WAIT/STUCK/DONE）就完全不用改——换段 = 换跟随器。

        与 :meth:`plan` 的唯一区别是不调用 planner：其余（航点进度、卡住窗口、偏航计时、
        窄道记录）逐项重置成同一状态，保证换段后的行为与"就在这里重新规划"一致。
        """
        self.goal = (float(goal[0]), float(goal[1]))
        self._route_start = (float(start[0]), float(start[1]))
        self.plan_result = plan_result
        self.waypoint_index = 0
        self._zip_line_index = 0
        self._narrow_calibrated = set()
        self._narrow_attempts = {}
        self._reset_motion()
        self._off_route_since = None
        if plan_result.ok:
            self._advance_initial_waypoints((float(start[0]), float(start[1])))
        return plan_result

    def update(
        self,
        position: tuple[float, float],
        heading: float | None,
        now: float,
    ) -> FollowerStep:
        """根据当前位置和朝向生成下一步动作。

        Args:
            position: 当前融合位置 ``(x, z)``，世界米。
            heading: 当前罗盘方位角；``None`` 表示朝向不可用。
            now: 单调递增秒数，用于偏航持续时间和卡住窗口。

        Returns:
            :class:`FollowerStep`。位置不可用、规划失败或尚未规划时返回 ``FAILED``。
        """
        result = self.plan_result
        if result is None or not result.ok or not result.waypoints:
            return FollowerStep(FAILED, reason="尚无有效导航路径")

        pos = (float(position[0]), float(position[1]))
        waypoints = result.waypoints
        final = waypoints[-1]
        goal_distance = distance_xz(pos, final)
        if goal_distance <= self.config.goal_radius:
            self._reset_motion()
            if self.goal is not None:
                snap_offset = distance_xz(final, self.goal)
                if snap_offset > self.config.goal_radius:
                    # 规划器把不可通行的目标吸附到了附近的可行走格。走到吸附点不等于到达
                    # 请求目标，调用方（取货点/送货点确认）不能据 DONE 认定已经到位。
                    return self._step(
                        FAILED,
                        final,
                        goal_distance=snap_offset,
                        heading_error=None,
                        target_bearing=None,
                        waypoint_distance=goal_distance,
                        reason=(
                            f"已到达最近的可行走航点，但距请求目标仍有 {snap_offset:.1f}m"
                            "（请求目标不可通行，终点已被吸附）"
                        ),
                    )
            return self._step(
                DONE,
                final,
                goal_distance=goal_distance,
                heading_error=None,
                target_bearing=None,
                waypoint_distance=goal_distance,
            )

        narrow_hold = self._pending_narrow_hold(pos)
        if narrow_hold is not None:
            entry = waypoints[narrow_hold]
            entry_bearing = bearing_to_point(pos[0], pos[1], entry[0], entry[1])
            self._reset_motion()
            return self._step(
                NARROW_CALIBRATE,
                entry,
                target_bearing=entry_bearing,
                heading_error=None,
                waypoint_distance=distance_xz(pos, entry),
                goal_distance=goal_distance,
                reason="窄道入口前需停车校准并对准航点",
            )

        arrived_index = None
        blocked_waypoint_index = None
        waypoint_limit = self._waypoint_limit(len(waypoints))
        while (
            self.waypoint_index < waypoint_limit
            and distance_xz(pos, waypoints[self.waypoint_index]) <= self.config.arrive_radius
        ):
            if not self._waypoint_transition_clear(pos, self.waypoint_index, waypoint_limit):
                blocked_waypoint_index = self.waypoint_index
                break
            arrived_index = self.waypoint_index
            self.waypoint_index += 1

        if blocked_waypoint_index is not None:
            waypoint = waypoints[blocked_waypoint_index]
            waypoint_distance = distance_xz(pos, waypoint)
            self._reset_motion()
            return self._step(
                REPLAN,
                waypoint,
                target_bearing=bearing_to_point(pos[0], pos[1], waypoint[0], waypoint[1]),
                heading_error=None,
                waypoint_distance=waypoint_distance,
                goal_distance=goal_distance,
                reason="航点已进入到达半径，但到下一航点的连线被不可通行格阻挡",
            )

        zip_line_step = self.current_zip_line_step()
        if zip_line_step is not None:
            entry_index = zip_line_step.entry_waypoint_index
            if self.waypoint_index > entry_index:
                self.waypoint_index = entry_index
            entry = waypoints[entry_index]
            entry_distance = distance_xz(pos, entry)
            if self.waypoint_index >= entry_index and entry_distance <= self.config.arrive_radius:
                self._reset_motion()
                return self._step(
                    ZIP_LINE,
                    entry,
                    target_bearing=None,
                    heading_error=None,
                    waypoint_distance=entry_distance,
                    goal_distance=goal_distance,
                    zip_line_step=zip_line_step,
                )

        waypoint = waypoints[self.waypoint_index]
        waypoint_distance = distance_xz(pos, waypoint)
        target_bearing = bearing_to_point(pos[0], pos[1], waypoint[0], waypoint[1])
        skipped, shortcut_distance = self._skip_nearby_segments(pos)
        if skipped:
            waypoint = waypoints[self.waypoint_index]
            waypoint_distance = distance_xz(pos, waypoint)
            target_bearing = bearing_to_point(pos[0], pos[1], waypoint[0], waypoint[1])

        off_route_distance = self._confirmed_off_route_distance(pos, now)
        if off_route_distance is not None:
            self._reset_motion()
            return self._step(
                REPLAN,
                waypoint,
                target_bearing=target_bearing,
                heading_error=None,
                waypoint_distance=waypoint_distance,
                goal_distance=goal_distance,
                arrived_waypoint_index=arrived_index,
                skipped_waypoints=skipped,
                shortcut_distance=shortcut_distance,
                reason=(
                    f"偏离路径 {off_route_distance:.2f}m，超过 {max(0.0, float(self.config.off_route_radius)):.2f}m"
                ),
            )

        if heading is None:
            self._reset_motion()
            return self._step(
                WAIT,
                waypoint,
                target_bearing=target_bearing,
                heading_error=None,
                waypoint_distance=waypoint_distance,
                goal_distance=goal_distance,
                arrived_waypoint_index=arrived_index,
                skipped_waypoints=skipped,
                shortcut_distance=shortcut_distance,
                reason="朝向不可用",
            )

        delta = angle_delta(target_bearing, heading)
        tolerance = max(0.0, float(self.config.heading_tolerance))
        if abs(delta) > tolerance:
            self._reset_motion()
            return self._step(
                TURN,
                waypoint,
                target_bearing=target_bearing,
                heading_error=delta,
                waypoint_distance=waypoint_distance,
                goal_distance=goal_distance,
                arrived_waypoint_index=arrived_index,
                skipped_waypoints=skipped,
                shortcut_distance=shortcut_distance,
            )

        stuck = self._update_stuck(pos, now)
        if stuck:
            return self._step(
                STUCK,
                waypoint,
                target_bearing=target_bearing,
                heading_error=delta,
                waypoint_distance=waypoint_distance,
                goal_distance=goal_distance,
                arrived_waypoint_index=arrived_index,
                skipped_waypoints=skipped,
                shortcut_distance=shortcut_distance,
                reason=f"{self.config.stuck_window_s:.1f}s 内位移不足",
            )

        return self._step(
            WALK,
            waypoint,
            target_bearing=target_bearing,
            heading_error=delta,
            waypoint_distance=waypoint_distance,
            goal_distance=goal_distance,
            arrived_waypoint_index=arrived_index,
            skipped_waypoints=skipped,
            shortcut_distance=shortcut_distance,
        )

    def _pending_narrow_hold(self, position: tuple[float, float]) -> int | None:
        """返回当前是否需要为一个窄道入口停车校准，并给出入口航点下标。

        只有当前目标航点本身是「一格宽通路」的入口、且角色已经接近到
        ``narrow_stop_radius`` 以内、且尚未校准过时才触发，保证是在进入窄道之前停车。
        """
        if not self.config.narrow_calibration or self.plan_result is None:
            return None
        entries = self.plan_result.narrow_entries
        index = self.waypoint_index
        if index not in entries or index in self._narrow_calibrated:
            return None
        waypoints = self.plan_result.waypoints
        if index >= len(waypoints):
            return None
        # 已经踏进窄道内部就来不及"进入前"停车了，不再触发，避免在窄道中间停住。
        if self.planner.is_narrow_corridor(*self.grid.index_of_world(position[0], position[1])):
            return None
        radius = max(0.0, float(self.config.narrow_stop_radius))
        if distance_xz(position, waypoints[index]) > radius:
            return None
        return index

    def confirm_narrow_calibration(self) -> bool:
        """确认当前窄道入口的停车校准已完成，之后不再对同一入口重复停车。"""
        if self.plan_result is None:
            return False
        index = self.waypoint_index
        if index not in self.plan_result.narrow_entries:
            return False
        self._narrow_calibrated.add(index)
        self._reset_motion()
        self._off_route_since = None
        return True

    def narrow_calibration_failed(self) -> int:
        """记录当前窄道入口的一次校准/对准失败，返回该入口累计失败次数。"""
        if self.plan_result is None:
            return 0
        index = self.waypoint_index
        self._narrow_attempts[index] = self._narrow_attempts.get(index, 0) + 1
        return self._narrow_attempts[index]

    def _skip_nearby_segments(self, position: tuple[float, float]) -> tuple[tuple[int, ...], float | None]:
        """当前位置已经靠近后续航段时，跳过这些已无必要返回的航点。"""
        radius = max(0.0, float(self.config.shortcut_radius))
        if radius <= 0 or self.plan_result is None:
            return (), None
        waypoints = self.plan_result.waypoints
        skipped: list[int] = []
        nearest = None
        limit = self._waypoint_limit(len(waypoints))
        # 未校准的窄道入口必须保留为目标，不能作为捷径被跳过去。
        pending_entries = [index for index in self.plan_result.narrow_entries if index not in self._narrow_calibrated]
        hold = min((index for index in pending_entries if index >= self.waypoint_index), default=None)
        while self.waypoint_index < limit:
            if hold is not None and self.waypoint_index >= hold:
                break
            start = waypoints[self.waypoint_index]
            end = waypoints[self.waypoint_index + 1]
            distance, _ratio = point_segment_distance(position, start, end)
            if distance > radius:
                break
            if not self._shortcut_ok(position, start, end):
                break
            skipped.append(self.waypoint_index)
            nearest = distance if nearest is None else min(nearest, distance)
            self.waypoint_index += 1
        return tuple(skipped), nearest

    def _waypoint_transition_clear(
        self,
        position: tuple[float, float],
        index: int,
        waypoint_limit: int,
    ) -> bool:
        """到达半径内的航点还必须能安全看到当前点和下一航点。

        圆形半径只能说明两点在欧氏距离上接近，不能说明中间没有墙。窄道拐角处，
        当前位置可能已经进入当前航点半径，但直线前往下一航点会穿墙。此时切到下一
        航点会让任务直接朝墙走，因此必须保留当前航点并请求从真实位置重新规划。

        ``waypoint_limit`` 是当前连续行走段内允许作为目标的最后一个下标。滑索入口
        前的最后一段仍要检查到入口的视线，但不能把滑索另一端的航点当成步行目标。
        """
        if self.plan_result is None or not self.plan_result.waypoints:
            return False
        waypoints = self.plan_result.waypoints
        if index < 0 or index >= len(waypoints):
            return False

        here = self.grid.index_of_world(position[0], position[1])
        current = self.grid.index_of_world(waypoints[index][0], waypoints[index][1])
        if not self.planner.line_clear(here, current):
            return False

        next_index = index + 1
        if next_index < len(waypoints) and next_index <= waypoint_limit:
            following = self.grid.index_of_world(waypoints[next_index][0], waypoints[next_index][1])
            if not self.planner.line_clear(here, following):
                return False
        return True

    def current_zip_line_step(self) -> ZipLineRouteStep | None:
        """返回下一项待执行的滑索步骤；没有待执行项时返回 ``None``。

        任务层用它判断"这一拍是不是该走 :class:`NavState.ZIP_LINE_APPROACH`"，
        不需要知道跟随器内部用 ``_zip_line_index`` 记进度。
        """
        if self.plan_result is None:
            return None
        steps = self.plan_result.zip_line_steps
        if self._zip_line_index >= len(steps):
            return None
        return steps[self._zip_line_index]

    def _waypoint_limit(self, waypoint_count: int) -> int:
        """普通行走不得越过下一步滑索的上索航点。"""
        zip_line_step = self.current_zip_line_step()
        if zip_line_step is None:
            return max(0, waypoint_count - 1)
        return min(max(0, waypoint_count - 1), zip_line_step.entry_waypoint_index)

    def _walkable_ranges(self) -> list[tuple[int, int]]:
        """返回滑索断点之外的连续行走航点区间。"""
        if self.plan_result is None or not self.plan_result.waypoints:
            return []
        count = len(self.plan_result.waypoints)
        ranges: list[tuple[int, int]] = []
        start = 0
        for step in self.plan_result.zip_line_steps:
            ranges.append((start, min(step.entry_waypoint_index, count - 1)))
            start = min(max(start, step.exit_waypoint_index), count - 1)
        ranges.append((start, count - 1))
        return ranges

    @staticmethod
    def _range_for_index(ranges: list[tuple[int, int]], index: int) -> tuple[int, int] | None:
        for start, end in ranges:
            if start <= index <= end:
                return start, end
        return None

    def _off_route_distance(self, position: tuple[float, float]) -> float | None:
        """返回当前位置到当前**连续行走段**中相邻路线段的较近距离。

        滑索连接两端可能相距百米，不能把这段空中连线当成可行走路线，否则角色在
        上索点附近会被误判为已接近下一航点。这里按 ``zip_line_steps`` 把路线切开。
        """
        if self.plan_result is None:
            return None
        waypoints = self.plan_result.waypoints
        if not waypoints:
            return None
        index = min(max(0, self.waypoint_index), len(waypoints) - 1)
        walk_range = self._range_for_index(self._walkable_ranges(), index)
        if walk_range is None:
            return None
        range_start, range_end = walk_range
        distances = []
        if index == range_start and range_start == 0 and self._route_start is not None:
            distances.append(point_segment_distance(position, self._route_start, waypoints[0])[0])
        if index > range_start:
            distances.append(point_segment_distance(position, waypoints[index - 1], waypoints[index])[0])
        if index < range_end:
            distances.append(point_segment_distance(position, waypoints[index], waypoints[index + 1])[0])
        if not distances:
            return None
        return min(distances)

    def _confirmed_off_route_distance(
        self,
        position: tuple[float, float],
        now: float,
    ) -> float | None:
        """偏航必须持续超过保持时间，才返回距离并请求重新规划。"""
        radius = max(0.0, float(self.config.off_route_radius))
        if radius <= 0:
            self._off_route_since = None
            return None
        distance = self._off_route_distance(position)
        if distance is None or distance <= radius:
            self._off_route_since = None
            return None
        if self._off_route_since is None:
            self._off_route_since = float(now)
        hold_s = max(0.0, float(self.config.off_route_hold_s))
        if float(now) - self._off_route_since < hold_s:
            return None
        return distance

    def _shortcut_ok(
        self,
        position: tuple[float, float],
        anchor: tuple[float, float],
        end: tuple[float, float],
    ) -> bool:
        """直连到 ``end`` 是否可接受。

        除了视线不撞墙，还要过规划器 ``_replacement_ok`` 同一条风险判据：直连线穿过的
        未知格不得超过它替换掉的原航段。否则捷径会把规划器特意绕开的未探明区域抄回去，
        真撞上墙就触发卡住/重规划——正是未知格风险代价想避免的。
        """
        start_cell = self.grid.index_of_world(position[0], position[1])
        anchor_cell = self.grid.index_of_world(anchor[0], anchor[1])
        end_cell = self.grid.index_of_world(end[0], end[1])
        if not self.planner.passable(*start_cell) or not self.planner.passable(*end_cell):
            return False
        if not self.planner.line_clear(start_cell, end_cell):
            return False
        return self.planner.line_risk(start_cell, end_cell) <= self.planner.line_risk(anchor_cell, end_cell)

    def complete_zip_line(self) -> bool:
        """滑索执行成功后推进到下索后的连续行走段。"""
        step = self.current_zip_line_step()
        if step is None:
            return False
        self.waypoint_index = min(
            max(0, step.exit_waypoint_index),
            max(0, len(self.plan_result.waypoints) - 1),
        )
        self._zip_line_index += 1
        self._reset_motion()
        self._off_route_since = None
        self._advance_initial_waypoints(self.plan_result.waypoints[self.waypoint_index])
        return True

    def _advance_initial_waypoints(self, start: tuple[float, float]) -> None:
        """规划后跳过起点附近已经到达的初始航点。"""
        waypoints = self.plan_result.waypoints if self.plan_result else []
        if not waypoints:
            return
        limit = min(self._waypoint_limit(len(waypoints)), len(waypoints))
        while (
            self.waypoint_index < limit
            and distance_xz(start, waypoints[self.waypoint_index]) <= self.config.arrive_radius
            and self._waypoint_transition_clear(start, self.waypoint_index, limit)
        ):
            self.waypoint_index += 1

    def _update_stuck(self, position: tuple[float, float], now: float) -> bool:
        """按时间窗口更新卡住判定；窗口内移动足够远就重置窗口。"""
        if self._move_started_at is None or self._move_start_pos is None:
            self._move_started_at = float(now)
            self._move_start_pos = position
            return False

        moved = distance_xz(position, self._move_start_pos)
        if moved >= self.config.stuck_min_distance:
            self._move_started_at = float(now)
            self._move_start_pos = position
            return False
        return float(now) - self._move_started_at >= self.config.stuck_window_s

    def _leg_distance_m(self, waypoint_distance: float | None) -> float:
        """本段的"值不值得冲刺"判据（米）。

        ``waypoint_index > 0`` 时是上一个航点到当前航点的**规划**长度，不随接近航点变小；
        首航点（或索引已越界）没有"上一段"，退回到到当前航点的剩余距离——与该方法搬进
        跟随器之前任务层的取值一致，避免长首段不再触发冲刺。
        """
        result = self.plan_result
        waypoints = result.waypoints if result is not None else []
        index = int(self.waypoint_index)
        if 0 < index < len(waypoints):
            return distance_xz(waypoints[index - 1], waypoints[index])
        if waypoint_distance is None:
            return 0.0
        return abs(float(waypoint_distance))

    def _reset_motion(self) -> None:
        """清空卡住窗口；不修改航点索引或偏航状态。"""
        self._move_started_at = None
        self._move_start_pos = None

    def _step(
        self,
        action: str,
        waypoint: tuple[float, float],
        *,
        target_bearing: float | None,
        heading_error: float | None,
        waypoint_distance: float | None,
        goal_distance: float | None,
        arrived_waypoint_index: int | None = None,
        skipped_waypoints: tuple[int, ...] = (),
        shortcut_distance: float | None = None,
        zip_line_step: ZipLineRouteStep | None = None,
        reason: str = "",
    ) -> FollowerStep:
        """构造完整诊断字段的动作结果。"""
        result = self.plan_result
        count = len(result.waypoints) if result is not None else 0
        return FollowerStep(
            action=action,
            waypoint=(float(waypoint[0]), float(waypoint[1])),
            target_bearing=target_bearing,
            heading_error=heading_error,
            distance_to_waypoint=waypoint_distance,
            distance_to_goal=goal_distance,
            waypoint_index=self.waypoint_index,
            waypoint_count=count,
            leg_distance_m=self._leg_distance_m(waypoint_distance),
            arrived_waypoint_index=arrived_waypoint_index,
            skipped_waypoints=skipped_waypoints,
            shortcut_distance=shortcut_distance,
            zip_line_step=zip_line_step,
            reason=reason,
        )
