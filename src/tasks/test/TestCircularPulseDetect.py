from collections import deque

import cv2
import numpy as np
from qfluentwidgets import FluentIcon
from ok import Box

from src.core.BaseEfTask import BaseEfTask
from src.core.BattleConfig import RECOMMEND_SKILL_REGIONS
from src.image.recommend_skill_detector import get_recommend_skill_detector

# ---------------------------------------------------------------------------
# 四批预制参数：与战斗「自动释放推荐技能」共用 BattleConfig.RECOMMEND_SKILL_REGIONS。
#
#   x / y               按钮中心归一化坐标（pixel_x / 屏宽, pixel_y / 屏高）
#   button_radius       按钮参考半径（pixel / 短边），推荐技能检测器的采样带基准
#   effect_max_radius   光圈最大扩散半径（pixel / 短边），仅诊断环带采样范围参考
#
# 任一数值为 0 的批次视为未配置，运行时自动跳过。
# ---------------------------------------------------------------------------
_PRESETS = [dict(p) for p in RECOMMEND_SKILL_REGIONS]


class TestCircularPulseDetect(BaseEfTask):
    """推荐技能白色圆周脉冲检测实测任务：死循环扫描四批源区域，上升沿命中即画框并按对应按键。

    基于项目实际使用的 RecommendSkillDetector（白色圆周脉冲检测）：detect() 只在
    新脉冲周期的上升沿返回 True，每个白圈周期只按一次；white_ratio() 查询当帧
    信号层白色占比供诊断。批次 1/2/3/4 分别映射按键 1/2/3/4。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "光圈检测实测"
        self.group_name = "工具与调试"
        self.description = (
            "对四批预设源区域持续运行推荐技能白色圆周脉冲检测（RecommendSkillDetector）；"
            "上升沿命中时在按钮位置画框（绿=当前命中，红=按钮锚点），"
            "并按对应数字键（批次1→1，依次类推），每个周期按一次。"
            "逐轮打印各批次 white_ratio 数据行与环带活跃度诊断；"
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
        interval_value = self.config.get("扫描间隔(秒)", 0.1)
        interval = max(
            0.0,
            float(0.1 if interval_value is None or interval_value == "" else interval_value),
        )
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

        detector = get_recommend_skill_detector()
        detector.reset()

        scan_count = 0
        last_hit_count = None
        diag_store = {}   # 每批次滚动灰度窗口（环带差分用）
        while True:
            scan_count += 1
            frame = self.next_frame()
            height, width = frame.shape[:2]
            min_wh = min(width, height)

            button_boxes = []
            ring_boxes = []
            hit_count = 0
            for preset in presets:
                index = preset["index"]
                label = str(preset["label"])
                cx_n, cy_n, r_n = (
                    float(preset["x"]),
                    float(preset["y"]),
                    float(preset["button_radius"]),
                )
                rising = detector.detect(frame, cx_n, cy_n, r_n, label)
                self._sample_diag(preset, frame, diag_store)
                center_x = cx_n * width
                center_y = cy_n * height
                button_r = r_n * min_wh
                # 红框：按钮锚点位置（检测器在固定源坐标上采样，无校准圆心）。
                button_boxes.append(self._make_box(
                    f"pulse_b{index}_btn", center_x, center_y, button_r, 0.0))
                # 绿框：当前白色圆周脉冲命中（仅上升沿）。
                if rising:
                    hit_count += 1
                    ring_boxes.append(self._make_box(
                        f"pulse_b{index}_ring", center_x, center_y, button_r * 1.2, 1.0))
                    # 新白圈周期 → 按对应数字键（批次1→1，依次类推）。
                    key = str(index)
                    self.press_key(key)
                    self.log_info(
                        f"[{scan_count}] {label} 白色圆周脉冲上升沿命中, 按下按键 {key}"
                    )

            self.draw_boxes("pulse_buttons", button_boxes, color="red", debug=True)
            self.draw_boxes("pulse_rings", ring_boxes, color="green", debug=True)

            if hit_count != last_hit_count:
                self.log_info(f"[{scan_count}] 命中批次数: {hit_count}/{len(presets)}")
                last_hit_count = hit_count
            diag_every = max(1, int(self.config.get("诊断间隔(轮)", 5) or 5))
            if scan_count % diag_every == 0:
                for preset in presets:
                    label = str(preset["label"])
                    line = self._report_ratio(
                        scan_count, label, frame, preset)
                    if line:
                        self.log_info(line)
                    line = self._report_annulus(
                        scan_count, label, preset, diag_store, min_wh)
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

    @staticmethod
    def _report_ratio(scan_count: int, label: str, frame: np.ndarray,
                      preset: dict) -> str | None:
        """逐轮打印当帧信号层白色角度占比，定位「常态未白」或「常驻白」问题。"""
        detector = get_recommend_skill_detector()
        ratio = detector.white_ratio(
            frame,
            float(preset["x"]),
            float(preset["y"]),
            float(preset["button_radius"]),
        )
        return f"[{scan_count}] 数据 {label}: white_ratio={ratio:.2f} (确认阈值约 0.80)"

    _N_DIAG_SECTORS = 16
    _DIAG_WINDOW = 8  # 环带差分滚动窗口帧数

    @staticmethod
    def _sample_diag(preset: dict, frame: np.ndarray, diag_store: dict) -> None:
        """每轮采样：把按钮周边灰度裁剪推入滚动窗口，供环带帧间差分使用。"""
        height, width = frame.shape[:2]
        min_wh = min(width, height)
        center_x = float(preset["x"]) * width
        center_y = float(preset["y"]) * height
        effect_r = float(preset["effect_max_radius"]) * min_wh
        half = int(effect_r * 1.15) + 4
        x0, y0 = max(0, int(center_x) - half), max(0, int(center_y) - half)
        x1, y1 = min(width, int(center_x) + half), min(height, int(center_y) + half)
        if x1 - x0 < 8 or y1 - y0 < 8:
            return
        gray = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
        diag_store.setdefault(str(preset["label"]), deque(maxlen=TestCircularPulseDetect._DIAG_WINDOW)).append(
            (gray, int(center_x) - x0, int(center_y) - y0))

    def _report_annulus(self, scan_count: int, label: str, preset: dict,
                        diag_store: dict, min_wh: float) -> str | None:
        """取滚动窗口内最大帧间差分再统计环带活跃度。

        每轮采样、窗口内取最大值后不受采样混叠影响（见历史注释：旧版每 50 轮
        才取两帧相隔数秒的差分，周期性动画两帧相位相近时差分趋近 0 而误报）。
        """
        buf = diag_store.get(label)
        if not buf or len(buf) < 2:
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
        button_px = float(preset["button_radius"]) * min_wh
        effect_r = float(preset["effect_max_radius"]) * min_wh
        mask = np.zeros(best.shape, np.uint8)
        cv2.circle(mask, (cx, cy), int(effect_r * 1.05), 255, -1)
        cv2.circle(mask, (cx, cy), max(0, int(button_px * 0.85)), 0, -1)
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
