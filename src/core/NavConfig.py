# -*- coding: utf-8 -*-
"""全局「导航配置」的默认值、说明与**分辨率自适应**选档逻辑。

为什么要独立成全局配置
----------------------
小地图比例尺 ``s``（米/像素）与轴映射都是**按画面分辨率变化**的：小地图圆盘按画面宽缩放，
``s = C / 画面宽``（实测常数 ``C ≈ 1706.5`` 米）。轴映射是 ``diag(s, -s)``（地图系像素 ->
世界系米；世界_z 与地图_y 符号相反）。

把这两个值配在**每个任务**里意味着：换一次分辨率就要把所有任务的配置改一遍，而且
历史值（0.6703 / 0.6862…）是 2560 宽下量的，在 1920 下直接用会差 27%。所以集中到全局，
并按当前分辨率自动选档。

数值来源
--------
``C = 1706.5``：关联工作区 nav-grid-editor 在 1920x1080 下用「小地图贴回拼接总图 + WS
绝对坐标」测得 5 个位置的 m/px 中位数 0.8886、对齐残差全为 (0,0)，离散 0.08%；
等价常数形式 1706.5/1920。分辨率依赖关系与各向同性（<0.25%）均已实测确认。

三个档位的默认值由 ``C`` 反推，保证档位与常数两条路径**完全连续**：
1920 -> 0.8888、2560 -> 0.6666、3840 -> 0.4444。两个都落在实测离散范围内。

本模块不 import ok / GUI，便于纯函数单测。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

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
    CONFIG_GRID_RECOVERY_TIME,
    CONFIG_GRID_RISK_COST,
    CONFIG_GRID_SHORTCUT_RADIUS,
    CONFIG_GRID_STUCK_DISTANCE,
    CONFIG_GRID_STUCK_WINDOW,
    CONFIG_GRID_TICK,
    CONFIG_GRID_TIMEOUT,
    CONFIG_GRID_TURN_TOLERANCE,
    CONFIG_GRID_TURN_WHILE_MOVING,
    CONFIG_GRID_USE_ZIP_LINES,
    CONFIG_GRID_WALL_PENALTY,
    CONFIG_GRID_WAYPOINT_RADIUS,
    CONFIG_GRID_WAYPOINT_TOLERANCE,
    CONFIG_GRID_ZOOM,
    DEFAULT_GRID_NAV_CONFIG,
    GRID_NAV_CONFIG_DESCRIPTION,
)

__all__ = [
    "DEFAULT_NAV_CONFIG",
    "DEFAULT_SCALE_CONSTANT",
    "NAV_CONFIG_DESCRIPTION",
    "NAV_CONFIG_GROUP_GRID",
    "NAV_CONFIG_GROUP_KEY",
    "NAV_CONFIG_GROUP_LOCALIZATION",
    "NAV_CONFIG_GROUP_MOVEMENT",
    "NAV_CONFIG_GROUP_PLANNING",
    "NAV_CONFIG_NAME",
    "NAV_CONFIG_TYPE",
    "NAV_CONTENT_KEY",
    "NAV_MAP_ID_KEY",
    "NAV_MATRIX_SUFFIX",
    "NAV_PLAN_ONLY_KEY",
    "NAV_RESOLUTION_TIERS",
    "NAV_SCALE_CONSTANT_KEY",
    "NAV_SCALE_SUFFIX",
    "NAV_WAIT_POSITION_TIMEOUT_KEY",
    "NAV_WS_ACCOUNT_KEY",
    "NavProfile",
    "nav_profile_for_width",
    "tier_for_width",
]

NAV_CONFIG_NAME = "Nav Config"

NAV_CONTENT_KEY = "真值content"
NAV_WS_ACCOUNT_KEY = "真值地图账号"
NAV_SCALE_CONSTANT_KEY = "比例尺常数(米)"
NAV_MAP_ID_KEY = "地图id(留空自动)"
NAV_PLAN_ONLY_KEY = "仅规划不移动"
NAV_WAIT_POSITION_TIMEOUT_KEY = "等待定位超时(秒)"

NAV_CONFIG_GROUP_KEY = "导航配置分类"
NAV_CONFIG_GROUP_LOCALIZATION = "定位与比例尺"
NAV_CONFIG_GROUP_GRID = "网格与滑索"
NAV_CONFIG_GROUP_PLANNING = "规划与代价"
NAV_CONFIG_GROUP_MOVEMENT = "行走与脱困"

#: 档位名与它对应的画面宽度（像素）。档位键名形如 ``1K比例尺(米/像素)`` / ``1K轴映射(逗号4值)``。
NAV_RESOLUTION_TIERS: tuple[tuple[str, int], ...] = (("1K", 1920), ("2K", 2560), ("4K", 3840))

NAV_SCALE_SUFFIX = "比例尺(米/像素)"
NAV_MATRIX_SUFFIX = "轴映射(逗号4值)"

#: ``s = C / 画面宽``。实测：1920 下 5 个位置中位 0.8886、残差全 (0,0)、离散 0.08%。
DEFAULT_SCALE_CONSTANT = 1706.5


def _tier_scale(width: int) -> float:
    """档位默认比例尺：由常数反推，保证与"常数路径"完全连续。"""
    return round(DEFAULT_SCALE_CONSTANT / width, 4)


def _tier_matrix(scale: float) -> str:
    """档位默认轴映射：diag(+s, -s)。"""
    return f"{scale},0,0,{-scale}"


def _tier_defaults() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, width in NAV_RESOLUTION_TIERS:
        scale = _tier_scale(width)
        out[f"{name}{NAV_SCALE_SUFFIX}"] = scale
        out[f"{name}{NAV_MATRIX_SUFFIX}"] = _tier_matrix(scale)
    return out


DEFAULT_NAV_CONFIG: dict[str, Any] = {
    NAV_CONFIG_GROUP_KEY: NAV_CONFIG_GROUP_LOCALIZATION,
    NAV_CONTENT_KEY: "",
    NAV_WS_ACCOUNT_KEY: "",
    NAV_SCALE_CONSTANT_KEY: DEFAULT_SCALE_CONSTANT,
    **_tier_defaults(),
    NAV_MAP_ID_KEY: "",
    NAV_PLAN_ONLY_KEY: False,
    NAV_WAIT_POSITION_TIMEOUT_KEY: 30.0,
    **DEFAULT_GRID_NAV_CONFIG,
}

NAV_CONFIG_DESCRIPTION: dict[str, str] = {
    NAV_CONFIG_GROUP_KEY: "选择要显示的全局导航配置分类。",
    NAV_CONTENT_KEY: "官方地图 hg/check 的 data.content，提供绝对坐标（锚点/真值）。"
                     "留空则按「真值地图账号」或当前登录账号自动取。",
    NAV_WS_ACCOUNT_KEY: "content 为空时，从这个账号的地图同步里取 content。"
                        "留空则用当前登录账号。",
    NAV_SCALE_CONSTANT_KEY: "小地图比例尺常数 C：```比例尺 = C / 画面宽度(px)```。"
                            "分辨率不落在下面三个档位时的兜底，实测 C ≈ 1706.5 米"
                            "（即画面整宽对应的世界距离）。",
    **{
        f"{name}{NAV_SCALE_SUFFIX}": f"画面宽 {width}px（{name}）下的比例尺（米/像素）。"
                                     f"默认由常数反推：{DEFAULT_SCALE_CONSTANT}/{width}。"
                                     f"若在这个分辨率下单独标定过，可覆盖成实测值。"
        for name, width in NAV_RESOLUTION_TIERS
    },
    **{
        f"{name}{NAV_MATRIX_SUFFIX}": f"画面宽 {width}px（{name}）下的轴映射（米/像素），"
                                      f"逗号4值 a11,a12,a21,a22。默认 diag(+s, -s)"
                                      f"（地图系像素 -> 世界系米；世界_z 与地图_y 符号相反）。"
        for name, width in NAV_RESOLUTION_TIERS
    },
    NAV_MAP_ID_KEY: "可选。留空时使用实时位置流里的 mapId 加载导航网格。",
    NAV_PLAN_ONLY_KEY: "开启后只输出规划结果和路径日志，不按 W、不转鼠标。",
    NAV_WAIT_POSITION_TIMEOUT_KEY: "规划或导航开始前，等待融合坐标锚定的最长时间。",
    **GRID_NAV_CONFIG_DESCRIPTION,
}

NAV_CONFIG_TYPE = {
    NAV_CONFIG_GROUP_KEY: {
        "type": "drop_down",
        "options": [
            NAV_CONFIG_GROUP_LOCALIZATION,
            NAV_CONFIG_GROUP_GRID,
            NAV_CONFIG_GROUP_PLANNING,
            NAV_CONFIG_GROUP_MOVEMENT,
        ],
        "sub_configs": {
            NAV_CONFIG_GROUP_LOCALIZATION: [
                NAV_CONTENT_KEY,
                NAV_WS_ACCOUNT_KEY,
                NAV_SCALE_CONSTANT_KEY,
                *[
                    f"{name}{suffix}"
                    for name, _ in NAV_RESOLUTION_TIERS
                    for suffix in (NAV_SCALE_SUFFIX, NAV_MATRIX_SUFFIX)
                ],
            ],
            NAV_CONFIG_GROUP_GRID: [
                NAV_MAP_ID_KEY,
                CONFIG_GRID_DIR,
                CONFIG_GRID_FILE,
                CONFIG_GRID_ZOOM,
                CONFIG_GRID_USE_ZIP_LINES,
                NAV_PLAN_ONLY_KEY,
                NAV_WAIT_POSITION_TIMEOUT_KEY,
            ],
            NAV_CONFIG_GROUP_PLANNING: [
                CONFIG_GRID_CALIBRATION_WAYPOINTS,
                CONFIG_GRID_GOAL_RADIUS,
                CONFIG_GRID_WAYPOINT_RADIUS,
                CONFIG_GRID_WAYPOINT_TOLERANCE,
                CONFIG_GRID_MAX_EXPAND,
                CONFIG_GRID_ALLOW_UNKNOWN,
                CONFIG_GRID_RISK_COST,
                CONFIG_GRID_SHORTCUT_RADIUS,
                CONFIG_GRID_MARGIN,
                CONFIG_GRID_WALL_PENALTY,
                CONFIG_GRID_FRONTIER_MARGIN,
                CONFIG_GRID_FRONTIER_PENALTY,
            ],
            NAV_CONFIG_GROUP_MOVEMENT: [
                CONFIG_GRID_HEADING_TOLERANCE,
                CONFIG_GRID_TURN_TOLERANCE,
                CONFIG_GRID_MAX_TURN_ROUNDS,
                CONFIG_GRID_TURN_WHILE_MOVING,
                CONFIG_GRID_MOVING_TURN_GAIN,
                CONFIG_GRID_MOVING_TURN_MAX_DEG,
                CONFIG_GRID_MOVING_TURN_MAX_START_DEG,
                CONFIG_GRID_MOVING_TURN_MIN_DISTANCE,
                CONFIG_GRID_STUCK_WINDOW,
                CONFIG_GRID_STUCK_DISTANCE,
                CONFIG_GRID_MAX_RECOVERIES,
                CONFIG_GRID_RECOVERY_TIME,
                CONFIG_GRID_MAX_REPLANS,
                CONFIG_GRID_TIMEOUT,
                CONFIG_GRID_TICK,
            ],
        },
    },
}


@dataclass(frozen=True)
class NavProfile:
    """一次选档的结果。"""

    scale: float
    #: 轴映射的逗号4值字符串（与任务配置同格式，交给 parse_map_to_world 解析）
    map_to_world: str
    #: 选中的来源说明，写日志用
    source: str
    #: 命中的档位名（1K/2K/4K）；走常数兜底时为 None
    tier: str | None = None


def tier_for_width(width: int) -> tuple[str, int] | None:
    """宽度**精确**命中档位时返回 ``(档位名, 档位宽度)``，否则 None。

    刻意用精确匹配而不是"最接近"：窗口化下的任意宽度（如 1600）不代表它的比例尺可以用
    1920 档位的值，那种情况应该走 ``C / 宽度`` 的连续换算。
    """
    try:
        value = int(width)
    except (TypeError, ValueError):
        return None
    for name, tier_width in NAV_RESOLUTION_TIERS:
        if value == tier_width:
            return name, tier_width
    return None


def _positive_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number > 0 else 0.0


def nav_profile_for_width(width: Any, values: Mapping[str, Any] | None) -> NavProfile | None:
    """按当前画面宽度给出比例尺与轴映射。

    分辨率**总是**自适应——没有"关掉"这个开关：比例尺随画面宽变化是物理事实，
    配在任务里就必然会在换分辨率时过期，所以导航侧只认这一处。

    Args:
        width: 当前画面宽度（像素）；<=0 表示还不知道分辨率。
        values: 全局「导航配置」的取值（键见本模块常量）。

    Returns:
        :class:`NavProfile`；宽度未知（拿不到分辨率）或档位/常数都取不到有效值时返回
        **None**，调用方应据此明确报错而不是猜一个值。
    """
    width_value = _positive_float(width)
    if width_value <= 0 or not values:
        return None

    hit = tier_for_width(int(width_value))
    if hit is not None:
        name, tier_width = hit
        scale = _positive_float(values.get(f"{name}{NAV_SCALE_SUFFIX}"))
        matrix = str(values.get(f"{name}{NAV_MATRIX_SUFFIX}") or "").strip()
        if scale > 0 and matrix:
            return NavProfile(scale, matrix, f"{name}档位({tier_width}px)", name)

    constant = _positive_float(values.get(NAV_SCALE_CONSTANT_KEY))
    if constant > 0:
        scale = constant / width_value
        return NavProfile(scale, f"{scale:.6f},0,0,{-scale:.6f}",
                          f"比例尺常数/宽度({int(width_value)}px)")

    return None
