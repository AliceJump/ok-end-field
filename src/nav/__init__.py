# -*- coding: utf-8 -*-
"""导航模块：3D 体素网格地图、网格寻路、执行器。

可达性模型（替换原图模型）：
- 默认所有格子不可达；已知点位的格子标 free（仅当前坐标格，不膨胀）；
- blocked（标记墙/卡住）覆盖 free；
- 寻路 6 连通，free 代价 1、未知格风险 5×、垂直只进 free（落差 1m）、
  传送零成本、滑索为双向特殊边。
"""

from src.nav.grid import GridMap, build_grid
from src.nav.grid_finder import GridFinder, GridPlan
from src.nav.nav_runner import NavRunner, RunnerConfig, NavControls

__all__ = [
    "GridMap",
    "build_grid",
    "GridFinder",
    "GridPlan",
    "NavRunner",
    "RunnerConfig",
    "NavControls",
]
