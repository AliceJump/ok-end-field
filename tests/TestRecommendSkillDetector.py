"""推荐技能白色圆周脉冲检测器测试。

合成帧模拟游戏按钮同心环结构（从内向外）：
    按钮内部（含白色图标与亮色扇形段）→ 深色细描边
    → 信号层：常态灰蓝细环 / 脉冲时带辉光的纯白厚环
    → 最外层淡灰细环。

覆盖：常态不触发、白色脉冲上升沿、持续白只计一次、
回落重新武装、彩色特效不误触、局部白色弧形特效（不满半圈）不触发、
多按钮状态互不干扰。
"""
import unittest

import cv2
import numpy as np

from src.image.recommend_skill_detector import RecommendSkillDetector

W, H = 1280, 720

# 与游戏一致的参考色（BGR）
COLOR_BG = (38, 32, 28)           # 深色场景
COLOR_FACE = (45, 40, 36)         # 按钮内部
COLOR_ICON = (255, 255, 255)      # 面内白色图标
COLOR_SEGMENT = (208, 206, 0)     # 面内亮色扇形段（青/绿）
COLOR_BORDER = (33, 26, 23)       # 最内层深色描边 hsl(220,16%,11%)
COLOR_RIM_REST = (118, 112, 111)  # 常态灰蓝 hsl(222,4%,45%) ≈ RGB(111,112,118)
COLOR_WHITE = (255, 255, 255)     # 脉冲白 hsl(0,0%,100%)
COLOR_COLORED_VFX = (60, 40, 220) # 高饱和彩色特效（红）


def render(cx_n=0.920, cy_n=0.898, r_n=0.037,
           signal_color=COLOR_RIM_REST, white_arc_ratio=0.0):
    """在 (cx_n, cy_n) 处渲染按钮。

    signal_color：中间信号层颜色（常态灰蓝或脉冲白或特效色）。
    white_arc_ratio：>0 时在信号层半径上叠加一段白色弧（占比），
    模拟局部飞过的白色特效；叠加时信号层本身画常态灰蓝。
    """
    img = np.full((H, W, 3), COLOR_BG, np.uint8)
    cx, cy = int(cx_n * W), int(cy_n * H)
    r0 = int(r_n * min(W, H))
    cv2.circle(img, (cx, cy), int(r0 * 0.72), COLOR_FACE, -1)
    # 面内白色图标 + 亮色扇形段（验证采样带不被面内白色干扰）
    cv2.circle(img, (cx, cy - int(r0 * 0.15)), int(r0 * 0.22), COLOR_ICON, -1)
    cv2.ellipse(img, (cx, cy), (int(r0 * 0.55), int(r0 * 0.55)),
                0, 30, 150, COLOR_SEGMENT, -1)
    # 最内层深色细描边
    cv2.circle(img, (cx, cy), int(r0 * 0.88), COLOR_BORDER, 2)
    # 中间信号层
    cv2.circle(img, (cx, cy), r0, signal_color, 3)
    # 最外层淡灰细环
    cv2.circle(img, (cx, cy), int(r0 * 1.16), COLOR_RIM_REST, 2)
    # 局部白色弧形特效
    if white_arc_ratio > 0:
        end_deg = 360.0 * white_arc_ratio
        cv2.ellipse(img, (cx, cy), (r0, r0), 0, 0, end_deg, COLOR_WHITE, 3)
    return img


class TestRecommendSkillDetector(unittest.TestCase):

    def setUp(self):
        self.det = RecommendSkillDetector()

    def feed(self, frames, label="批次3"):
        return [self.det.detect(f, 0.920, 0.898, 0.037, label) for f in frames]

    def test_rest_state_never_triggers(self):
        results = self.feed([render()] * 10)
        self.assertFalse(any(results))

    def test_white_pulse_confirms_rising_edge(self):
        results = self.feed([render(signal_color=COLOR_WHITE)] * 5)
        self.assertEqual(results.count(True), 1, "应只在上升沿确认一次")
        self.assertTrue(results[0])

    def test_sustained_white_fires_once_then_rearm_after_off(self):
        pulse = render(signal_color=COLOR_WHITE)
        rest = render()
        seq = [pulse] * 6 + [rest] * 4 + [pulse] * 3
        results = self.feed(seq)
        self.assertEqual(results.count(True), 2, "两个脉冲周期各确认一次")
        self.assertEqual(results.index(True), 0)

    def test_colored_vfx_ignored(self):
        results = self.feed([render(signal_color=COLOR_COLORED_VFX)] * 8)
        self.assertFalse(any(results))

    def test_partial_white_arc_vfx_rejected(self):
        # 局部白色弧（30% 圆周）盖不满半圈 → 拒绝
        results = self.feed([render(white_arc_ratio=0.30)] * 8)
        self.assertFalse(any(results))

    def test_labels_are_independent(self):
        pulse_a = render(cx_n=0.920, signal_color=COLOR_WHITE)
        rest_a = render(cx_n=0.920)
        pulse_b = render(cx_n=0.870, signal_color=COLOR_WHITE)
        rest_b = render(cx_n=0.870)
        a = self.det.detect(pulse_a, 0.920, 0.898, 0.037, "批次3")
        b = self.det.detect(rest_b, 0.870, 0.898, 0.037, "批次2")
        c = self.det.detect(pulse_b, 0.870, 0.898, 0.037, "批次2")
        d = self.det.detect(rest_a, 0.920, 0.898, 0.037, "批次3")
        self.assertTrue(a)
        self.assertFalse(b)
        self.assertTrue(c)
        self.assertFalse(d)

    def test_reset_label_allows_immediate_rearm_after_flash(self):
        """全屏闪光过滤复位后，紧接出现的单按钮白圈应重新产生上升沿。"""
        pulse = render(signal_color=COLOR_WHITE)
        # 全屏闪光：该标签已被确认（active=True），detect 不再返回 True。
        self.assertTrue(self.det.detect(pulse, 0.920, 0.898, 0.037, "批次3"))
        self.assertFalse(self.det.detect(pulse, 0.920, 0.898, 0.037, "批次3"))
        # 闪光过滤后复位该标签 → 白圈立即重现仍能再次触发。
        self.det.reset_label("批次3")
        self.assertTrue(self.det.detect(pulse, 0.920, 0.898, 0.037, "批次3"))
        # 复位后重复白帧只计一次（不重复触发）。
        self.assertFalse(self.det.detect(pulse, 0.920, 0.898, 0.037, "批次3"))

    def test_reset_label_does_not_affect_other_labels(self):
        pulse = render(signal_color=COLOR_WHITE)
        self.assertTrue(self.det.detect(pulse, 0.920, 0.898, 0.037, "批次3"))
        self.det.reset_label("批次2")
        # 未复位的标签仍保持 active，白圈不再触发；复位无关标签不影响它。
        self.assertFalse(self.det.detect(pulse, 0.920, 0.898, 0.037, "批次3"))

    def test_is_pulsing_reflects_current_white_state(self):
        rest = render()
        pulse = render(signal_color=COLOR_WHITE)
        self.assertFalse(self.det.is_pulsing(rest, 0.920, 0.898, 0.037))
        self.assertTrue(self.det.is_pulsing(pulse, 0.920, 0.898, 0.037))
        # 不改变任何状态：查询后再检测仍按原状态机工作。
        self.assertTrue(self.det.detect(pulse, 0.920, 0.898, 0.037, "批次3"))
        self.assertFalse(self.det.detect(pulse, 0.920, 0.898, 0.037, "批次3"))

    def test_flash_after_some_labels_already_active(self):
        """部分标签已 active 时发生全屏闪光：复位全部激活标签后仍可重新触发。"""
        pulse = render(signal_color=COLOR_WHITE)
        # 闪光前 批次3 已是真实脉冲（active，不再产生上升沿）。
        self.assertTrue(self.det.detect(pulse, 0.920, 0.898, 0.037, "批次3"))
        # 全屏闪光：所有区域当前均为白色；按当帧白色状态复位全部激活区域标签
        # （含闪光前已 active 的批次3，而不只是本帧新确认的批次2）。
        self.assertTrue(self.det.is_pulsing(pulse, 0.920, 0.898, 0.037))
        self.det.reset_label("批次3")
        self.det.reset_label("批次2")
        self.det.reset_label("批次1")
        self.det.reset_label("批次4")
        # 闪光后单按钮白圈重现 → 重新产生上升沿。
        self.assertTrue(self.det.detect(pulse, 0.920, 0.898, 0.037, "批次3"))

    def test_reset_clears_active_across_battles(self):
        """上一场结束时标签 active：新战斗入口复位后，首帧白圈应重新触发。"""
        pulse = render(signal_color=COLOR_WHITE)
        # 上一场结束：白圈期间退出战斗，标签保持 active（战斗外无 detect 调用）。
        self.assertTrue(self.det.detect(pulse, 0.920, 0.898, 0.037, "批次3"))
        # 新战斗确认：战斗边界整体复位。
        self.det.reset()
        # 新战斗首帧白圈 → 重新产生上升沿。
        self.assertTrue(self.det.detect(pulse, 0.920, 0.898, 0.037, "批次3"))


if __name__ == "__main__":
    unittest.main()
