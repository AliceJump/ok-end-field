"""圆形 UI 循环扩散光圈检测器测试。

用合成帧覆盖设计规范的缓存规则、模糊校准、光圈状态机、抗背景干扰、
分辨率无关性与丢帧/FPS 波动鲁棒性，并统计 false positive /
false negative / detection latency。
"""
import unittest

import cv2
import numpy as np

from src.image.circular_pulse_detector import (
    CalibratedCircle,
    CircularPulseDetector,
    DetectionResult,
    DetectionState,
    _Entry,
    detect_circular_pulse,
    get_circular_pulse_detector,
)

W, H = 1280, 720
CX, CY, R = 880, 400, 48          # 真实按钮圆心/半径（像素）
EFFECT_MAX = 66                    # 光圈最大包络半径（像素）；阈值 = 0.8×66 = 52.8
SRC_DX, SRC_DY, SRC_R = 14, -10, 36  # 源区域相对真实圆心的偏移与半径
CYCLE_LEN = 22                     # 一个光圈周期的帧数（含 1 帧消失）


class _Scene:
    """合成 3D 场景：平滑噪声背景 + 可选干扰物 + 不透明圆形按钮 + 光圈。"""

    def __init__(self, w=W, h=H, cx=CX, cy=CY, r=R, seed=7):
        self.w, self.h = w, h
        self.cx, self.cy, self.r = cx, cy, r
        pad = 96  # 背景画布外边距，供摄像机平移使用
        self.pad = pad
        rng = np.random.default_rng(seed)
        grid = rng.uniform(28, 72, (h // 24 + 2, w // 24 + 2)).astype(np.float32)
        canvas = cv2.resize(grid, (w + 2 * pad, h + 2 * pad), interpolation=cv2.INTER_CUBIC)
        self.canvas = np.clip(canvas, 20, 90)
        self.rng = np.random.default_rng(seed + 1)
        self.decoys = []  # 背景中的其他圆形物体 (x, y, radius)

    def render(self, cam=(0.0, 0.0), particles=0, ring=None, alpha=0.18):
        ox = int(round(self.pad + cam[0]))
        oy = int(round(self.pad + cam[1]))
        img = np.repeat(self.canvas[oy:oy + self.h, ox:ox + self.w][..., None], 3, axis=2).astype(np.float32)
        for dx, dy, dr in self.decoys:
            cv2.circle(img, (int(dx), int(dy)), int(dr), (152, 152, 152), 3, cv2.LINE_AA)
        for _ in range(particles):
            px, py = self.rng.uniform(0, self.w), self.rng.uniform(0, self.h)
            pr, val = self.rng.uniform(2, 5), self.rng.uniform(30, 60)
            cv2.circle(img, (int(px), int(py)), int(pr), (val, val, val), -1, cv2.LINE_AA)
        # 稳定、不透明、连续轮廓的圆形按钮 + 内部细节
        cv2.circle(img, (self.cx, self.cy), self.r, (104, 112, 118), -1, cv2.LINE_AA)
        cv2.circle(img, (self.cx, self.cy), self.r, (215, 220, 228), 2, cv2.LINE_AA)
        cv2.circle(img, (self.cx, self.cy), int(self.r * 0.62), (74, 80, 86), -1, cv2.LINE_AA)
        if ring is not None:
            outer = int(round(ring)) + 1
            inner = max(0, int(round(ring)) - 4)
            mask = np.zeros((self.h, self.w), np.uint8)
            cv2.circle(mask, (self.cx, self.cy), outer, 255, -1)
            cv2.circle(mask, (self.cx, self.cy), inner, 0, -1)
            blend = mask.astype(np.float32)[..., None] / 255.0 * alpha
            img += (255.0 - img) * blend
        return np.clip(img, 0, 255).astype(np.uint8)


def _source():
    """默认源参数（归一化），相对真实按钮有偏移且半径偏小。"""
    return (
        (CX + SRC_DX) / W,
        (CY + SRC_DY) / H,
        SRC_R / min(W, H),
        EFFECT_MAX / min(W, H),
    )


def _timeline(n_frames, cycle_len=CYCLE_LEN, r_button=R, r_max=EFFECT_MAX,
              jitter_rng=None, blank_frames=1):
    """循环光圈时间线：由内向外扩散 → 最大处瞬间消失 → 重现。"""
    radii = []
    last_expand = cycle_len - 1 - blank_frames
    for t in range(n_frames):
        p = t % cycle_len
        if p > last_expand:
            radii.append(None)  # 到达最大范围后瞬间消失
            continue
        frac = p / last_expand
        radius = r_button + 2 + (r_max - r_button - 2) * frac
        if jitter_rng is not None:
            radius += jitter_rng.uniform(-1.5, 1.5)
        radii.append(radius)
    return radii


def _run(detector, scene, timeline, frames=None, cam_per_frame=None,
         particles_per_frame=None, feed=None, source=None):
    """按时间线喂帧并收集 (帧号, 结果)。feed(t)->False 表示该帧丢弃。"""
    nx, ny, nbr, ner = source or _source()
    results = []
    for t in range(len(timeline) if frames is None else frames):
        if feed is not None and not feed(t):
            continue
        cam = cam_per_frame(t) if cam_per_frame else (0.0, 0.0)
        parts = particles_per_frame(t) if particles_per_frame else 0
        frame = scene.render(cam=cam, particles=parts, ring=timeline[t])
        results.append((t, detector.detect(frame, nx, ny, nbr, ner)))
    return results


def _first_detected(results):
    return next((i for i, (_, r) in enumerate(results) if r.detected), None)


class TestCacheRules(unittest.TestCase):
    """缓存命中 / 未命中 / 多缓存选择 / 边界情况。"""

    def test_miss_then_hit_and_reuse(self):
        det = CircularPulseDetector()
        scene = _Scene()
        nx, ny, nbr, ner = _source()
        det.detect(scene.render(), nx, ny, nbr, ner)
        self.assertEqual(det.stats.misses, 1)
        self.assertEqual(len(det.calibrated_circles), 1)
        # 同一区域再次调用：直接复用，禁止重新搜索圆形。
        det.detect(scene.render(), nx, ny, nbr, ner)
        self.assertEqual(det.stats.hits, 1)
        self.assertEqual(det.stats.misses, 1)

    def test_source_radius_variation_still_hits(self):
        det = CircularPulseDetector()
        scene = _Scene()
        nx, ny, _, ner = _source()
        det.detect(scene.render(), nx, ny, SRC_R / min(W, H), ner)
        # 源半径变大/变小但圆心仍被包含 → 命中。
        for radius_px in (SRC_R * 0.6, SRC_R * 1.8):
            det.detect(scene.render(), nx, ny, radius_px / min(W, H), ner)
        self.assertEqual(det.stats.hits, 2)
        self.assertEqual(det.stats.recalibrations, 0)

    def test_cached_center_on_source_boundary_is_hit(self):
        det = CircularPulseDetector()
        scene = _Scene()
        # 构造源圆心，使缓存圆心恰好落在源搜索圆圆周上（距离 == 源半径）。
        dist_px = 30.0
        entry = _Entry(CalibratedCircle(CX / W, CY / H, R / min(W, H)))
        det._entries.append(entry)
        det.detect(
            scene.render(),
            (CX + dist_px) / W,
            CY / H,
            dist_px / min(W, H),
            EFFECT_MAX / min(W, H),
        )
        self.assertEqual(det.stats.hits, 1)
        self.assertEqual(det.stats.misses, 0)

    def test_multiple_cached_centers_nearest_wins(self):
        det = CircularPulseDetector()
        scene = _Scene()

        def make(cx, cy):
            return _Entry(CalibratedCircle(cx / W, cy / H, R / min(W, H)))

        det._entries.append(make(CX + 60, CY))       # 距源中心 47px
        det._entries.append(make(CX - 20, CY + 8))   # 距源中心 38px
        det._entries.append(make(CX + 5, CY - 40))   # 距源中心 31px，最近
        nx, ny = (CX + SRC_DX) / W, (CY + SRC_DY) / H
        result = det.detect(scene.render(), nx, ny, 90 / min(W, H), EFFECT_MAX / min(W, H))
        self.assertEqual(det.stats.hits, 1)
        self.assertAlmostEqual(result.actual_x, (CX + 5) / W, places=6)
        self.assertAlmostEqual(result.actual_y, (CY - 40) / H, places=6)

    def test_outside_source_is_miss(self):
        det = CircularPulseDetector()
        scene = _Scene()
        det._entries.append(_Entry(CalibratedCircle((CX + 500) / W, CY / H, R / min(W, H))))
        det.detect(scene.render(), *_source())
        self.assertEqual(det.stats.misses, 1)
        self.assertEqual(det.stats.hits, 0)


class TestCalibration(unittest.TestCase):
    """首次模糊圆形定位：中心/半径偏离均能校准出真实值。"""

    def test_center_offset_calibrated(self):
        det = CircularPulseDetector()
        scene = _Scene()
        result = det.detect(scene.render(), *_source())
        self.assertIsNotNone(result.actual_x)
        self.assertLess(abs(result.actual_x * W - CX), 3.0)
        self.assertLess(abs(result.actual_y * H - CY), 3.0)
        self.assertLess(abs(result.actual_radius * min(W, H) - R), 3.0)

    def test_radius_deviation_calibrated(self):
        det = CircularPulseDetector()
        scene = _Scene(cx=CX, cy=CY, r=72)  # 真实半径远大于源半径
        nx, ny = (CX + 8) / W, (CY - 6) / H
        result = det.detect(scene.render(), nx, ny, 34 / min(W, H), 170 / min(W, H))
        self.assertIsNotNone(result.actual_x)
        self.assertLess(abs(result.actual_radius * min(W, H) - 72), 3.5)

    def test_calibration_failure_returns_idle(self):
        det = CircularPulseDetector()
        blank = np.full((H, W, 3), 60, np.uint8)  # 无任何圆形
        result = det.detect(blank, *_source())
        self.assertFalse(result.detected)
        self.assertEqual(result.state, DetectionState.IDLE)
        self.assertEqual(len(det.calibrated_circles), 0)


class TestNoFalsePositive(unittest.TestCase):
    """无光圈时不得误报。"""

    def _assert_no_detection(self, detector, results):
        fp = sum(1 for _, r in results if r.detected)
        self.assertEqual(fp, 0, f"误报 {fp} 帧")
        self.assertTrue(all(r.cycle_id == 0 for _, r in results))

    def test_static_background(self):
        det = CircularPulseDetector()
        scene = _Scene()
        self._assert_no_detection(det, _run(det, scene, [None] * 60))

    def test_dynamic_3d_background(self):
        det = CircularPulseDetector()
        scene = _Scene()
        results = _run(
            det, scene, [None] * 60,
            cam_per_frame=lambda t: (t * 1.5, t * 0.8),
            particles_per_frame=lambda t: 6,
        )
        self._assert_no_detection(det, results)

    def test_background_with_other_circle_objects(self):
        det = CircularPulseDetector()
        scene = _Scene()
        scene.decoys = [(300, 250, 46), (1050, 520, 38)]  # 效果区外的圆形物体
        results = _run(
            det, scene, [None] * 60,
            cam_per_frame=lambda t: (t * 1.2, -t),
        )
        self._assert_no_detection(det, results)


class TestPulseDetection(unittest.TestCase):
    """光圈扩散 / 消失 / 重现 / 多周期 / 低延迟。"""

    def test_normal_expansion_detected_with_low_latency(self):
        det = CircularPulseDetector()
        scene = _Scene()
        results = _run(det, scene, _timeline(30))
        idx = _first_detected(results)
        self.assertIsNotNone(idx, "正常扩散未被检出")
        print(f"\n[latency] 正常扩散首检延迟: {idx} 帧")
        self.assertLessEqual(idx, 8)

    def test_vanish_at_max_then_reappear(self):
        det = CircularPulseDetector()
        scene = _Scene()
        # 两帧空隙：脉冲消失后按钮回落，再在下一周期重新胀大。
        results = _run(det, scene, _timeline(CYCLE_LEN * 2 + 8, cycle_len=23, blank_frames=2))
        cycles = [r.cycle_id for _, r in results]
        self.assertGreaterEqual(max(cycles), 2, "消失重现后未计入新周期")
        second_cycle = [
            i for i, (_, r) in enumerate(results)
            if i > CYCLE_LEN and r.detected
        ]
        self.assertTrue(second_cycle, "消失后未在下一周期重新检出")

    def test_multiple_cycles_counted(self):
        det = CircularPulseDetector()
        scene = _Scene()
        results = _run(det, scene, _timeline(CYCLE_LEN * 3 + 4))
        max_cycle = max(r.cycle_id for _, r in results)
        self.assertGreaterEqual(max_cycle, 2)
        # 检出不应长期中断（reset 间隙有宽限）。
        missed_run = 0
        for _, r in results[_first_detected(results):]:
            missed_run = 0 if r.detected else missed_run + 1
            self.assertLess(missed_run, 20, "确认后长时间丢失检出")

    def test_low_opacity_ring_detected(self):
        det = CircularPulseDetector()
        scene = _Scene()
        tl = _timeline(40)
        results = []
        for t, radius in enumerate(tl):
            frame = scene.render(ring=radius, alpha=0.07)
            results.append((t, det.detect(frame, *_source())))
        idx = _first_detected(results)
        self.assertIsNotNone(idx, "低透明度光圈未被检出")
        self.assertLessEqual(idx, 10)

    def test_ring_close_to_background_brightness(self):
        det = CircularPulseDetector()
        scene = _Scene()
        tl = _timeline(40)
        results = []
        for t, radius in enumerate(tl):
            frame = scene.render(ring=radius, alpha=0.09)
            results.append((t, det.detect(frame, *_source())))
        idx = _first_detected(results)
        self.assertIsNotNone(idx, "与背景亮度接近的光圈未被检出")


class TestInterferenceRobustness(unittest.TestCase):
    """摄像机移动 / 粒子 / 抖动 / 丢帧 / FPS 波动下仍能检出。"""

    def test_camera_pan_during_pulse(self):
        det = CircularPulseDetector()
        scene = _Scene()
        results = _run(
            det, scene, _timeline(50),
            cam_per_frame=lambda t: (t * 1.6, t * 0.9),
        )
        idx = _first_detected(results)
        self.assertIsNotNone(idx, "摄像机移动时光圈未被检出")
        self.assertLessEqual(idx, 10)

    def test_particles_during_pulse(self):
        det = CircularPulseDetector()
        scene = _Scene()
        results = _run(
            det, scene, _timeline(50),
            particles_per_frame=lambda t: 8,
        )
        idx = _first_detected(results)
        self.assertIsNotNone(idx, "粒子干扰时光圈未被检出")
        self.assertLessEqual(idx, 10)

    def test_peak_radius_jitter_tolerated(self):
        det = CircularPulseDetector()
        scene = _Scene()
        tl = _timeline(50, jitter_rng=np.random.default_rng(3))
        results = _run(det, scene, tl)
        idx = _first_detected(results)
        self.assertIsNotNone(idx, "峰值抖动时光圈未被检出")
        self.assertLessEqual(idx, 10)

    def test_dropped_frames_and_fps_fluctuation(self):
        rng = np.random.default_rng(11)
        clock = {"t": 10.0}

        def unstable_clock():
            clock["t"] += rng.uniform(0.01, 0.09)
            return clock["t"]

        det = CircularPulseDetector(clock=unstable_clock)
        scene = _Scene()
        tl = _timeline(60)
        results = []
        fed = 0
        for t, radius in enumerate(tl):
            if t % 3 == 2:  # 每 3 帧丢 1 帧
                continue
            frame = scene.render(ring=radius, particles=t % 5)
            results.append((fed, det.detect(frame, *_source())))
            fed += 1
        idx = _first_detected(results)
        self.assertIsNotNone(idx, "丢帧 + FPS 波动时光圈未被检出")
        self.assertLessEqual(idx, 10)


class TestResolutionIndependence(unittest.TestCase):
    """同一归一化参数在不同分辨率下行为一致。"""

    def test_resolutions(self):
        for w, h in ((854, 480), (1280, 720), (1920, 1080), (2560, 1440), (1024, 768)):
            with self.subTest(resolution=f"{w}x{h}"):
                s = min(w, h) / min(W, H)
                scene = _Scene(w=w, h=h, cx=int(CX * s), cy=int(CY * s),
                               r=round(R * s))
                det = CircularPulseDetector()
                # 坐标/半径按 min 边归一化：非 16:9 下圆心归一化坐标随布局变化。
                nx, ny = (scene.cx + SRC_DX * s) / w, (scene.cy + SRC_DY * s) / h
                nbr, ner = SRC_R / min(W, H), EFFECT_MAX / min(W, H)
                tl = _timeline(40, r_button=R * s, r_max=EFFECT_MAX * s)
                idx = None
                for t, radius in enumerate(tl):
                    frame = scene.render(ring=radius)
                    result = det.detect(frame, nx, ny, nbr, ner)
                    if result.detected:
                        idx = t
                        break
                self.assertIsNotNone(idx, f"{w}x{h} 未检出")
                # 首个周期内确认即可；严格延迟预算由参考分辨率专项验证。
                self.assertLessEqual(idx, CYCLE_LEN)
                print(f"[latency] {w}x{h}: {idx}")


class TestApiContract(unittest.TestCase):
    """返回值协议与缓存生命周期。"""

    def test_bool_result_equals_detected(self):
        result = DetectionResult(detected=True, confidence=0.9)
        self.assertTrue(bool(result))
        self.assertFalse(bool(DetectionResult()))

    def test_result_defaults(self):
        result = DetectionResult()
        self.assertIs(result.state, DetectionState.IDLE)
        self.assertIsNone(result.peak_radius)
        self.assertEqual(result.cycle_id, 0)

    def test_invalid_inputs_raise(self):
        det = CircularPulseDetector()
        frame = np.zeros((H, W, 3), np.uint8)
        with self.assertRaises(ValueError):
            det.detect(None, 0.5, 0.5, 0.05, 0.1)
        with self.assertRaises(ValueError):
            det.detect(frame, 1.5, 0.5, 0.05, 0.1)
        with self.assertRaises(ValueError):
            det.detect(frame, 0.5, 0.5, 0, 0.1)

    def test_reset_cache_clears_entries(self):
        det = CircularPulseDetector()
        scene = _Scene()
        det.detect(scene.render(), *_source())
        self.assertEqual(len(det.calibrated_circles), 1)
        det.reset_cache()
        self.assertEqual(len(det.calibrated_circles), 0)
        det.detect(scene.render(), *_source())
        self.assertEqual(det.stats.misses, 2)  # 清空后需重新校准

    def test_shared_detector_lifecycle(self):
        """项目唯一入口：共享实例跨调用复用缓存，跟随应用生命周期。"""
        det = get_circular_pulse_detector()
        self.assertIs(det, get_circular_pulse_detector())
        scene = _Scene(cx=300, cy=200, r=40)
        nx, ny = 300 / W, 200 / H
        nbr, ner = 30 / min(W, H), 120 / min(W, H)
        first = detect_circular_pulse(scene.render(), nx, ny, nbr, ner)
        self.assertIsInstance(first, DetectionResult)
        hits_before = det.stats.hits
        second = detect_circular_pulse(scene.render(), nx, ny, nbr, ner)
        self.assertEqual(det.stats.hits, hits_before + 1)  # 缓存命中而非重新校准
        self.assertEqual(
            [(c.x, c.y) for c in det.calibrated_circles if abs(c.x - nx) < 1e-3],
            [(300 / W, 200 / H)],
        )


if __name__ == "__main__":
    unittest.main()
