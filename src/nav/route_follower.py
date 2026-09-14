"""Pure waypoint follower for a planned navigation grid route."""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.nav.grid_io import DenseGrid
from src.nav.grid_planner import GridPlanner, PlanResult

WAIT = "wait"
TURN = "turn"
WALK = "walk"
STUCK = "stuck"
REPLAN = "replan"
DONE = "done"
FAILED = "failed"


def angle_delta(target: float, current: float) -> float:
    """Return the shortest signed angular delta in degrees."""
    return (float(target) - float(current) + 180.0) % 360.0 - 180.0


def bearing_to_point(x: float, z: float, target_x: float, target_z: float) -> float:
    """Return the compass bearing from ``(x, z)`` to ``(target_x, target_z)``.

    The world grid uses positive X as east and positive Z as north. Compass
    bearings therefore increase clockwise: north 0, east 90, south 180, west 270.
    """
    dx = float(target_x) - float(x)
    dz = float(target_z) - float(z)
    return math.degrees(math.atan2(dx, dz)) % 360.0


def distance_xz(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def point_segment_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> tuple[float, float]:
    """Return distance to a segment and the clamped projection ratio."""
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
    """Tuning for path following. All distances are in world meters."""

    arrive_radius: float = 1.0
    goal_radius: float = 2.0
    heading_tolerance: float = 8.0
    stuck_window_s: float = 2.5
    stuck_min_distance: float = 0.35
    shortcut_radius: float = 1.0
    risk_cost: float = 5.0
    allow_unknown: bool = True
    margin: int = 1
    wall_penalty: float = 1.0
    frontier_margin: int = 2
    frontier_penalty: float = 1.0
    waypoint_tolerance: float = 2.0
    max_expand: int = 400_000
    off_route_radius: float = 4.0


@dataclass(frozen=True)
class FollowerStep:
    """One decision produced by :class:`GridRouteFollower`."""

    action: str
    waypoint: tuple[float, float] | None = None
    target_bearing: float | None = None
    heading_error: float | None = None
    distance_to_waypoint: float | None = None
    distance_to_goal: float | None = None
    waypoint_index: int = 0
    waypoint_count: int = 0
    arrived_waypoint_index: int | None = None
    skipped_waypoints: tuple[int, ...] = ()
    shortcut_distance: float | None = None
    reason: str = ""


class GridRouteFollower:
    """Follow a :class:`~src.nav.grid_planner.PlanResult` without game I/O."""

    def __init__(self, grid: DenseGrid, config: FollowerConfig | None = None):
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
        )
        self.plan_result: PlanResult | None = None
        self.goal: tuple[float, float] | None = None
        self.waypoint_index = 0
        self._move_started_at: float | None = None
        self._move_start_pos: tuple[float, float] | None = None
        self._walk_aligned = False

    def pause(self) -> None:
        """Freeze motion tracking while the outer loop is not controlling movement."""
        self._reset_motion()

    def plan(
        self,
        start: tuple[float, float],
        goal: tuple[float, float],
        *,
        time_budget_s: float | None = None,
    ) -> PlanResult:
        """Plan a fresh route and reset waypoint progress."""
        self.goal = (float(goal[0]), float(goal[1]))
        self.plan_result = self.planner.plan(start, goal, time_budget_s=time_budget_s)
        self.waypoint_index = 0
        self._reset_motion()
        self._walk_aligned = False
        if self.plan_result.ok:
            self._advance_initial_waypoints((float(start[0]), float(start[1])))
        return self.plan_result

    def update(
        self,
        position: tuple[float, float],
        heading: float | None,
        now: float,
    ) -> FollowerStep:
        """Turn/pause/walk decision for the current position and heading."""
        result = self.plan_result
        if result is None or not result.ok or not result.waypoints:
            return FollowerStep(FAILED, reason="尚无有效导航路径")

        pos = (float(position[0]), float(position[1]))
        waypoints = result.waypoints
        final = waypoints[-1]
        goal_distance = distance_xz(pos, final)
        if goal_distance <= self.config.goal_radius:
            self._reset_motion()
            return self._step(
                DONE,
                final,
                goal_distance=goal_distance,
                heading_error=None,
                target_bearing=None,
                waypoint_distance=goal_distance,
            )

        arrived_index = None
        while (
            self.waypoint_index < len(waypoints) - 1
            and distance_xz(pos, waypoints[self.waypoint_index]) <= self.config.arrive_radius
        ):
            arrived_index = self.waypoint_index
            self.waypoint_index += 1

        waypoint = waypoints[self.waypoint_index]
        waypoint_distance = distance_xz(pos, waypoint)
        target_bearing = bearing_to_point(pos[0], pos[1], waypoint[0], waypoint[1])
        skipped, shortcut_distance = self._skip_nearby_segments(pos)
        if skipped:
            waypoint = waypoints[self.waypoint_index]
            waypoint_distance = distance_xz(pos, waypoint)
            target_bearing = bearing_to_point(pos[0], pos[1], waypoint[0], waypoint[1])

        off_route_distance = self._off_route_distance(pos)
        if off_route_distance is not None:
            self._walk_aligned = False
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
                    f"偏离路径 {off_route_distance:.2f}m，超过 "
                    f"{max(0.0, float(self.config.off_route_radius)):.2f}m"
                ),
            )

        if heading is None:
            self._walk_aligned = False
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
        turn_threshold = tolerance if not self._walk_aligned else tolerance * 2.0
        if abs(delta) > turn_threshold:
            self._walk_aligned = False
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

        self._walk_aligned = True
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

    def _skip_nearby_segments(self, position: tuple[float, float]) -> tuple[tuple[int, ...], float | None]:
        """Skip waypoints when the player is already near the following segment."""
        radius = max(0.0, float(self.config.shortcut_radius))
        if radius <= 0 or self.plan_result is None:
            return (), None
        waypoints = self.plan_result.waypoints
        skipped: list[int] = []
        nearest = None
        while self.waypoint_index < len(waypoints) - 1:
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

    def _off_route_distance(self, position: tuple[float, float]) -> float | None:
        """Distance to the active route segment, or None when off-route checking is disabled."""
        radius = max(0.0, float(self.config.off_route_radius))
        if radius <= 0 or self.plan_result is None:
            return None
        waypoints = self.plan_result.waypoints
        if self.waypoint_index >= len(waypoints) - 1:
            return None
        distance, _ratio = point_segment_distance(
            position,
            waypoints[self.waypoint_index],
            waypoints[self.waypoint_index + 1],
        )
        return distance if distance > radius else None

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

    def _advance_initial_waypoints(self, start: tuple[float, float]) -> None:
        waypoints = self.plan_result.waypoints if self.plan_result else []
        while (
            self.waypoint_index < len(waypoints) - 1
            and distance_xz(start, waypoints[self.waypoint_index]) <= self.config.arrive_radius
        ):
            self.waypoint_index += 1

    def _update_stuck(self, position: tuple[float, float], now: float) -> bool:
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

    def _reset_motion(self) -> None:
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
        reason: str = "",
    ) -> FollowerStep:
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
            arrived_waypoint_index=arrived_waypoint_index,
            skipped_waypoints=skipped_waypoints,
            shortcut_distance=shortcut_distance,
            reason=reason,
        )
