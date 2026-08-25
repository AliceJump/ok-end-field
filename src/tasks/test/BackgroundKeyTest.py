# -*- coding: utf-8 -*-
"""后台模式按键测试任务。

验证后台模式下按键（按下时前置游戏、松开时恢复窗口）能否真正送达游戏。
用于调试：置顶是否成功、等待时间是否足够、按键是否生效、窗口是否恢复。

本任务默认强制后台模式（后台模式启用=True），以便直接测试后台按键路径。
"""

import time

import win32gui
from qfluentwidgets import FluentIcon

from src.core.BaseEfTask import BaseEfTask
from src.data.FeatureList import FeatureList as fL


class BackgroundKeyTest(BaseEfTask):
    """后台模式按键测试（工具与调试分组）。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "后台按键测试"
        self.group_name = "工具与调试"
        self.group_icon = FluentIcon.DEVELOPER_TOOLS
        self.description = "验证后台模式下按键能否送达游戏（置顶+等待+按键+恢复），输出诊断日志"
        self.visible = self.debug

        self.default_config = {
            "后台模式启用": True,  # 强制后台模式，直接测试后台按键路径
            "测试按键": "m",
            "测试次数": 3,
            "按键后等待(秒)": 1.0,
            "仅测试置顶": False,
        }
        self.config_description = {
            "测试按键": self.tr("要测试的按键（仅支持 m 打开地图，结果可观察）"),
            "测试次数": self.tr("重复测试次数"),
            "按键后等待(秒)": self.tr("按键后等待结果出现的时间"),
            "仅测试置顶": self.tr("勾选后只测试窗口置顶是否成功（不按键），用于隔离置顶问题"),
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
        key = str(self.config.get("测试按键", "m"))
        if key != "m":
            self.log_info("测试按键仅支持 m（打开地图），已强制使用 m")
            key = "m"
        repeat = max(1, int(self.config.get("测试次数", 3)))
        wait_after = float(self.config.get("按键后等待(秒)", 1.0))
        only_activate = bool(self.config.get("仅测试置顶", False))

        self.log_info(f"当前输入模式: {self.input_mode()}")
        self.ensure_main()
        self._close_map()

        game_hwnd = self.get_game_hwnd()
        success = 0
        for i in range(repeat):
            self.log_info(f"[{i + 1}/{repeat}] {'仅置顶测试' if only_activate else f'测试按键 {key!r}'}")
            fg_before = win32gui.GetForegroundWindow()
            self.log_info(
                f"  按键前前台: {fg_before} | 游戏={game_hwnd} | 是否游戏={fg_before == game_hwnd}"
            )

            if only_activate:
                # 仅测试置顶：激活窗口并检查是否真正成为前台
                self.active_and_send_mouse_delta(activate=True, only_activate=True)
                time.sleep(0.5)
                fg_after = win32gui.GetForegroundWindow()
                became = fg_after == game_hwnd
                self.log_info(f"  置顶后前台: {fg_after} | 是否置顶成功={became}")
                if became:
                    success += 1
                # 恢复原窗口
                if fg_before and win32gui.IsWindow(fg_before) and fg_after != fg_before:
                    try:
                        win32gui.SetForegroundWindow(fg_before)
                    except Exception:
                        pass
            else:
                # 完整后台按键流程：前置→等待→按键→恢复
                self.press_key(key)
                fg_after = win32gui.GetForegroundWindow()
                restored = fg_after == fg_before
                self.log_info(f"  按键后前台: {fg_after} | 是否恢复原窗口={restored}")

                self.sleep(wait_after)
                if self._in_map():
                    success += 1
                    self.log_info(f"  [{i + 1}/{repeat}] 地图已打开 ✓")
                else:
                    self.log_error(f"  [{i + 1}/{repeat}] 地图未打开 ✗")
                self._close_map()

        self.log_info(f"测试完成：{success}/{repeat} 次成功")
        if only_activate:
            if success == repeat:
                self.log_info("结论：窗口置顶可正常成功", notify=True)
            else:
                self.log_error("结论：窗口置顶未能稳定成功", notify=True)
        elif success == repeat:
            self.log_info("结论：后台模式按键可正常送达游戏", notify=True)
        else:
            self.log_error("结论：后台模式按键未能稳定送达游戏", notify=True)