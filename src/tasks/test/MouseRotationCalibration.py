# -*- coding: utf-8 -*-
"""鼠标视角旋转系数标定测试任务。

通过现有输入接口发送已知鼠标相对位移（dx），并用 get_arrow_angle()
读取小地图箭头朝向变化（Δyaw），实测当前输入接口的位移单位与
游戏摄像机 yaw 旋转之间的关系：

    yaw_per_pixel = Δyaw / dx

本任务只负责标定测量与系数计算，不接入正式自动转向逻辑，
也不把结果写入正式配置（第一版不做持久化）。
"""

from qfluentwidgets import FluentIcon

from src.core.BaseEfTask import BaseEfTask


def angle_delta(after: float, before: float) -> float:
    """计算 [0, 360) 角度间的最短角度差，返回 (-180, 180]。

    例如 angle_delta(2, 358) == 4.0，正确跨越 0/360 边界。
    """
    return (after - before + 180.0) % 360.0 - 180.0


class MouseRotationCalibration(BaseEfTask):
    """鼠标视角旋转系数标定测试（工具与调试分组）。"""

    requires_foreground = True  # 视角旋转需要前台

    # 基础标定参数（可通过任务配置覆盖，集中在此处便于统一调整）
    CALIBRATION_DX = 100  # 每个 sample 的测试位移量，正负方向交替
    CALIBRATION_DX_LIST = ""  # 标定位移列表（逗号分隔多个像素值，如 "200, 400, 800"）；留空则用 标定位移dx 单值
    REPEAT_COUNT = 4  # 标定采样次数（仅单位移模式下生效：正负交替 repeat 次）
    W_HOLD_TIME = 0.3  # 按 W 刷新朝向时的长按时长（秒），太短角色不会真正位移/朝向不刷新
    ANGLE_REFRESH_DELAY = 0.1  # 松开 W 后等待画面更新的时间（秒）
    TURN_SETTLE_DELAY = 0.3  # 发送鼠标位移后、按 W 刷新前等待转向完成的时间（秒）
    MIN_SCORE = 0.6  # 箭头角度检测最低置信度

    # 验证流程参数（标定后用系数反算位移实测验证；可通过任务配置覆盖）
    VERIFY_TARGET_YAW = 90.0  # 验证目标角度（度）；0=跳过验证；正值=鼠标左移方向，负值=鼠标右移方向
    VERIFY_ANGLES = ""  # 验证角度列表（逗号分隔多个角度，如 "30, 90, 180, -90"）；留空则用 验证目标角度(度) 单角度
    VERIFY_COUNT = 2  # 每个目标角度的验证重复次数
    VERIFY_TOLERANCE = 5.0  # 实测角度与目标角度误差容差（度），超过判 FAIL
    VERIFY_PAIR_LR = True  # 左右方向成对验证：每个角度自动补上相反方向（正角度补负角度，负角度补正角度）
    MANUAL_YAW_PER_PIXEL = ""  # 手动指定系数（留空用本次标定结果，可跳过标定只做验证）

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "鼠标视角旋转系数标定"
        self.group_name = "工具与调试"
        self.group_icon = FluentIcon.DEVELOPER_TOOLS
        self.description = "发送已知鼠标位移实测并标定 yaw_per_pixel 旋转系数"
        self.visible = self.debug

        self.default_config = {
            "标定位移dx": self.CALIBRATION_DX,
            "标定位移列表(逗号分隔)": self.CALIBRATION_DX_LIST,
            "重复次数": self.REPEAT_COUNT,
            "W长按时间(秒)": self.W_HOLD_TIME,
            "角度刷新等待(秒)": self.ANGLE_REFRESH_DELAY,
            "转向后等待(秒)": self.TURN_SETTLE_DELAY,
            "最低置信度": self.MIN_SCORE,
            "验证目标角度(度)": self.VERIFY_TARGET_YAW,
            "验证角度列表(逗号分隔)": self.VERIFY_ANGLES,
            "验证次数": self.VERIFY_COUNT,
            "验证误差容差(度)": self.VERIFY_TOLERANCE,
            "左右方向成对验证": self.VERIFY_PAIR_LR,
            "手动yaw_per_pixel(留空用标定)": self.MANUAL_YAW_PER_PIXEL,
        }
        self.config_description = {
            "标定位移dx": "每个 sample 的测试方向位移量，正负交替测试（标定位移列表留空时生效）",
            "标定位移列表(逗号分隔)": "一次标定多个位移量（如 200, 400, 800），每个位移正负各测一次，覆盖多量程；留空则只用 标定位移dx 单值",
            "重复次数": "标定采样次数，正负方向交替（仅单位移模式生效）",
            "W长按时间(秒)": "按 W 刷新朝向时的长按时长，太短则朝向不刷新",
            "角度刷新等待(秒)": "松开 W 后等待画面更新的时间",
            "转向后等待(秒)": "发送鼠标位移后、按 W 刷新前等待转向完成的时间；太短可能在转向未完成时就按 W 刷新朝向",
            "最低置信度": "箭头角度检测的最低置信度，低于此值该 sample 作废",
            "验证目标角度(度)": "用标定系数反算位移并实测验证的角度；正值=向左转(鼠标左移)，负值=向右转，0=不验证",
            "验证角度列表(逗号分隔)": "一次验证多个角度（如 30, 90, 180, -90），每个角度都实测；留空则只用 验证目标角度(度) 单角度",
            "验证次数": "每个目标角度的验证重复次数",
            "验证误差容差(度)": "实测角度与目标角度误差超过该值判 FAIL",
            "左右方向成对验证": "开启后每个验证角度自动补上相反方向（如 44 会自动补 -44），左右两方向都实测；关闭则只测列表里的方向",
            "手动yaw_per_pixel(留空用标定)": "手动填标定系数可直接跳过标定只做验证；留空则先用本次标定结果",
        }

        # 运行时参数（在 run() 中从配置读取，默认取类常量）
        self._w_hold_time = self.W_HOLD_TIME
        self._angle_refresh_delay = self.ANGLE_REFRESH_DELAY
        self._turn_settle_delay = self.TURN_SETTLE_DELAY
        self._min_score = self.MIN_SCORE

    def run(self):
        """执行标定（或手动系数）并用系数验证目标角度。

        流程：
          1. 读配置（标定 + 验证参数）。
          2. 标定阶段：交替发送 ±dx 统计 yaw_per_pixel；
             若配置了手动系数则跳过标定，直接用该系数。
          3. 验证阶段：用系数反算 dx = 目标角度 / k，发送该位移并实测
             实际转角与目标角度的误差。
        """
        self.log_info("=== Mouse Rotation Calibration ===", notify=True)

        calibration_dx = int(self.config.get("标定位移dx", self.CALIBRATION_DX))
        calibration_dx_list_raw = str(
            self.config.get("标定位移列表(逗号分隔)", self.CALIBRATION_DX_LIST)).strip()
        repeat_count = max(1, int(self.config.get("重复次数", self.REPEAT_COUNT)))
        self._w_hold_time = max(
            0.0, float(self.config.get("W长按时间(秒)", self.W_HOLD_TIME)))
        self._angle_refresh_delay = max(
            0.0, float(self.config.get("角度刷新等待(秒)", self.ANGLE_REFRESH_DELAY)))
        self._turn_settle_delay = max(
            0.0, float(self.config.get("转向后等待(秒)", self.TURN_SETTLE_DELAY)))
        self._min_score = max(
            0.0, min(1.0, float(self.config.get("最低置信度", self.MIN_SCORE))))

        verify_target_yaw = float(
            self.config.get("验证目标角度(度)", self.VERIFY_TARGET_YAW))
        verify_angles_raw = str(
            self.config.get("验证角度列表(逗号分隔)", self.VERIFY_ANGLES)).strip()
        verify_count = max(1, int(self.config.get("验证次数", self.VERIFY_COUNT)))
        verify_tolerance = max(
            0.0, float(self.config.get("验证误差容差(度)", self.VERIFY_TOLERANCE)))
        pair_lr_raw = self.config.get("左右方向成对验证", self.VERIFY_PAIR_LR)
        if isinstance(pair_lr_raw, str):
            pair_lr = pair_lr_raw.strip().lower() in (
                "1", "true", "yes", "on", "是", "开启", "开")
        else:
            pair_lr = bool(pair_lr_raw)
        manual_k = self._parse_manual_k(
            self.config.get("手动yaw_per_pixel(留空用标定)", self.MANUAL_YAW_PER_PIXEL))

        self.log_info(
            f"Calibration parameters:  dx={calibration_dx}  repeat={repeat_count}  "
            f"w_hold_time={self._w_hold_time}  refresh_delay={self._angle_refresh_delay}  "
            f"turn_settle_delay={self._turn_settle_delay}  min_score={self._min_score}"
        )

        # 生成标定位移测试计划：列表非空时每个位移值正负各测一次（多量程）；
        # 列表为空时用单值 dx 正负交替 repeat_count 次（原逻辑）。
        dx_plan = self._parse_dx_plan(calibration_dx_list_raw, calibration_dx, repeat_count)
        if len(dx_plan) > 1:
            self.log_info(
                f"Calibration plan:  {len(dx_plan)} 个 sample -> "
                + ", ".join(f"{d:+d}" for d in dx_plan)
            )

        if manual_k is not None:
            # 手动系数模式：跳过标定，直接用用户提供的系数做验证
            if manual_k > 0:
                # 系数恒为负（鼠标右移 dx>0 视角左转 Δyaw<0，k=Δyaw/dx<0），
                # 正数必为漏写负号，自动取负修正
                self.log_info(
                    f"手动 yaw_per_pixel 为正数，自动取负修正: "
                    f"{manual_k:.5f} -> {-manual_k:.5f}",
                    notify=True,
                )
                manual_k = -manual_k
            self.log_info(
                f"Manual yaw_per_pixel = {manual_k:.5f}，跳过标定直接验证")
            stats = {
                "mean_k": manual_k, "std": 0.0,
                "k_pos": manual_k, "k_neg": manual_k, "n": 0,
            }
        else:
            valid_samples = []
            for index, dx in enumerate(dx_plan, start=1):
                sample = self._run_sample(index, dx)
                if sample is not None:
                    valid_samples.append(sample)
            stats = self._compute_stats(valid_samples)
            self._report(stats, len(dx_plan))

        # 验证阶段：用标定系数反算位移并实测（支持多角度）
        verify_angles = self._parse_verify_angles(verify_angles_raw, verify_target_yaw)
        if not verify_angles:
            if verify_target_yaw == 0:
                self.log_info(
                    "Verification skipped:  验证目标角度 = 0 且未配置角度列表")
            else:
                self.log_info("Verification skipped:  无有效验证角度")
            return
        if pair_lr:
            verify_angles = self._pair_left_right(verify_angles)
        if stats is None or abs(stats["mean_k"]) < 1e-9:
            self.log_info(
                "Verification skipped:  标定系数无效（无有效样本或 k=0）", notify=True)
            return
        if len(verify_angles) > 1:
            self.log_info(
                f"Verification plan:  {len(verify_angles)} 个角度 -> "
                + ", ".join(f"{a:+.2f}°" for a in verify_angles)
            )
        for angle in verify_angles:
            self._verify(angle, stats["k_pos"], stats["k_neg"],
                         verify_count, verify_tolerance)

    def _read_arrow_angle(self):
        """按 W 长按一小段时间刷新朝向，等待画面更新后读取箭头角度（关闭角度平滑）。

        - 必须长按：瞬时按键（down_time=0.02s）不足以让角色真正位移，小地图
          朝向不会刷新，会读到陈旧角度；长按 _w_hold_time 秒让朝向更新。
        - 必须关闭默认的低分平滑：平滑会在 score 较低时返回上一帧角度，
          导致实际检测失败被误认为 Δyaw = 0。
        """
        self.press_key("w", down_time=self._w_hold_time)
        self.sleep(self._angle_refresh_delay)
        angle, score = self.get_arrow_angle(smoothing_threshold=None)
        return angle, 0.0 if score is None else score

    def _send_delta(self, dx: int) -> bool:
        """通过现有输入接口发送单步鼠标位移。

        steps=1 确保测试中的 dx 与底层实际发送的相对位移一致，
        不使用多 steps 拆分鼠标移动。
        """
        try:
            self.active_and_send_mouse_delta(dx=dx, dy=0, steps=1, delay=0)
            return True
        except Exception as e:
            self.log_error(f"鼠标位移发送失败 dx={dx}: {e}", exception=e)
            return False

    def _score_ok(self, score: float) -> bool:
        return score is not None and score >= self._min_score

    def _score_reason(self, score: float, phase: str) -> str:
        """把不合格的 score 归类为检测失败或置信度不足。"""
        if score is None or score <= 0:
            return f"{phase}_detection_failed"
        return f"{phase}_low_score"

    def _run_sample(self, index: int, dx: int):
        """执行单个标定 sample。

        Returns:
            dict | None: 有效样本返回数据 dict，失败返回 None。
        """
        try:
            before_angle, before_score = self._read_arrow_angle()
            if before_angle is None:
                self.log_info(
                    f"Calibration sample rejected:  reason=before_detection_failed  "
                    f"before_score={before_score:.3f}  after_score=-"
                )
                return None
            if not self._score_ok(before_score):
                self.log_info(
                    f"Calibration sample rejected:  "
                    f"reason={self._score_reason(before_score, 'before')}  "
                    f"before_score={before_score:.3f}  after_score=-"
                )
                return None

            if not self._send_delta(dx):
                self.log_info(
                    f"Calibration sample rejected:  reason=input_failed  dx={dx}"
                )
                return None

            # 发位移后先纯等待转向完成，再按 W 刷新朝向读角度，避免时序耦合
            self.sleep(self._turn_settle_delay)

            after_angle, after_score = self._read_arrow_angle()
            if after_angle is None:
                self.log_info(
                    f"Calibration sample rejected:  reason=after_detection_failed  "
                    f"before_score={before_score:.3f}  after_score={after_score:.3f}"
                )
                # 恢复视角（反向位移）；恢复失败也不伪装成成功
                self._send_delta(-dx)
                return None
            if not self._score_ok(after_score):
                self.log_info(
                    f"Calibration sample rejected:  "
                    f"reason={self._score_reason(after_score, 'after')}  "
                    f"before_score={before_score:.3f}  after_score={after_score:.3f}"
                )
                # 恢复视角（反向位移）；恢复失败也不伪装成成功
                self._send_delta(-dx)
                return None

            delta_yaw = angle_delta(after_angle, before_angle)
            k = delta_yaw / dx
            sample = {
                "dx": dx,
                "before_angle": before_angle,
                "after_angle": after_angle,
                "delta_yaw": delta_yaw,
                "before_score": before_score,
                "after_score": after_score,
                "k": k,
            }
            self.log_info(
                f"sample {index}:  dx={dx:+d}  before={before_angle:.2f} "
                f"score={before_score:.3f}  after ={after_angle:.2f} "
                f"score={after_score:.3f}  delta ={delta_yaw:+7.2f}  "
                f"k     ={k:+10.5f}"
            )

            # 恢复视角（反向位移），避免多次测试后摄像机持续累积旋转。
            # 恢复动作不需要作为标定 sample。
            self._send_delta(-dx)
            # 等待画面稳定后再进入下一个 sample
            self.sleep(self._angle_refresh_delay)
            return sample

        except Exception as e:
            self.log_error(
                f"Calibration sample {index} 异常: {e}", exception=e, notify=True)
            self.log_info("Calibration sample rejected:  reason=exception")
            return None

    @staticmethod
    def _parse_manual_k(raw) -> float | None:
        """解析手动系数；空/None/非法输入返回 None（表示用本次标定结果）。"""
        if raw is None:
            return None
        text = str(raw).strip()
        if not text:
            return None
        try:
            return float(text)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_dx_plan(raw: str, single_dx: int, repeat_count: int) -> list[int]:
        """生成标定位移测试序列。

        - raw 为空：回退到单值 single_dx 正负交替 repeat_count 次（原逻辑）。
        - raw 非空：对列表中每个位移值依次 +d / -d 各测一次，覆盖多量程；
          过滤空段、非法值、0；去重并保留原顺序。
        """
        values = []
        for part in str(raw).split(","):
            part = part.strip()
            if not part:
                continue
            try:
                value = int(float(part))
            except (TypeError, ValueError):
                continue
            if value == 0 or value in values:
                continue
            values.append(value)
        if not values:
            return [single_dx if i % 2 == 0 else -single_dx
                    for i in range(repeat_count)]
        plan = []
        for v in values:
            plan.append(v)
            plan.append(-v)
        return plan

    @staticmethod
    def _parse_verify_angles(raw: str, single: float) -> list[float]:
        """解析验证角度列表（逗号分隔，可含正负）。

        - 过滤空段、非法值、0 度（无意义）；去重并保留原顺序。
        - raw 无有效角度时回退到单角度 single（single 为 0 则返回空列表）。
        """
        angles = []
        for part in str(raw).split(","):
            part = part.strip()
            if not part:
                continue
            try:
                value = float(part)
            except (TypeError, ValueError):
                continue
            if value == 0 or value in angles:
                continue
            angles.append(value)
        if not angles and single != 0:
            angles = [single]
        return angles

    @staticmethod
    def _pair_left_right(angles: list[float]) -> list[float]:
        """把每个角度补上相反方向，实现左右成对验证；保留顺序并去重。

        例如 [44.0, 55.0] -> [44.0, -44.0, 55.0, -55.0]。
        """
        paired = []
        for a in angles:
            if a not in paired:
                paired.append(a)
            if -a not in paired:
                paired.append(-a)
        return paired

    def _compute_stats(self, valid_samples):
        """统计标定样本，返回 {mean_k, std, k_pos, k_neg, n}；无有效样本返回 None。

        k_pos 为 dx>0（鼠标右移）方向的均值，k_neg 为 dx<0（鼠标左移）方向均值；
        某方向无样本时回退到总体均值，避免除以 0。
        """
        n = len(valid_samples)
        if n == 0:
            return None

        ks = [s["k"] for s in valid_samples]
        mean_k = sum(ks) / n
        std = (sum((k - mean_k) ** 2 for k in ks) / n) ** 0.5

        positive_k = [s["k"] for s in valid_samples if s["dx"] > 0]
        negative_k = [s["k"] for s in valid_samples if s["dx"] < 0]
        k_pos = sum(positive_k) / len(positive_k) if positive_k else mean_k
        k_neg = sum(negative_k) / len(negative_k) if negative_k else mean_k
        return {"mean_k": mean_k, "std": std, "k_pos": k_pos, "k_neg": k_neg, "n": n}

    def _report(self, stats, expected_count: int) -> None:
        """打印标定汇总：yaw_per_pixel、std、正负方向各自均值、状态。"""
        if stats is None:
            self.log_info("Result:  valid_samples = 0  status = FAIL", notify=True)
            return

        n = stats["n"]
        status = "PASS" if n == expected_count else "WARN"

        self.log_info(
            f"Result:  yaw_per_pixel = {stats['mean_k']:.5f}  "
            f"std            = {stats['std']:.5f}  "
            f"valid_samples  = {n}  "
            f"positive_k     = {stats['k_pos']:.5f}  "
            f"negative_k     = {stats['k_neg']:.5f}  "
            f"status         = {status}",
            notify=True,
        )

    def _verify(self, target_yaw: float, k_pos: float, k_neg: float,
                count: int, tolerance: float) -> None:
        """用标定系数验证目标角度：dx = target_yaw / k，实测转角误差。

        - 按方向选择系数：target_yaw > 0（左转）用 k_neg，< 0（右转）用 k_pos，
          避免左右不对称被均值掩盖；dx 与 k 带符号相除，方向自动正确。
        - 每次验证后反向位移恢复视角，保证各次起点一致。
        """
        self.log_info(f"=== Verification: {target_yaw:+.2f}° ===", notify=True)
        k = k_neg if target_yaw > 0 else k_pos
        dx = int(round(target_yaw / k)) if abs(k) > 1e-9 else 0
        if dx == 0:
            self.log_info("Verify skipped:  invalid k or dx = 0", notify=True)
            return
        self.log_info(
            f"Verify parameters:  target_yaw={target_yaw:+.2f}°  "
            f"k={k:.5f}  dx={dx:+d}"
        )

        results = []
        for i in range(1, count + 1):
            before_angle, before_score = self._read_arrow_angle()
            if before_angle is None or not self._score_ok(before_score):
                self.log_info(
                    f"verify {i}:  rejected reason="
                    f"{self._score_reason(before_score, 'before')}  "
                    f"before_score={before_score:.3f}"
                )
                continue

            if not self._send_delta(dx):
                self.log_info(f"verify {i}:  rejected reason=input_failed  dx={dx}")
                continue

            # 发位移后先纯等待转向完成，再按 W 刷新朝向读角度，避免时序耦合
            self.sleep(self._turn_settle_delay)

            after_angle, after_score = self._read_arrow_angle()
            if after_angle is None or not self._score_ok(after_score):
                self.log_info(
                    f"verify {i}:  rejected reason="
                    f"{self._score_reason(after_score, 'after')}  "
                    f"before_score={before_score:.3f}  after_score={after_score:.3f}"
                )
                # 恢复视角；恢复失败也不伪装成成功
                self._send_delta(-dx)
                continue

            actual = angle_delta(after_angle, before_angle)
            error = actual - target_yaw
            ok = abs(error) <= tolerance
            results.append({"actual": actual, "error": error, "ok": ok})
            self.log_info(
                f"verify {i}:  target={target_yaw:+7.2f}°  "
                f"actual={actual:+7.2f}°  error={error:+7.2f}°  "
                f"dx={dx:+d}  {'PASS' if ok else 'FAIL'}"
            )

            # 恢复视角，等待画面稳定后进入下一次验证
            self._send_delta(-dx)
            self.sleep(self._angle_refresh_delay)

        n = len(results)
        if n == 0:
            self.log_info("Verify Result:  valid = 0  status = FAIL", notify=True)
            return

        actual_mean = sum(r["actual"] for r in results) / n
        mean_error = sum(r["error"] for r in results) / n
        max_abs_error = max(abs(r["error"]) for r in results)
        all_ok = all(r["ok"] for r in results)
        if all_ok and n == count:
            status = "PASS"
        elif n > 0:
            status = "WARN"
        else:
            status = "FAIL"

        self.log_info(
            f"Verify Result:  target={target_yaw:+7.2f}°  "
            f"actual_mean={actual_mean:+7.2f}°  "
            f"mean_error={mean_error:+7.2f}°  "
            f"max_abs_error={max_abs_error:7.2f}°  "
            f"valid={n}  status={status}",
            notify=True,
        )
