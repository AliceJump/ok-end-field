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

    # 基础标定参数（可通过任务配置覆盖，集中在此处便于统一调整）
    CALIBRATION_DX = 100  # 每个 sample 的测试位移量，正负方向交替
    REPEAT_COUNT = 4  # 标定采样次数
    ANGLE_REFRESH_DELAY = 0.1  # 按 W 刷新朝向后的画面等待时间（秒）
    MIN_SCORE = 0.6  # 箭头角度检测最低置信度

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "鼠标视角旋转系数标定"
        self.group_name = "工具与调试"
        self.group_icon = FluentIcon.DEVELOPER_TOOLS
        self.description = "发送已知鼠标位移实测并标定 yaw_per_pixel 旋转系数"
        self.visible = self.debug

        self.default_config = {
            "标定位移dx": self.CALIBRATION_DX,
            "重复次数": self.REPEAT_COUNT,
            "角度刷新等待(秒)": self.ANGLE_REFRESH_DELAY,
            "最低置信度": self.MIN_SCORE,
        }
        self.config_description = {
            "标定位移dx": "每个 sample 的测试方向位移量，正负交替测试",
            "重复次数": "标定采样次数，正负方向交替",
            "角度刷新等待(秒)": "按 W 刷新朝向后的画面等待时间",
            "最低置信度": "箭头角度检测的最低置信度，低于此值该 sample 作废",
        }

        # 运行时参数（在 run() 中从配置读取，默认取类常量）
        self._angle_refresh_delay = self.ANGLE_REFRESH_DELAY
        self._min_score = self.MIN_SCORE

    def run(self):
        """执行标定：交替发送 ±dx，读取角度差并统计 yaw_per_pixel。"""
        self.log_info("=== Mouse Rotation Calibration ===", notify=True)

        calibration_dx = int(self.config.get("标定位移dx", self.CALIBRATION_DX))
        repeat_count = max(1, int(self.config.get("重复次数", self.REPEAT_COUNT)))
        self._angle_refresh_delay = max(
            0.0, float(self.config.get("角度刷新等待(秒)", self.ANGLE_REFRESH_DELAY)))
        self._min_score = max(
            0.0, min(1.0, float(self.config.get("最低置信度", self.MIN_SCORE))))

        self.log_info(
            f"Calibration parameters:  dx={calibration_dx}  repeat={repeat_count}  "
            f"refresh_delay={self._angle_refresh_delay}  min_score={self._min_score}"
        )

        valid_samples = []
        for index in range(1, repeat_count + 1):
            # 正负方向交替：+dx, -dx, +dx, -dx, ...
            dx = calibration_dx if index % 2 == 1 else -calibration_dx
            sample = self._run_sample(index, dx)
            if sample is not None:
                valid_samples.append(sample)

        self._report(valid_samples, repeat_count)

    def _read_arrow_angle(self):
        """按 W 刷新朝向，等待画面更新后读取箭头角度（关闭角度平滑）。

        标定必须关闭默认的低分平滑：平滑会在 score 较低时返回上一帧角度，
        导致实际检测失败被误认为 Δyaw = 0。
        """
        self.press_key("w")
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

    def _report(self, valid_samples, repeat_count: int) -> None:
        """汇总统计：yaw_per_pixel、std、正负方向各自均值。"""
        n = len(valid_samples)
        if n == 0:
            self.log_info("Result:  valid_samples = 0  status = FAIL", notify=True)
            return

        ks = [s["k"] for s in valid_samples]
        mean_k = sum(ks) / n
        std = (sum((k - mean_k) ** 2 for k in ks) / n) ** 0.5

        positive_k = [s["k"] for s in valid_samples if s["dx"] > 0]
        negative_k = [s["k"] for s in valid_samples if s["dx"] < 0]
        positive_mean = sum(positive_k) / len(positive_k) if positive_k else float("nan")
        negative_mean = sum(negative_k) / len(negative_k) if negative_k else float("nan")

        if n == repeat_count:
            status = "PASS"
        elif n > 0:
            status = "WARN"
        else:
            status = "FAIL"

        self.log_info(
            f"Result:  yaw_per_pixel = {mean_k:.5f}  "
            f"std            = {std:.5f}  "
            f"valid_samples  = {n}  "
            f"positive_k     = {positive_mean:.5f}  "
            f"negative_k     = {negative_mean:.5f}  "
            f"status         = {status}",
            notify=True,
        )
