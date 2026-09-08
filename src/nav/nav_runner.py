# -*- coding: utf-8 -*-
"""网格导航执行器：按网格寻路结果驱动角色移动。

纯逻辑模块，游戏控制通过 NavControls 协议注入，由任务层（BaseEfTask）实现。

关键行为：
- step() 由任务循环按 ~0.2s 节拍调用，传入最新玩家坐标（WS 位置流）；
- 同一格连续卡住：第 2 次重规划吸附排除墙格近旁（换方向），第 3 次直接失败；
- 每次成功规划触发 on_plan 回调（任务层记录拐点日志）；
- 战斗中自动暂停，结束自动从当前位置重吸附重规划；
- 滑索边走 ride_zip 钩子，传送首跳走 teleport 钩子；
- 冒险起始段（起点偏离图）先走向最近可走格（吸附带直线视线检查，不穿墙）。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Protocol

from src.nav.grid import GridMap, _DIRS
from src.nav.grid_finder import GridFinder, GridPlan

IDLE = "idle"
MOVING = "moving"
PAUSED = "paused"
DONE = "done"
FAILED = "failed"

PAUSE_COMBAT = "战斗中"


def angle_delta(target: float, current: float) -> float:
    """最小角度差（度）：返回 target - current 的 [-180, 180] 归一化值。"""
    return (float(target) - float(current) + 180.0) % 360.0 - 180.0


def heading_to(x: float, z: float, tx: float, tz: float) -> float:
    """从 (x,z) 指向 (tx,tz) 的世界朝向（度，0-360）。

    坐标约定：x=东、z=北、y=高；正北为 0°，正西为 90°，角度逆时针增大。
    """
    return (math.degrees(math.atan2(x - tx, tz - z)) + 360.0) % 360.0


@dataclass
class RunnerConfig:
    arrive_radius: float = 1.0       # 到达中间 waypoint 判定半径（米，xz 平面）
    goal_radius: float = 3.0         # 到达最终目的地判定半径（米，xz 平面）
    heading_tol_deg: float = 10.0    # 朝向误差容忍（度），超限先转向
    stuck_window_s: float = 3.0      # 卡住判定时间窗（秒）
    stuck_min_dist: float = 0.5      # 时间窗内位移低于该值判定卡住（米）
    allow_teleport: bool = True      # 寻路是否允许零成本传送
    snap_radius: float = 4.0         # 起点吸附半径（米）
    allow_risk: bool = True          # 吸附失败时冒险规划（就近图节点/未知格）
    risk_cost: float = 5.0           # 未知格风险代价（相对 free 格 1）
    risk_margin: int = 30            # 风险扩展边界外扩（格）
    wall_margin: int = 0             # 离墙代价：free 格离墙 < 该值(格) 时加代价（0=关）
    wall_penalty: float = 0.0        # 离墙缺额每格加的代价（>0 且 wall_margin>0 才生效）
    max_stuck_recovery: int = 2      # 卡住脱困(侧移+前进)最多尝试次数，之后换方向/失败
    prediction_horizon: float = 0.0  # 位置外推补偿 WS 延迟（秒）；0=关闭
    max_predict_dist: float = 6.0    # 单次外推最大距离（米），防止转向时飞过头


class NavControls(Protocol):
    def heading(self) -> float | None:
        """当前角色朝向（世界系 yaw，度，0-360）；读不到返回 None。"""
        ...

    def turn(self, delta_deg: float) -> None:
        """旋转角色 delta_deg 度（正值 = 世界系 yaw 增大的方向）。"""
        ...

    def walk(self, held: bool) -> None:
        """按住（True）/松开（False）前进键 W。"""
        ...

    def ride_zip(self, start_pos: tuple, end_pos: tuple) -> None:
        """执行一段滑索：从 start_pos 附近上索滑到 end_pos。"""
        ...

    def teleport(self, node_pos: tuple) -> None:
        """传送到传送点 node_pos 附近（零成本传送边执行）。"""
        ...


class NavRunner:
    """网格寻路执行状态机。"""

    def __init__(self, grid: GridMap, controls: NavControls, cfg: RunnerConfig | None = None):
        self.grid = grid
        self.controls = controls
        self.cfg = cfg or RunnerConfig()
        self.finder = GridFinder(grid, risk_cost=self.cfg.risk_cost,
                                 risk_margin=self.cfg.risk_margin,
                                 wall_margin=self.cfg.wall_margin,
                                 wall_penalty=self.cfg.wall_penalty)
        self.state = IDLE
        self.reason = ""
        self._paused_reason = ""
        self._goal: tuple | None = None
        self._plan: GridPlan | None = None
        self._idx = 0
        self._zip_riding = False
        self._zip_target = -1
        self._last_pos: tuple = (0.0, 0.0, 0.0)
        self._walk_started_at: float | None = None
        self._walk_start_pos: tuple = (0.0, 0.0)
        self._combat_check = None
        self.on_plan = None     # 可注入：on_plan(GridPlan)，每次成功规划后回调（任务层记拐点日志）
        self.recover_stuck_cb = None  # 可注入：recover_stuck()，卡住时先尝试侧移+前进脱困
        self._stuck_cell: tuple | None = None   # 连续卡住的格（用于换吸附目标）
        self._stuck_count = 0
        self._recovery_attempts = 0   # 已尝试的卡住脱困次数
        # 位置历史（航位推算：补偿 WS 位置流延迟）
        self._pos_hist: list = []               # [(monotonic, x, z)]，最多保留 ~10 条
        self._predicted: tuple | None = None    # 最近一次外推的 (x, z)

    # ---------- 控制 ----------

    def set_combat_check(self, fn) -> None:
        self._combat_check = fn

    @property
    def risky(self) -> bool:
        """当前规划是否为冒险路线（起点/终点偏离已走过区域）。"""
        return bool(self._plan is not None and self._plan.risky)

    def navigate_to(self, goal: tuple, start: tuple | None = None) -> bool:
        """规划并开始导航。start 为实际起点；缺省使用最近一次 step 的位置。"""
        self._goal = tuple(float(v) for v in goal)
        if start is not None:
            self._last_pos = tuple(float(v) for v in start)
        self._stuck_cell = None
        self._stuck_count = 0
        res = self.finder.find_path(self._last_pos, self._goal,
                                    snap_radius=self.cfg.snap_radius,
                                    allow_risk=self.cfg.allow_risk,
                                    allow_teleport=self.cfg.allow_teleport)
        if not res.ok:
            self.state = FAILED
            self.reason = res.reason
            return False
        self._adopt_plan(res)
        self.state = MOVING
        self.reason = ""
        return True

    def pause(self, reason: str = "手动") -> None:
        if self.state == MOVING:
            self.controls.walk(False)
            self.state = PAUSED
            self._paused_reason = reason

    def resume(self) -> None:
        if self.state == PAUSED:
            self._do_resume()

    def abort(self) -> None:
        self.controls.walk(False)
        self.state = IDLE
        self._plan = None
        self._idx = 0
        self._zip_riding = False
        self._stuck_cell = None
        self._stuck_count = 0
        self.reason = ""

    # ---------- 内部 ----------

    def _do_pause(self, reason: str) -> None:
        self.controls.walk(False)
        self.state = PAUSED
        self._paused_reason = reason

    def _do_resume(self) -> None:
        self._paused_reason = ""
        if self._goal is None:
            self.state = IDLE
            return
        res = self.finder.find_path(self._last_pos, self._goal,
                                    snap_radius=self.cfg.snap_radius,
                                    allow_risk=self.cfg.allow_risk,
                                    allow_teleport=self.cfg.allow_teleport)
        if not res.ok:
            self.state = FAILED
            self.reason = res.reason
            return
        self._adopt_plan(res)
        self.state = MOVING

    def _adopt_plan(self, res: GridPlan) -> None:
        self._plan = res
        self._zip_riding = False
        self._walk_started_at = None
        self._walk_start_pos = (0.0, 0.0)
        if self.on_plan is not None:
            try:
                self.on_plan(res)
            except Exception:
                pass
        if res.via_teleport and res.waypoints:
            # 首跳传送：直接传送到路径首格
            self.controls.teleport(res.waypoints[0])
            self._idx = 1 if len(res.waypoints) > 1 else 0
        elif res.risky_start:
            # 冒险起始段：先走向最近可走格
            self._idx = 0
        else:
            self._idx = 1 if len(res.waypoints) > 1 else 0

    def _reset_stuck(self, now: float) -> None:
        self._walk_started_at = now
        self._walk_start_pos = (self._last_pos[0], self._last_pos[2])

    def _handle_stuck(self) -> None:
        self.controls.walk(False)
        cell = self.grid.cell_of(self._last_pos[0], self._last_pos[1], self._last_pos[2])
        if cell == self._stuck_cell:
            self._stuck_count += 1
        else:
            self._stuck_cell = cell
            self._stuck_count = 1
            self._recovery_attempts = 0  # 换到新卡住点，重新允许脱困
        # 先尝试脱困：侧移(A/D)+前进(W)，最多 max_stuck_recovery 次；仍不动再换方向/失败
        if (self.recover_stuck_cb is not None
                and self._recovery_attempts < self.cfg.max_stuck_recovery):
            self._recovery_attempts += 1
            try:
                self.recover_stuck_cb()
            except Exception:
                pass
            # 重新计时，观察是否因此移动（不重置 recovery_attempts，避免无限循环）
            self._walk_started_at = now
            self._walk_start_pos = (self._last_pos[0], self._last_pos[2])
            if self.state != FAILED:
                self.state = MOVING
            return
        if self._goal is None:
            self.state = FAILED
            self.reason = "卡住且无目标"
            return
        # 同一位置连续卡住：换个吸附目标（排除当前格及其相邻格），
        # 避免反复撞同一面墙；连卡 3 次直接判失败，终止无限循环。
        if self._stuck_count >= 3:
            self.state = FAILED
            self.reason = (
                f"同一位置连续卡住 {self._stuck_count} 次："
                f"被障碍阻挡，请检查地图/标记墙")
            return
        snap_exclude = set()
        if self._stuck_count >= 2:
            snap_exclude = {self._stuck_cell}
            if self._stuck_cell is not None:
                ix, iy, iz = self._stuck_cell
                snap_exclude |= {(ix + dx, iy + dy, iz + dz) for dx, dy, dz in _DIRS}
        res = self.finder.find_path(self._last_pos, self._goal,
                                    snap_radius=self.cfg.snap_radius,
                                    allow_risk=self.cfg.allow_risk,
                                    allow_teleport=self.cfg.allow_teleport,
                                    snap_exclude=snap_exclude)
        if not res.ok:
            self.state = FAILED
            self.reason = f"卡住且重规划失败: {res.reason}"
            return
        self._adopt_plan(res)
        self.state = MOVING

    def _advance(self) -> None:
        self._idx += 1
        self._zip_riding = False
        # 到达新航点说明能移动：重置连续卡住计数
        self._stuck_cell = None
        self._stuck_count = 0
        self._reset_stuck(time.monotonic())
        if self._plan is None or self._idx >= len(self._plan.waypoints):
            self.controls.walk(False)
            self.state = DONE
            self.reason = ""

    def _walk_pos(self) -> tuple:
        """行走判定用的 (x, z)：开启预测时用外推位（补偿 WS 位置流延迟）。

        外推只在有近期速度且距离有界时生效；关闭预测或数据不足时退回原始位置。
        """
        if self._predicted is not None:
            return self._predicted
        return self._last_pos[0], self._last_pos[2]

    def current_target_info(self) -> dict | None:
        """返回当前想要去的目标信息（供位置日志等展示）。

        Returns:
            dict | None：{waypoint:(x,y,z), yaw:度, delta_deg:度, turning:bool}；
            无计划或不在移动时返回 None。
        """
        if self._plan is None or not self._plan.waypoints or self.state != MOVING:
            return None
        idx = min(self._idx, len(self._plan.waypoints) - 1)
        wp = tuple(float(v) for v in self._plan.waypoints[idx])
        px, pz = self._last_pos[0], self._last_pos[2]
        yaw = heading_to(px, pz, wp[0], wp[2])
        cur_yaw = self.controls.heading()
        delta = angle_delta(yaw, cur_yaw) if cur_yaw is not None else None
        turning = delta is not None and abs(delta) > self.cfg.heading_tol_deg
        return {
            "waypoint": wp,
            "yaw": yaw,
            "delta_deg": delta,
            "turning": bool(turning),
            "idx": self._idx,
            "total": len(self._plan.waypoints),
        }

    def _predict(self, now: float) -> tuple:
        """按最近移动速度外推当前位置 (x, z)。

        - 只用最近 ~4 段位移的中位速度，速度夹在 (0, 8m/s]；
        - 外推距离夹在 max_predict_dist 内，转向（最近两采样几乎不动）时不外推；
        - 预测方向 = 最近两个原始位置的位移方向。
        """
        x, z = self._last_pos[0], self._last_pos[2]
        horizon = float(self.cfg.prediction_horizon)
        if horizon <= 0:
            return x, z
        hist = self._pos_hist
        if len(hist) < 3:
            return x, z
        # 最近两采样位移太小 → 认为在转向/静止，不外推
        t0, p0x, p0z = hist[-2]
        t1, p1x, p1z = hist[-1]
        d_last = math.hypot(p1x - p0x, p1z - p0z)
        if d_last < 0.15:
            return x, z
        speeds = []
        for i in range(max(1, len(hist) - 4), len(hist)):
            ta, ax, az = hist[i - 1]
            tb, bx, bz = hist[i]
            dt = tb - ta
            d = math.hypot(bx - ax, bz - az)
            if dt > 0.01 and d > 0.01:
                speeds.append(min(d / dt, 8.0))
        if not speeds:
            return x, z
        speeds.sort()
        speed = speeds[len(speeds) // 2]  # 中位速度
        dx, dz = p1x - p0x, p1z - p0z
        inv = 1.0 / d_last
        dist = min(speed * horizon, float(self.cfg.max_predict_dist))
        return x + dx * inv * dist, z + dz * inv * dist

    def _move_toward(self, wx: float, wy: float, wz: float) -> None:
        """朝目标点移动：朝向误差大先转视角（转完迈步让身体朝向跟上），否则前进。"""
        px, pz = self._walk_pos()
        need_yaw = heading_to(px, pz, wx, wz)
        cur_yaw = self.controls.heading()
        if cur_yaw is None:
            self.controls.walk(False)
            return
        delta = angle_delta(need_yaw, cur_yaw)
        if abs(delta) > self.cfg.heading_tol_deg:
            # 先松开 W 再转向（连续移动时避免边转边走）
            self.controls.walk(False)
            # 转视角（turn 内部会迈一步让身体朝向跟上），本轮不额外前进
            self.controls.turn(delta)
            return
        self.controls.walk(True)

    # ---------- 主循环 ----------

    def step(self, position: tuple, now: float | None = None) -> str:
        self._last_pos = tuple(float(v) for v in position)
        if now is None:
            now = time.monotonic()
        # 维护位置历史并外推（供转向/到达判定补偿 WS 延迟）
        self._pos_hist.append((now, self._last_pos[0], self._last_pos[2]))
        if len(self._pos_hist) > 10:
            del self._pos_hist[: len(self._pos_hist) - 10]
        self._predicted = self._predict(now)

        # 战斗自动暂停/恢复
        in_combat = False
        if self._combat_check is not None:
            try:
                in_combat = bool(self._combat_check())
            except Exception:
                in_combat = False
        if in_combat:
            if self.state == MOVING:
                self._do_pause(PAUSE_COMBAT)
            return self.state
        if self.state == PAUSED and self._paused_reason == PAUSE_COMBAT:
            self._do_resume()
            return self.state

        if self.state != MOVING:
            return self.state
        plan = self._plan
        if plan is None or not plan.waypoints:
            self.state = IDLE
            return self.state

        # 滑索滑行中：等位置接近终点
        if self._zip_riding:
            if 0 <= self._zip_target < len(plan.waypoints):
                tx, ty, tz = plan.waypoints[self._zip_target]
                if math.hypot(self._last_pos[0] - tx, self._last_pos[2] - tz) <= self.cfg.arrive_radius:
                    self._advance()
            return self.state

        if self._idx >= len(plan.waypoints):
            self.controls.walk(False)
            self.state = DONE
            return self.state

        px, py, pz = self._last_pos

        # 冒险起始段：走向最近可走格
        if self._idx == 0:
            wx, wy, wz = plan.waypoints[0]
            if math.hypot(px - wx, pz - wz) <= self.cfg.arrive_radius:
                self._idx = 1
                self._reset_stuck(now)
                return self.state
            self._move_toward(wx, wy, wz)
            return self.state

        wp = plan.waypoints[self._idx]
        seg = plan.segments[self._idx - 1] if self._idx - 1 < len(plan.segments) else "walk"

        # 滑索边
        if seg == "zip":
            self.controls.walk(False)
            self.controls.ride_zip(plan.waypoints[self._idx - 1], wp)
            self._zip_riding = True
            self._zip_target = self._idx
            return self.state

        # 到达判定（xz 平面）；最终目的地用 goal_radius（默认 3m）
        dist = math.hypot(px - wp[0], pz - wp[2])
        is_final = self._idx == len(plan.waypoints) - 1
        radius = self.cfg.goal_radius if is_final else self.cfg.arrive_radius
        if dist <= radius:
            self._advance()
            return self.state

        self._move_toward(wp[0], wp[1], wp[2])

        # 卡住检测
        if self._walk_started_at is None:
            self._reset_stuck(now)
        if now - self._walk_started_at >= self.cfg.stuck_window_s:
            moved = math.hypot(px - self._walk_start_pos[0], pz - self._walk_start_pos[1])
            if moved < self.cfg.stuck_min_dist:
                self._handle_stuck()
            else:
                self._reset_stuck(now)
        return self.state
