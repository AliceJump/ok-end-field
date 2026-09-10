# -*- coding: utf-8 -*-
"""小地图区域检查：截一帧游戏画面，圈出里程计所用的小地图圆环区域并保存图片。

用途：核对「小地图里程计」假设的圆心与内/外半径是否真的对准了游戏小地图。
区域不对（圆心偏了、半径过大过小）会直接导致相位相关失效、位移恒为 0，
而那种故障在日志里只表现为"位移=(0.0,0.0)px"，很难反推是区域圈错了。

几何参数取自 :func:`minimap_odometry.region_geometry`——与里程计建掩膜用的是
同一个函数，所以本任务圈出来的区域**就是**实际参与相位相关的像素范围。

输出两张图到保存目录：
  - <时间>_region_full.png：整帧 + 圆环标注（外圈绿、内圈红、圆心十字、环带染色）
  - <时间>_region_zoom.png：以圆心为中心裁出约 2 倍外径并放大，便于细看是否对准

日志同时打印几何参数与环带灰度标准差：标准差过低说明该区域基本是纯色，
多半没圈在小地图上（正常小地图纹理丰富，标准差通常远大于 5）。

注：图上文字只用 ASCII，避免 OpenCV 的 Hershey 字体画不出中文。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from qfluentwidgets import FluentIcon

from src.core.BaseEfTask import BaseEfTask
from src.tasks.mixin.minimap_odometry import (
    DEFAULT_CENTER_RATIO,
    DEFAULT_R_INNER_RATIO,
    DEFAULT_R_OUTER_RATIO,
    annulus_mask,
    region_geometry,
)

# 环带灰度标准差低于该值，认为该区域基本是纯色（多半没圈对）
_BAND_STD_SUSPICIOUS = 5.0


class MinimapRegionCheck(BaseEfTask):
    """截图一次并标注小地图圆环区域，用于核对圆心/半径是否准确。"""

    requires_foreground = True  # 需要读取游戏画面/小地图

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "小地图区域检查"
        self.group_name = "工具与调试"
        self.group_icon = FluentIcon.DEVELOPER_TOOLS
        self.description = "截一帧游戏画面，圈出里程计用的小地图圆环区域并保存，核对区域是否对准"
        self.visible = self.debug

        self.default_config = {
            "圆心x比例(占宽)": DEFAULT_CENTER_RATIO[0],
            "圆心y比例(占高)": DEFAULT_CENTER_RATIO[1],
            "外圈半径比例(占宽)": DEFAULT_R_OUTER_RATIO,
            "内圈半径比例(占宽)": DEFAULT_R_INNER_RATIO,
            "裁剪放大倍数": 5,
            "标记外扩比例": 0.5,
            "保存目录": "screenshots/minimap_region",
        }
        self.config_description = {
            "圆心x比例(占宽)": "小地图圆心 x / 画面宽（默认与里程计一致）",
            "圆心y比例(占高)": "小地图圆心 y / 画面高（默认与里程计一致）",
            "外圈半径比例(占宽)": "圆环外半径 / 画面宽；相位相关只用这条环带里的像素",
            "内圈半径比例(占宽)": "圆环内半径 / 画面宽；用于盖住中心箭头的扫掠区",
            "裁剪放大倍数": "放大图倍数（最近邻放大，便于看清单个像素是否圈对）",
            "标记外扩比例": "裁剪范围在外圈之外再留出的余量（占外半径的比例）",
            "保存目录": "图片保存路径（相对于项目根目录）",
        }

    # ------------------------------------------------------------------ #
    # 小工具
    # ------------------------------------------------------------------ #
    def _cfg_float(self, key, default):
        try:
            return float(self.config.get(key, default))
        except (TypeError, ValueError):
            return float(default)

    # ------------------------------------------------------------------ #
    # 主流程
    # ------------------------------------------------------------------ #
    def run(self):
        frame = self.next_frame()
        if frame is None:
            self.log_warning("截图失败：next_frame 返回 None（游戏窗口是否在前台？）", notify=True)
            return

        h, w = frame.shape[:2]
        cx_ratio = self._cfg_float("圆心x比例(占宽)", DEFAULT_CENTER_RATIO[0])
        cy_ratio = self._cfg_float("圆心y比例(占高)", DEFAULT_CENTER_RATIO[1])
        r_out_ratio = self._cfg_float("外圈半径比例(占宽)", DEFAULT_R_OUTER_RATIO)
        r_in_ratio = self._cfg_float("内圈半径比例(占宽)", DEFAULT_R_INNER_RATIO)
        pad_ratio = max(0.0, self._cfg_float("标记外扩比例", 0.5))
        zoom = max(1, int(self._cfg_float("裁剪放大倍数", 5)))

        # 与里程计建掩膜同一个函数 -> 圈出来的就是实际参与相位相关的区域
        cx, cy, r_in, r_out = region_geometry(
            w, h, (cx_ratio, cy_ratio), r_out_ratio, r_in_ratio
        )
        mask = annulus_mask(h, w, (cx, cy), r_in, r_out)

        # 环带灰度标准差：区域没圈对时基本是纯色，标准差会很低
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        band = mask > 0
        band_std = float(gray[band].std()) if band.any() else 0.0

        # ---- 标注 ----
        vis = frame.copy()
        overlay = vis.copy()
        overlay[band] = (0, 255, 255)  # BGR：环带染黄
        vis = cv2.addWeighted(overlay, 0.35, vis, 0.65, 0)

        c = (int(round(cx)), int(round(cy)))
        cv2.drawMarker(vis, c, (0, 0, 255), markerType=cv2.MARKER_CROSS,
                       markerSize=40, thickness=2)
        cv2.circle(vis, c, int(round(r_out)), (0, 255, 0), 2)   # 外圈绿
        cv2.circle(vis, c, int(round(r_in)), (0, 0, 255), 2)    # 内圈红

        task_w = int(getattr(self, "width", 0) or 0)
        task_h = int(getattr(self, "height", 0) or 0)
        lines = [
            f"frame={w}x{h}  task={task_w}x{task_h}",
            f"center=({cx:.1f},{cy:.1f})px ratio=({cx_ratio:.4f},{cy_ratio:.4f})",
            f"r_in={r_in:.1f}px r_out={r_out:.1f}px ratio=({r_in_ratio:.4f},{r_out_ratio:.4f})",
            f"band_gray_std={band_std:.1f}  (green=outer red=inner)",
        ]
        # 文字画在左下角：小地图默认在左上，这样文字不会压住要检查的区域
        # （放大图是从圆心裁的，若文字在上面会被一起裁进去挡住画面）。
        for i, text in enumerate(lines):
            y = h - 20 - (len(lines) - 1 - i) * 32
            cv2.putText(vis, text, (14, y), cv2.FONT_HERSHEY_SIMPLEX, 0.85,
                        (0, 0, 0), 4, cv2.LINE_AA)      # 描边，深色背景下也看得清
            cv2.putText(vis, text, (14, y), cv2.FONT_HERSHEY_SIMPLEX, 0.85,
                        (0, 255, 255), 2, cv2.LINE_AA)

        # ---- 裁剪放大（从标注图上裁，保留圆圈） ----
        pad = r_out * pad_ratio
        x0 = max(0, int(cx - r_out - pad))
        x1 = min(w, int(cx + r_out + pad))
        y0 = max(0, int(cy - r_out - pad))
        y1 = min(h, int(cy + r_out + pad))
        crop = vis[y0:y1, x0:x1]
        if crop.size:
            # 放大后过大的话自动降倍数，避免写出几十 MB 的图
            max_side = 4096
            eff_zoom = max(1, min(zoom, max_side // max(crop.shape[0], crop.shape[1]) or 1))
            zoom_img = cv2.resize(crop, None, fx=eff_zoom, fy=eff_zoom,
                                  interpolation=cv2.INTER_NEAREST)
        else:
            eff_zoom, zoom_img = 1, crop

        # ---- 保存 ----
        save_dir = Path(self.config.get("保存目录", "screenshots/minimap_region"))
        save_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        full_path = save_dir / f"{stamp}_region_full.png"
        zoom_path = save_dir / f"{stamp}_region_zoom.png"
        ok_full = cv2.imwrite(str(full_path), vis)
        ok_zoom = cv2.imwrite(str(zoom_path), zoom_img)

        self.log_info(
            f"[小地图区域] 画面 {w}x{h} 圆心=({cx:.1f},{cy:.1f})px "
            f"内径={r_in:.1f}px 外径={r_out:.1f}px | 环带灰度std={band_std:.1f}"
        )
        if task_w and task_h and (task_w, task_h) != (w, h):
            self.log_warning(
                f"任务记录的分辨率 {task_w}x{task_h} 与实际帧 {w}x{h} 不一致："
                f"里程计是按任务分辨率建掩膜再把帧缩放到该尺寸，比例虽然相同，"
                f"但若宽高比不同会导致圆心错位，请核对",
                notify=True,
            )
        if not (ok_full and ok_zoom):
            self.log_warning(f"图片写入失败（目录是否可写？）: {save_dir.resolve()}", notify=True)
            return
        self.log_info(f"整帧标注图: {full_path.resolve()}")
        self.log_info(f"放大图({eff_zoom}x): {zoom_path.resolve()}", notify=True)

        if band_std < _BAND_STD_SUSPICIOUS:
            self.log_warning(
                f"环带灰度标准差仅 {band_std:.1f}（接近纯色）：该区域大概率没圈在小地图上。"
                f"请对照放大图调整「圆心x/y比例」与「内/外半径比例」",
                notify=True,
            )
