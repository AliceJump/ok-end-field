"""小地图网格导航调试任务：规划并移动到目标世界坐标。"""

from __future__ import annotations

import math

from qfluentwidgets import FluentIcon

from src.core.BaseEfTask import BaseEfTask
from src.core.NavConfig import (
    NAV_MAP_ID_KEY,
    NAV_PLAN_ONLY_KEY,
    NAV_WAIT_POSITION_TIMEOUT_KEY,
)
from src.icons import Icons
from src.tasks.mixin.grid_navigation_mixin import GridNavigationMixin
from src.tasks.mixin.zip_line_mixin import ZipLineMixin


class MinimapNavigateToPoint(ZipLineMixin, GridNavigationMixin, BaseEfTask):
    """用于验证网格加载、路径规划和自动移动的调试任务。"""

    requires_foreground = True  # 移动与转视角依赖前台输入

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "小地图网格导航"
        self.group_name = "工具与调试"
        self.group_icon = FluentIcon.DEVELOPER_TOOLS
        self.description = "使用二维导航网格与用户滑索规划路径，并自动控制角色走到目标世界坐标"
        self.icon = Icons.Navigation
        self.visible = self.debug

        self._init_grid_navigation_mixin()
        self.default_config = {
            "目标X": 0.0,
            "目标Z": 0.0,
        }
        self.config_description = {
            "目标X": "目标世界坐标 X（米；导航网格列方向）",
            "目标Z": "目标世界坐标 Z（米；导航网格行方向）",
        }
        self.default_config_group.update({
            "目标坐标": [
                "目标X",
                "目标Z",
            ],
        })

    def run(self):
        if not self.in_world():
            self.log_warning("当前不在大世界画面，无法读取小地图位置与朝向", notify=True)
            return False

        goal = (
            float(self.config.get("目标X", 0.0)),
            float(self.config.get("目标Z", 0.0)),
        )
        map_id = str(self._grid_config_get(NAV_MAP_ID_KEY, "") or "").strip()
        self.log_info(f"开始网格导航: 目标=({goal[0]:.2f}, {goal[1]:.2f})", notify=True)

        if self._grid_cfg_bool(NAV_PLAN_ONLY_KEY, False):
            return self._run_plan_only(goal, map_id)

        result = self.navigate_grid_to(goal, map_id=map_id)
        if result:
            self.log_info("网格导航完成", notify=True)
        else:
            self.log_warning("网格导航失败", notify=True)
        return result

    def _run_plan_only(self, goal: tuple[float, float], map_id: str) -> bool:
        """只采样一次实时位置并输出规划结果，不发送移动输入。"""
        position_service = self._get_minimap_position_service()
        if position_service is None:
            self.log_warning("未注册「小地图定位」触发任务，无法获取当前位置", notify=True)
            return False
        if not getattr(position_service, "enabled", True):
            self.log_warning("「小地图定位」触发任务未启用，无法获取当前位置", notify=True)
            return False
        position_service.start_minimap_position(wait_stable=False)
        if not getattr(position_service, "minimap_position_ready", True):
            self.log_warning("小地图定位器未完成初始化，请检查全局「Nav Config」和游戏窗口", notify=True)
            return False
        timeout = max(
            1.0,
            self._grid_cfg_float(NAV_WAIT_POSITION_TIMEOUT_KEY, 30.0),
        )
        started = self.active_time()
        while self.active_time() - started < timeout:
            frame = self.next_frame()
            state = position_service.minimap_position(frame=frame, now=self.active_time())
            x, z = state.get("x"), state.get("z")
            if x is None or z is None:
                self.sleep(0.2)
                continue

            actual_map = str(state.get("map_id") or map_id or "")
            result = self.plan_grid_path(
                (float(x), float(z)),
                goal,
                map_id=actual_map,
            )
            if not result.ok:
                self._log_grid_plan_failure(result)
                return False

            distance = math.hypot(result.waypoints[-1][0] - x, result.waypoints[-1][1] - z)
            self.log_info(
                f"规划完成：当前位置=({x:.2f}, {z:.2f})，"
                f"目标=({result.waypoints[-1][0]:.2f}, {result.waypoints[-1][1]:.2f})，"
                f"直线距离={distance:.2f}m",
                notify=True,
            )
            return True

        self.log_warning(f"等待定位超过 {timeout:.1f}s，无法规划", notify=True)
        return False

    def pause(self):
        """暂停时立即松开 ``W``，避免角色继续移动。"""
        self._set_grid_walking(False)
        return super().pause()

    def on_destroy(self):
        try:
            self._set_grid_walking(False)
        finally:
            parent = getattr(super(), "on_destroy", None)
            if callable(parent):
                parent()
