"""Preview a wide, smooth pitch sweep with slower horizontal rotation."""

import json
import math
from dataclasses import asdict, dataclass, fields

import win32api
import win32gui
from qfluentwidgets import FluentIcon

from src.core.BaseEfTask import BaseEfTask
from src.tasks.test.pitch_scan_input import physical_coordinates, read_input_environment, split_mouse_delta


@dataclass(frozen=True)
class PitchScanTrajectory:
    amplitude: int
    horizontal_speed: float
    period: float
    cycles: int
    ramp: float = 0.25

    @property
    def duration(self):
        return self.period * self.cycles + self.ramp

    def position(self, elapsed):
        """Integrate a cosine speed ramp, keeping position and velocity continuous."""
        elapsed = max(0.0, min(elapsed, self.duration))
        remaining = self.duration - elapsed
        if elapsed < self.ramp:
            motion_time = (elapsed - self.ramp / math.pi * math.sin(math.pi * elapsed / self.ramp)) / 2
        elif remaining < self.ramp:
            motion_time = (
                self.period * self.cycles
                - (remaining - self.ramp / math.pi * math.sin(math.pi * remaining / self.ramp)) / 2
            )
        else:
            motion_time = elapsed - self.ramp / 2
        return (
            self.horizontal_speed * motion_time,
            -self.amplitude * math.sin(math.tau * motion_time / self.period),
        )


class PitchScanTestTask(BaseEfTask):
    TICK_SECONDS = 0.01
    HORIZONTAL_SPEED_RATIO = 1 / 8
    ENVIRONMENT_CHECK_SECONDS = 0.5

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "大幅俯仰扫描实测"
        self.group_name = "工具与调试"
        self.icon = FluentIcon.SEARCH
        self.description = "在游戏主界面测试大幅平滑俯仰扫描；先中键复位并强制鼠标归中，完成后自动停止。"
        self.default_config.update({"俯仰振幅(1080p像素)": 240, "测试周期数": 5})
        self.config_description.update(
            {
                "俯仰振幅(1080p像素)": "上下单侧鼠标输入幅度，以1080p测试值为基准，按实际窗口高度缩放。增大振幅会延长周期，保持移动速度。",
                "测试周期数": "完整上下摆动的次数；水平持续旋转，按停止键可随时结束。",
            }
        )
        self.config_type.update(
            {
                "俯仰振幅(1080p像素)": {"min": 30, "max": 600},
                "测试周期数": {"min": 1, "max": 10},
            }
        )

    def _prepare_scan(self):
        if not self.active_and_send_mouse_delta(only_activate=True):
            raise RuntimeError("游戏窗口激活失败，取消俯仰扫描")
        environment = self._read_input_environment()
        center = self.screen_center()
        if self.click(*center, key="middle", down_time=0.02, after_sleep=0.3, move_back=False) is False:
            raise RuntimeError("中键复位失败，取消俯仰扫描")
        # Force the native client center even if capture coordinates have a
        # different DPI scale or origin. Do not scale relative camera input.
        expected = environment.physical_center
        with physical_coordinates():
            win32api.SetCursorPos(expected)
            actual = win32api.GetCursorPos()
        if (
            win32gui.GetForegroundWindow() != environment.hwnd
            or max(abs(a - b) for a, b in zip(actual, expected, strict=True)) > environment.center_tolerance
        ):
            raise RuntimeError("鼠标未归中或游戏失去焦点，取消俯仰扫描")
        return environment

    def _read_input_environment(self):
        return read_input_environment(self.get_game_hwnd(), (self.width, self.height))

    def _check_input_environment(self, baseline):
        current = self._read_input_environment()
        changed = [
            field.name for field in fields(baseline) if getattr(current, field.name) != getattr(baseline, field.name)
        ]
        if changed:
            raise RuntimeError(f"扫描环境发生变化（{', '.join(changed)}），停止本次扫描；重新运行任务将读取新参数")

    def run(self):
        amplitude = int(self.config.get("俯仰振幅(1080p像素)", 240))
        cycles = int(self.config.get("测试周期数", 5))
        if not 30 <= amplitude <= 600 or not 1 <= cycles <= 10:
            raise ValueError("俯仰振幅须为30至600，测试周期数须为1至10")

        # Use the navigation search's explicit timing budget, excluding detector
        # latency: 40 segments * (2 smooth steps * 0.005s + 0.1s between segments).
        # Its old 30px / 8-cycle pitch peaks at 2*pi*30/0.55 pixels per second.
        # Lengthen each cycle in proportion to amplitude instead of speeding it up.
        # Slow yaw independently so it does not visually overwhelm the pitch sweep.
        # LevelCameraCtrl normalizes Mouse X by Screen.width and Mouse Y by
        # Screen.height. Scale each axis independently to cancel those divisors.
        # Physical client dimensions approximate Unity's current output size;
        # saved resolution, DPI-virtualized sizes and capture crops do not.
        reference_duration = 40 * (2 * 0.005 + 0.1)
        environment = self._prepare_scan()
        width, height = environment.client_size
        trajectory = PitchScanTrajectory(
            amplitude=round(amplitude * height / 1080),
            horizontal_speed=40 * 192 / reference_duration * self.HORIZONTAL_SPEED_RATIO * width / 1920,
            period=reference_duration / 8 * amplitude / 30,
            cycles=cycles,
        )
        self.log_info(
            f"扫描输入环境 {environment.profile_id}："
            f"{json.dumps(asdict(environment), ensure_ascii=False, sort_keys=True)}；"
            f"每包上限 {environment.mouse.packet_limit}；游戏配置保存值仅用于环境识别，尚未标定灵敏度换算"
        )
        self.log_info(
            f"俯仰扫描实测：物理客户区 {width}x{height}，采集 {self.width}x{self.height}，"
            f"振幅 ±{trajectory.amplitude} 输入单位，"
            f"周期 {trajectory.period:.2f}s，水平速度 {trajectory.horizontal_speed:.1f} 输入单位/s，"
            f"周期数 {cycles}，预计 {trajectory.duration:.2f}s"
        )
        elapsed = 0.0
        previous_clock = self.active_time()
        started = previous_clock
        previous_x = previous_y = 0
        next_environment_check = 0.0
        packets = 0
        peak_interval = 0.0
        while elapsed < trajectory.duration:
            self.sleep(self.TICK_SECONDS)
            now = self.active_time()
            if win32gui.GetForegroundWindow() != environment.hwnd:
                raise RuntimeError("游戏失去焦点，停止俯仰扫描")
            if elapsed >= next_environment_check:
                self._check_input_environment(environment)
                next_environment_check = elapsed + self.ENVIRONMENT_CHECK_SECONDS
            # A stalled scheduler must stretch the preview instead of emitting a
            # large catch-up jump. active_time also excludes framework pauses.
            peak_interval = max(peak_interval, now - previous_clock)
            elapsed = min(trajectory.duration, elapsed + max(0.0, min(now - previous_clock, 2 * self.TICK_SECONDS)))
            previous_clock = now
            x, y = (round(value) for value in trajectory.position(elapsed))
            dx, dy = x - previous_x, y - previous_y
            for packet_x, packet_y in split_mouse_delta(dx, dy, environment.mouse.packet_limit):
                if win32gui.GetForegroundWindow() != environment.hwnd:
                    raise RuntimeError("游戏失去焦点，停止俯仰扫描")
                if not self.active_and_send_mouse_delta(dx=packet_x, dy=packet_y, activate=False, steps=1, delay=0):
                    raise RuntimeError("鼠标位移发送失败，停止俯仰扫描")
                packets += 1
            previous_x, previous_y = x, y
        self.log_info(
            f"俯仰扫描实测完成：环境 {environment.profile_id}，共发送 {packets} 包，"
            f"水平总输入 {previous_x}，俯仰净输入 {previous_y}，"
            f"实际活动耗时 {self.active_time() - started:.2f}s，最大调度间隔 {peak_interval:.3f}s"
        )
