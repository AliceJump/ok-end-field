from collections import deque

import cv2
import numpy as np
from qfluentwidgets import FluentIcon
from ok import Box

from src.core.BaseEfTask import BaseEfTask
from src.core.BattleConfig import RECOMMEND_SKILL_REGIONS
from src.image.circular_pulse_detector import (
    DetectionResult,
    detect_circular_pulse,
    get_circular_pulse_detector,
)

# ---------------------------------------------------------------------------
# 四批预制参数：与战斗「自动释放推荐技能」共用 BattleConfig.RECOMMEND_SKILL_REGIONS。
#
#   x / y               源区域中心归一化坐标（pixel_x / 屏宽, pixel_y / 屏高）
#   button_radius       源搜索圆半径（pixel / 短边），允许偏大，只要包住真实圆心
#   effect_max_radius   光圈最大扩散半径（相对真实圆心，pixel / 短边）
#
# 任一数值为 0 的批次视为未配置，运行时自动跳过。
# ---------------------------------------------------------------------------
_PRESETS = [dict(p) for p in RECOMMEND_SKILL_REGIONS]


class TestCircularPulseDetect(BaseEfTask):
    """循环扩散光圈检测实测任务：死循环扫描四批源区域，命中即画框并按对应按键。

    批次 1/2/3/4 分别映射按键 1/2/3/4；每个光圈周期只按一次。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "光圈检测实测"
        self.group_name = "工具与调试"
        self.description = (
            "对四批预设源区域持续运行圆形 UI 循环扩散光圈检测；"
            "命中时在匹配位置画框（绿=当前峰值环，红=已校准按钮），"
            "并按对应数字键（批次1→1，依次类推），每个周期按一次。"
            "逐轮打印各批次 r_t/thr/actual_r/support 数据行与环带活跃度诊断；"
            "参数需在 BattleConfig.RECOMMEND_SKILL_REGIONS 中配置。"
        )
        self.icon = FluentIcon.SEARCH
        self.visible = self.debug
        self.default_config.update({
            "扫描间隔(秒)": 0.1,
            "诊断间隔(轮)": 5,
        })
        self.config_description.update({
            "扫描间隔(秒)": "每轮四批检测后的等待时间；越小采样越密。",
            "诊断间隔(轮)": "每隔多少轮输出一行按钮数据与环带诊断；设为 1 逐轮打印。",
        })

    def run(self):
        interval = max(0.0, float(self.config.get("扫描间隔(秒)", 0.1) or 0.1))
        presets = [
            dict(p, index=i + 1)
            for i, p in enumerate(_PRESETS)
            if self._is_configured(p)
        ]
        if not presets:
            raise ValueError("请先在 BattleConfig.RECOMMEND_SKILL_REGIONS 中配置至少一批预设区域参数")

        self.log_info(
            f"开始光圈检测实测: {len(presets)} 批 "
            f"({', '.join(str(p['label']) for p in presets)})",
            notify=True,
        )

        scan_count = 0
        last_states = {}
        last_hit_count = None
        last_cycles = {}  # 每批次上次按键时的 cycle_id，保证每个周期只按一次
        diag_store = {}   # 每批次滚动灰度窗口（环带差分用）
        last_diag_r = {}  # 每批次上次数据行的 r_t，供增量 Δr_t 显示
        results_map = {}  # 每批次最近一次检测结果，供诊断用
        while True:
            scan_count += 1
            frame = self.next_frame()
            height, width = frame.shape[:2]
            min_wh = min(width, height)

            button_boxes = []
            ring_boxes = []
            hit_count = 0
            for preset in presets:
                result = detect_circular_pulse(
                    frame,
                    float(preset["x"]),
                    float(preset["y"]),
                    float(preset["button_radius"]),
                    float(preset["effect_max_radius"]),
                )
                results_map[preset["label"]] = result
                self._sample_diag(preset, frame, result, diag_store)
                if result.actual_x is None or result.actual_radius is None:
                    continue
                index = preset["index"]
                center_x = result.actual_x * width
                center_y = result.actual_y * height
                button_r = result.actual_radius * min_wh
                # 红框：已校准的真实按钮位置（缓存锚点）。
                button_boxes.append(self._make_box(
                    f"pulse_b{index}_btn", center_x, center_y, button_r, result.confidence))
                # 绿框：当前峰值环位置（仅检出时光圈存在）。
                if result.detected and result.peak_radius is not None:
                    hit_count += 1
                    ring_r = result.peak_radius * min_wh
                    ring_boxes.append(self._make_box(
                        f"pulse_b{index}_ring_{result.state.value}", center_x, center_y, ring_r,
                        result.confidence))
                    # 新光圈周期 → 按对应数字键（批次1→1，依次类推）。
                    label = str(preset["label"])
                    if last_cycles.get(label) != result.cycle_id:
                        last_cycles[label] = result.cycle_id
                        key = str(index)
                        self.press_key(key)
                        self.log_info(
                            f"[{scan_count}] {label} 第{result.cycle_id}周期命中, 按下按键 {key}"
                        )
                self._log_state_change(scan_count, preset, result, last_states,
                                       width, height)

            self.draw_boxes("pulse_buttons", button_boxes, color="red", debug=True)
            self.draw_boxes("pulse_rings", ring_boxes, color="green", debug=True)

            if hit_count != last_hit_count:
                self.log_info(f"[{scan_count}] 命中批次数: {hit_count}/{len(presets)}")
                last_hit_count = hit_count
            stats = get_circular_pulse_detector().stats
            if scan_count % 20 == 0:
                summary = ", ".join(
                    f"{p['label']}={last_states.get(p['label'], 'idle')}" for p in presets
                )
                self.log_info(
                    f"[{scan_count}] 心跳: {summary} "
                    f"(cache hit={stats.hits} miss={stats.misses} 重校准={stats.recalibrations})"
                )
            diag_every = max(1, int(self.config.get("诊断间隔(轮)", 5) or 5))
            if scan_count % diag_every == 0:
                for preset in presets:
                    label = str(preset["label"])
                    result = results_map.get(preset["label"])
                    line = self._report_detail(
                        scan_count, label, result, width, height, last_diag_r)
                    if line:
                        self.log_info(line)
                    line = self._report_annulus(
                        scan_count, label, preset, result, diag_store, min_wh)
                    if line:
                        self.log_info(line)
            self.sleep(interval)

    @staticmethod
    def _is_configured(preset: dict) -> bool:
        return all(
            0 < float(preset[key]) <= 1
            for key in ("x", "y", "button_radius", "effect_max_radius")
        ) and 0 <= float(preset["x"]) <= 1 and 0 <= float(preset["y"]) <= 1

    @staticmethod
    def _make_box(name: str, center_x: float, center_y: float, radius: float,
                  confidence: float) -> Box:
        box = Box(center_x - radius, center_y - radius, radius * 2, radius * 2)
        box.name = name
        box.confidence = confidence
        return box

    def _log_state_change(self, scan_count: int, preset: dict, result: DetectionResult,
                          last_states: dict, width: int, height: int) -> None:
        label = str(preset["label"])
        state = result.state.value
        if last_states.get(label) == state:
            return
        detail = self._measure_text(result, width, height)
        self.log_info(
            f"[{scan_count}] {label}: {last_states.get(label, '无')} -> {state} "
            f"(cycle={result.cycle_id}, conf={result.confidence:.2f}, {detail})"
        )
        last_states[label] = state

    def _measure_text(self, result: DetectionResult, width: int, height: int) -> str:
        """把当帧完整测量（圆心/基线半径/表观半径/阈值/计数）压成一行文本。"""
        if result.actual_x is None or result.actual_y is None or result.actual_radius is None:
            return "未校准"
        min_wh = min(width, height)
        cx = result.actual_x * width
        cy = result.actual_y * height
        parts = [
            f"c=({cx:.0f},{cy:.0f})",
            f"base={result.actual_radius * min_wh:.1f}px",
        ]
        if result.peak_radius is not None and result.pulse_threshold is not None:
            parts.append(f"r_t={result.peak_radius * min_wh:.1f}px")
            parts.append(f"thr={result.pulse_threshold * min_wh:.1f}px")
            parts.append(f"sup={result.angular_consistency:.2f}")
            parts.append(f"ab={result.above}")
            parts.append(f"be={result.below}")
            parts.append(f"arm={int(result.armed)}")
        else:
            parts.append("r_t=测不到")
        return " ".join(parts)

    _N_DIAG_SECTORS = 16
    _DIAG_WINDOW = 8  # 环带差分滚动窗口帧数

    def _sample_diag(self, preset: dict, frame: np.ndarray,
                     result: DetectionResult | None, diag_store: dict) -> None:
        """每轮采样：把按钮周边灰度裁剪推入滚动窗口，供环带帧间差分使用。"""
        if result is None or result.actual_x is None or result.actual_radius is None:
            return
        height, width = frame.shape[:2]
        min_wh = min(width, height)
        center_x = result.actual_x * width
        center_y = result.actual_y * height
        effect_r = float(preset["effect_max_radius"]) * min_wh
        half = int(effect_r * 1.15) + 4
        x0, y0 = max(0, int(center_x) - half), max(0, int(center_y) - half)
        x1, y1 = min(width, int(center_x) + half), min(height, int(center_y) + half)
        if x1 - x0 < 8 or y1 - y0 < 8:
            return
        gray = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
        diag_store.setdefault(str(preset["label"]), deque(maxlen=self._DIAG_WINDOW)).append(
            (gray, int(center_x) - x0, int(center_y) - y0))

    def _report_detail(self, scan_count: int, label: str,
                       result: DetectionResult | None, width: int, height: int,
                       last_diag_r: dict) -> str | None:
        """逐轮打印表观半径相对阈值的完整测量，定位「测量偏大」或「阈值过低」。"""
        if result is None or result.actual_radius is None:
            return f"[{scan_count}] 数据 {label}: 未校准（无真实圆锚点）"
        text = self._measure_text(result, width, height)
        min_wh = min(width, height)
        if result.peak_radius is None or result.pulse_threshold is None:
            return f"[{scan_count}] 数据 {label}: {result.state.value} {text}"
        r_t = result.peak_radius * min_wh
        prev = last_diag_r.get(label)
        last_diag_r[label] = r_t
        delta = "" if prev is None else f" Δr_t={r_t - prev:+.1f}"
        return (
            f"[{scan_count}] 数据 {label}: {result.state.value} {text}{delta} "
            f"conf={result.confidence:.2f}"
        )

    def _report_annulus(self, scan_count: int, label: str, preset: dict,
                        result: DetectionResult | None, diag_store: dict,
                        min_wh: float) -> str | None:
        """取滚动窗口内最大帧间差分再统计环带活跃度。

        旧版每 50 轮才取两帧相隔数秒的差分，周期性光圈动画两帧相位相近时
        差分趋近 0，导致所有批次（含真实动画）都误报「无变化」；改为逐轮
        采样、窗口内取最大值后不再受采样混叠影响。
        """
        buf = diag_store.get(label)
        if not buf or len(buf) < 2 or result is None or result.actual_radius is None:
            return None
        cur, cx, cy = buf[-1]
        best = None
        for prev, _, _ in list(buf)[:-1]:
            if prev.shape != cur.shape:
                continue
            diff = cv2.absdiff(cur, prev)
            best = diff if best is None else np.maximum(best, diff)
        if best is None:
            return None
        actual_px = result.actual_radius * min_wh
        effect_r = float(preset["effect_max_radius"]) * min_wh
        mask = np.zeros(best.shape, np.uint8)
        cv2.circle(mask, (cx, cy), int(effect_r * 1.05), 255, -1)
        cv2.circle(mask, (cx, cy), max(0, int(actual_px * 0.85)), 0, -1)
        annulus = best[mask > 0]
        active = int(np.count_nonzero(annulus >= 5))
        total = int(annulus.size)
        max_diff = int(best.max())
        coverage = 0
        if active:
            ys, xs = np.mgrid[0:best.shape[0], 0:best.shape[1]]
            sectors = ((np.arctan2(ys - cy, xs - cx) + np.pi)
                       / (2 * np.pi) * self._N_DIAG_SECTORS).astype(np.int32)
            hit = sectors[(mask > 0) & (best >= 5)] % self._N_DIAG_SECTORS
            coverage = len(np.unique(hit))
        if active == 0:
            hint = "窗口内环带无变化（该按钮当前无动画）"
        elif coverage <= self._N_DIAG_SECTORS // 4:
            hint = "仅局部变化（粒子/背景/光标），非整圈光圈"
        else:
            hint = "存在大范围环状变化"
        return (
            f"[{scan_count}] 诊断 {label}: 窗口{len(buf)}帧 环带活跃 {active}/{total}px "
            f"max_diff={max_diff} 扇区覆盖 {coverage}/{self._N_DIAG_SECTORS} → {hint}"
        )
