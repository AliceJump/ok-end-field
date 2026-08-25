# -*- coding: utf-8 -*-
"""Alt+点击小地图中心打开地图的测试任务。

验证 alt 键不前置游戏窗口时，alt+点击小地图中心能否替代 send_key("m")
打开地图界面（后台输入可行性验证）。

小地图中心坐标取自 ItemNavigatorTask._arrow_center_rel：
    (162 / 1920, 166 / 1080) ≈ (0.0844, 0.1537)
"""

from qfluentwidgets import FluentIcon

from src.core.BaseEfTask import BaseEfTask
from src.data.FeatureList import FeatureList as fL

# 小地图中心相对坐标（与物品导航箭头中心一致）
MINIMAP_CENTER_X = 162 / 1920
MINIMAP_CENTER_Y = 166 / 1080


class AltClickMinimapTest(BaseEfTask):
    """Alt+点击小地图中心打开地图测试（工具与调试分组）。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "Alt点击小地图测试"
        self.group_name = "工具与调试"
        self.group_icon = FluentIcon.DEVELOPER_TOOLS
        self.description = "验证 alt+点击小地图中心能否替代 send_key('m') 打开地图（后台输入可行性）"
        self.visible = self.debug

        self.default_config = {
            "测试次数": 3,
            "测试前前置游戏": False,
        }
        self.config_description = {
            "测试次数": "重复测试次数，每次先关闭地图再重新打开",
            "测试前前置游戏": "勾选后每次测试前先把游戏窗口置前（对比前台/后台行为）",
        }

    def _in_map(self) -> bool:
        if self.find_one(fL.in_map, box=self.box_of_screen(0.027, 0.531, 0.051, 0.896)):
            return True
        return any(self.find_one(feature) for feature in (fL.transaction_icon, fL.main_centre_icon))

    def _close_map(self) -> None:
        if self._in_map():
            self.press_key("m")
            self.sleep(1)

    def run(self):
        repeat = max(1, int(self.config.get("测试次数", 3)))
        bring_foreground = bool(self.config.get("测试前前置游戏", False))

        self.ensure_main()
        self._close_map()

        success = 0
        for i in range(repeat):
            if bring_foreground:
                self.active_and_send_mouse_delta(activate=True, only_activate=True)
            self.log_info(
                f"[{i + 1}/{repeat}] alt+点击小地图中心 ({MINIMAP_CENTER_X:.4f}, {MINIMAP_CENTER_Y:.4f})"
            )
            self.click_with_alt(MINIMAP_CENTER_X, MINIMAP_CENTER_Y, after_sleep=1.0)
            if self.wait_until(self._in_map, time_out=5, raise_if_not_found=False):
                success += 1
                self.log_info(f"[{i + 1}/{repeat}] 地图已打开 ✓")
            else:
                self.log_error(f"[{i + 1}/{repeat}] 地图未打开 ✗")
            self._close_map()

        self.log_info(f"测试完成：{success}/{repeat} 次成功")
        if success == repeat:
            self.log_info("结论：alt+点击小地图中心可替代 send_key('m')", notify=True)
        else:
            self.log_error("结论：alt+点击小地图中心未能稳定打开地图", notify=True)