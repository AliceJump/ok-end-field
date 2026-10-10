import math

from src.core.BaseEfTask import BaseEfTask


class SearchMixin(BaseEfTask):
    """通用搜索辅助：整圈旋转视角搜索、WASD 轻微左右移动搜索。"""

    def rotate_search(
        self,
        check_func,
        segments: int = 40,
        step_ratio: float = 0.1,
        steps: int = 2,
        delay: float = 0.005,
        between_delay: float = 0.0,
        detection_multiplier: int = 2,
        pitch_amplitude: int = 30,
        pitch_cycles: int = 2,
    ):
        """沿平滑正弦俯仰轨迹原地旋转整圈搜索，命中即返回检测结果。

        水平总位移保持与旧实现一致：``segments * int(width * step_ratio)``。
        检测采样次数由 ``detection_multiplier`` 放大，每次水平位移按累计目标重新分配，
        不改变整圈的总 yaw 像素量。俯仰以正弦曲线围绕起始视角摆动，完整扫描结束时
        回到起始 pitch；若中途命中则立即停止并保持当前视角。

        Args:
            check_func: 无参回调，返回真值表示命中（可直接返回检测结果）。
            segments: 旧实现的水平分段数，继续用于定义整圈总水平位移。
            step_ratio: 旧实现每段水平位移占屏幕宽度的比例。
            steps: 单次相对鼠标移动的平滑步数。
            delay: 原实现单次鼠标平滑步的等待预算；提高检测频次后按采样倍数均分，
                保持整圈鼠标移动等待总量不变。
            between_delay: 原实现每个水平分段的等待预算；提高检测频次后会按采样倍数均分，
                保持整圈额外等待总量不变。
            detection_multiplier: 每个原水平分段拆成多少个检测采样点，默认 2 倍频率。
            pitch_amplitude: 1080p 基准下的最大俯仰鼠标位移，默认 30px；按分辨率缩放。
            pitch_cycles: 一整圈内完成的正弦俯仰周期数，默认 2 个周期。

        Returns:
            check_func 的命中结果；整圈未命中返回 None。
        """
        if segments <= 0:
            return None

        detection_multiplier = max(1, int(detection_multiplier))
        pitch_cycles = max(1, int(pitch_cycles))
        sample_count = segments * detection_multiplier

        segment_dx = max(1, int(self.width * step_ratio))
        total_dx = segment_dx * segments
        pitch_amplitude_px = 0 if pitch_amplitude <= 0 else self.scale_distance(pitch_amplitude)
        sample_move_delay = delay / detection_multiplier if delay > 0 else 0.0
        sample_between_delay = between_delay / detection_multiplier if between_delay > 0 else 0.0

        previous_x = 0
        previous_pitch = 0

        for sample_index in range(1, sample_count + 1):
            progress = sample_index / sample_count

            # 用累计目标拆分水平位移，避免整数舍入改变旧实现的总 dx。
            target_x = round(total_dx * progress)
            dx = target_x - previous_x
            previous_x = target_x

            # 负 dy 为先抬头；完整周期结束时 target_pitch 必然回到 0。
            phase = math.tau * pitch_cycles * progress
            target_pitch = round(-pitch_amplitude_px * math.sin(phase))
            dy = target_pitch - previous_pitch
            previous_pitch = target_pitch

            self.active_and_send_mouse_delta(
                dx=dx,
                dy=dy,
                activate=True,
                steps=steps,
                delay=sample_move_delay,
            )
            if sample_between_delay > 0:
                self.sleep(sample_between_delay)
            result = check_func()
            if result:
                return result
        return None

    def strafe_search(
        self,
        check_func,
        passes: int | None = 3,
        duration: float = 0.2,
        keys=("w", "a", "s", "d"),
        time_out: float = -1,
        reset_position: bool = False,
    ):
        """WASD 轻微移动搜索，每次方向尝试移动后检测，可选未命中归正回原位。

        Args:
            check_func: 无参回调，返回真值表示命中（可直接返回检测结果）。
            passes: 各方向尝试轮数，默认 3 轮；None 表示不限制轮数（配合 time_out 使用）。
            duration: 每次按移动键的持续秒数。
            keys: 依次尝试的移动键，默认 W/A/S/D 前后左右。
            time_out: 总搜索时限（秒），<=0 表示不限制。
            reset_position: 是否在每次检测失败后反向移动归正，默认 False。

        Returns:
            check_func 的命中结果；未命中返回 None。
        """
        opposite = {"w": "s", "s": "w", "a": "d", "d": "a"}
        start = self.active_time() if time_out > 0 else None
        count = 0

        def restore(key):
            if reset_position:
                self.move_keys(opposite[key], duration=duration)

        result = check_func()  # 先原地检测一次
        if start is not None and self.active_time() - start >= time_out:
            return None
        if result:
            return result

        while passes is None or count < passes * len(keys):
            for key in keys:
                self.move_keys(key, duration=duration)
                count += 1

                if start is not None and self.active_time() - start >= time_out:
                    restore(key)
                    return None

                result = check_func()

                # check_func 自身耗时可能跨越 timeout
                if start is not None and self.active_time() - start >= time_out:
                    restore(key)
                    return None

                if result:
                    return result

                restore(key)

        return None
