# -*- coding: utf-8 -*-
"""二维网格导航的纯 Python 核心。

- :mod:`src.nav.grid_io` —— 稠密单值枚举网格（``0=未知/1=可行走/2=阻挡``）、
  坐标换算、邻域、贴墙安全距离，以及 ``*.grid.npz`` 读写。
- :mod:`src.nav.grid_planner` —— 在网格上做 A*：未知格按风险加价、禁斜穿墙角、
  起终点吸附、视线简化，并可把用户滑索作为跨区域转移边。
- :mod:`src.nav.zip_line_graph` —— 解析官方地图 ``saveMarks``，按 80m/110m
  连接半径构建用户滑索图。
- :mod:`src.nav.route_follower` —— 消费规划结果的状态机：切航点、转向、行走、
  滑索执行、偏航重规划、卡住判定与终点确认。它不直接操作游戏输入。

任务层（``GridNavigationMixin``）负责把这里输出的动作转换成键盘、鼠标和定位校准；
本包不依赖 ``ok`` 框架，便于独立测试与复用。完整架构见
``docs/dev/导航与小地图定位.md``。
"""

from src.nav.grid_io import (
    AXIS_CONVENTION,
    CELL_BLOCKED,
    CELL_FREE,
    CELL_NAMES,
    CELL_UNKNOWN,
    GRID_MAGIC,
    GRID_SUFFIX,
    SCHEMA_VERSION,
    DenseGrid,
    GridMeta,
    load_grid,
    new_grid,
    save_grid,
)
from src.nav.grid_planner import GridPlanner, PlanResult
from src.nav.zip_line_graph import ZipLineGraph, ZipLineLink, ZipLineNode, ZipLineStep

__all__ = [
    "AXIS_CONVENTION",
    "CELL_BLOCKED",
    "CELL_FREE",
    "CELL_NAMES",
    "CELL_UNKNOWN",
    "GRID_MAGIC",
    "GRID_SUFFIX",
    "SCHEMA_VERSION",
    "DenseGrid",
    "GridMeta",
    "GridPlanner",
    "PlanResult",
    "ZipLineGraph",
    "ZipLineLink",
    "ZipLineNode",
    "ZipLineStep",
    "load_grid",
    "new_grid",
    "save_grid",
]
