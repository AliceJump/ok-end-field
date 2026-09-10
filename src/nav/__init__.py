# -*- coding: utf-8 -*-
"""导航网格：表示、读写与规划。

- :mod:`src.nav.grid_io` —— 稠密单值枚举网格（``0=未知/1=可行走/2=阻挡``）、
  坐标换算、邻域、贴墙安全距离（clearance），以及 ``*.grid.npz`` 的读写
  （格式规范见模块文档，编辑器按它生成）。
- :mod:`src.nav.grid_planner` —— 在网格上做 A*：未知格按风险加价、禁斜穿墙角、
  起终点吸附、视线简化，输出世界坐标航点。

执行状态机（按航点走、卡住重规划、战斗暂停）等导航栈恢复时再加。
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
    "load_grid",
    "new_grid",
    "save_grid",
]
