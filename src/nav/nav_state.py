"""网格导航每一拍的显式状态与会话数据。

``navigate_grid_to`` 原来把三件事放在同一个循环里：每一拍的判定分支、跨拍累计的会话
计数，以及一份手写的运行状态重置清单。本模块把状态数据抽出来：

- :class:`NavState` —— 每一拍恰好命中一个状态；执行类状态的名字与
  :mod:`src.nav.route_follower` 的动作常量同值，定位闸门类状态另有名字；
- :class:`GridRouteRequest` —— 一次导航请求里跨拍不变的参数；
- :class:`NavSession` —— 只对本次导航有效的可变状态，随 ``navigate_grid_to`` 建立、
  结束即丢弃；
- :class:`NavTick` —— 一拍内不变的输入快照（帧、定位、朝向质量、跟随器动作）。

这里没有"上一次处于哪个状态"的跨拍字段：主循环每一拍都按固定优先级重新判定一次，
所以状态表示"这一拍走哪个分支"。跨导航保留的运行模式（滑索快照缓存、滑索行走模式、
被封禁的滑索连接）仍留在任务层，不放进会话，避免把"本次导航"和"跨导航缓存"混进
同一个命名空间。

本模块不依赖 ``ok`` 框架，只依赖 :mod:`src.nav.route_follower` 的动作常量，便于单测。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

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
)

__all__ = [
    "GridRouteRequest",
    "NavSession",
    "NavState",
    "NavTick",
    "SyncReason",
    "SyncWait",
]


class NavState(StrEnum):
    """``navigate_grid_to`` 每一拍的状态。

    前六个是定位就绪闸门：位置还不可信，只停车等待或发起静止校准。后面是路线与执行
    阶段，其中 ``WALK``/``TURN``/``WAIT``/``NARROW_CALIBRATE``/``ZIP_LINE``/
    ``STUCK``/``REPLAN``/``DONE``/``FAILED`` 与 :mod:`src.nav.route_follower`
    的动作常量同值，方便把跟随器输出直接映射成状态。

    **这里的声明顺序不代表判定优先级。** 优先级实际分三段写在任务层：
    :meth:`~src.tasks.navigation.mixin.grid_navigation_mixin.GridNavigationMixin._grid_nav_classify`
    （闸门）、``_grid_nav_follow``（规划 / 滑索接近）、``_grid_nav_action_state``
    （跟随器动作），表格见 ``docs/dev/网格导航.md`` §7.1。前六个闸门确实按声明顺序
    判定，执行类则按"跟随器动作 → 状态"的映射走，与枚举顺序无关。
    """

    # --- 定位就绪闸门：先停车，直到可以信任位置 ---
    WAIT_POSE = "wait_pose"
    WAIT_ANCHOR = "wait_anchor"
    WAIT_TRUST = "wait_trust"
    WAIT_ODOM = "wait_odom"
    CALIBRATE = "calibrate"
    WAIT_HEADING = "wait_heading"

    # --- 路线与执行：名字与跟随器输出的动作一致 ---
    PLAN = "plan"
    ZIP_LINE_APPROACH = "zip_line_approach"
    WAYPOINT_ARRIVED = "waypoint_arrived"
    STOP_REACHED = "stop_reached"
    REPLAN = REPLAN
    ZIP_LINE = ZIP_LINE
    DONE = DONE
    STUCK = STUCK
    NARROW_CALIBRATE = NARROW_CALIBRATE
    TURN = TURN
    WALK = WALK
    WAIT = WAIT
    FAILED = FAILED


class SyncReason(StrEnum):
    """为什么发起这一次静止校准。

    静止校准是导航里唯一的"主动停车点"，触发它的原因有六种。把原因显式带进
    等待函数，日志与状态提示才回答得了"为什么又停下来校准"。
    """

    #: 重锚后绝对坐标不可信，必须先校准再规划。
    TRUST = "trust"
    #: 定位服务通过 ``sync_needed`` 主动请求。
    SYNC_NEEDED = "sync_needed"
    #: 走过指定数量的航点后的周期校准。
    WAYPOINT_INTERVAL = "waypoint_interval"
    #: 偏航后原地校准，消除定位偏差再重新规划。
    OFF_ROUTE = "off_route"
    #: 已回到走过的已知格，继续原目标前先校准。
    BACKTRACK_DONE = "backtrack_done"
    #: 进入一格宽通路前，避免带着偏差蹭墙。
    NARROW_ENTRY = "narrow_entry"

    @property
    def label(self) -> str:
        """显示文案：日志与状态提示用，枚举值本身保持稳定以便检索。"""
        return _SYNC_REASON_LABELS[self]


_SYNC_REASON_LABELS: dict[SyncReason, str] = {
    SyncReason.TRUST: "定位重新校准",
    SyncReason.SYNC_NEEDED: "定位服务请求",
    SyncReason.WAYPOINT_INTERVAL: "航点间隔",
    SyncReason.OFF_ROUTE: "偏航后原地校准",
    SyncReason.BACKTRACK_DONE: "回到已知格后校准",
    SyncReason.NARROW_ENTRY: "窄道入口前",
}


@dataclass(frozen=True)
class SyncWait:
    """一次静止校准等待的结果。

    ``synced`` 为 True 表示发生了真实重锚或当前估计已与 WS 对齐；
    ``timed_out`` 为 True 表示本次导航已经超时，调用方必须立刻结束。
    """

    synced: bool
    timed_out: bool


@dataclass(frozen=True)
class GridRouteRequest:
    """一次 ``navigate_grid_to`` 调用里跨拍不变的参数。"""

    goal: tuple[float, float]
    #: 目标的**高度**。分层地图里"终点在第几层"由它判定（只看 Y，不看 levelId）；
    #: 为 None 表示调用方只知道平面坐标，层判定会兜底到大地图。
    goal_y: float | None = None
    map_id: str = ""
    grid_path: str | None = None
    grid_dir: str | None = None
    zoom: str | None = None
    stop_distance: float | None = None


@dataclass
class NavSession:
    """只对本次导航有效的可变状态。

    旧实现把这些值摊在主循环的局部变量里，只有一部分被 :meth:`GridNavigationMixin.
    _reset_grid_navigation_run_state` 覆盖；收进一个对象后，"哪些状态属于本次导航"
    由类型本身回答。
    """

    request: GridRouteRequest
    deadline: float
    limit: float
    tick: float
    #: 已经发生过的重规划次数（有进展时归还额度）。
    replans: int = 0
    #: 已经用掉的脱困次数（有进展时归还额度）。
    recoveries: int = 0
    #: 历史上离目标最近的距离，用于判断"这一拍有没有进展"。
    best_goal_distance: float = math.inf
    #: 距上次校准又走过了几个航点。
    waypoints_since_calibration: int = 0
    #: 校准失败后的重试时间点，避免每拍都请求一次静止校准。
    next_sync_retry_at: float = 0.0
    #: 最近一次见过的校准序号；变化说明发生过真重锚。
    last_sync_seq: int = 0
    #: 是否已经规划过至少一次，用于区分首次规划与重规划。
    had_plan: bool = False
    #: 偏航后先返回的"最近走过的已知格"；None 表示直接规划原目标。
    recovery_goal: tuple[float, float] | None = None
    #: 本次导航实际走过的已知格，仅用于偏航后寻找安全返回点。
    visited_cells: dict[tuple[int, int], tuple[float, float]] = field(default_factory=dict)
    #: 分层路线（:class:`~src.nav.layered_planner.LayeredRoute`）；None 表示本次走的是
    #: 原来的单层路径。有它时 ``_grid_nav_follower`` 始终指向**当前分段**的跟随器。
    route: Any = None
    #: 当前正在走第几段。
    segment_index: int = 0
    #: **当前正在使用的网格（层）**——拓扑记录，不是每拍重算出来的。
    #:
    #: 分层路线是"按段换网格"的：跨过一条连接、切到下一段之后，当前网格就自然变成落点
    #: 那一层。重规划时必须以它为准，因为 ``floor_regions`` 的标注很稀疏，站在层上的位置
    #: 大多会落到"大地图"兜底，照兜底重算会把还在层上的角色丢回主图。``None`` 表示本次
    #: 导航还没有用过任何网格（首次规划），此时才需要用高度 / X-Z 去判起点在哪一层。
    active_tier: str | None = None
    #: 起终点解析出来的层，只用于日志与状态提示。
    start_tier: str = ""
    goal_tier: str = ""

    @property
    def goal(self) -> tuple[float, float]:
        """本次导航的最终目标。"""
        return self.request.goal

    @property
    def stop_distance(self) -> float | None:
        """提前结束的接近距离；None 表示必须走到 ``goal_radius``。"""
        return self.request.stop_distance

    @property
    def goal_y(self) -> float | None:
        """目标高度；调用方只给了平面坐标时为 None。"""
        return self.request.goal_y

    @property
    def layered(self) -> bool:
        """本次导航是否在走分层路线。"""
        return self.route is not None

    def note_progress(self, distance_to_goal: float) -> bool:
        """目标距离明显刷新时归还重规划与脱困额度，返回是否有进展。"""
        if distance_to_goal + 0.5 < self.best_goal_distance:
            self.best_goal_distance = distance_to_goal
            self.replans = 0
            self.recoveries = 0
            return True
        return False

    def note_waypoint_arrival(self) -> int:
        """记一次航点到达，返回累计未校准航点数。"""
        self.waypoints_since_calibration += 1
        return self.waypoints_since_calibration

    def note_sync(self, sync_seq: int) -> bool:
        """记录校准序号；真重锚会整体平移世界坐标，历史格不再可信。"""
        if sync_seq == self.last_sync_seq:
            return False
        self.last_sync_seq = sync_seq
        self.visited_cells.clear()
        return True


@dataclass
class NavTick:
    """一拍内不变的输入快照；``step`` 与 ``pending_zip_step`` 由判定阶段填入。"""

    session: NavSession
    frame: Any
    pose: dict | None
    x: float | None
    z: float | None
    map_id: str
    sync_seq: int
    #: 过完朝向质量阈值后的朝向；None 表示这一拍没有可用朝向。
    heading: float | None
    heading_score: float | None
    #: 朝向质量阈值，供需要对准的动作复用。
    min_score: float = 0.0
    step: Any = None
    pending_zip_step: Any = None

    @property
    def tick(self) -> float:
        """控制周期（秒）。"""
        return self.session.tick

    @property
    def deadline(self) -> float:
        """本次导航的截止时间。"""
        return self.session.deadline

    @property
    def limit(self) -> float:
        """本次导航的超时上限（秒）。"""
        return self.session.limit

    @property
    def position(self) -> tuple[float, float]:
        """当前融合坐标；只有过完锚定闸门的状态才会取用。"""
        return (float(self.x), float(self.z))
