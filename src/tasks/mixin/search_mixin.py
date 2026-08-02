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
    ):
        """原地旋转视角整圈搜索，每转一段调用一次 check_func，命中即返回其结果。

        Args:
            check_func: 无参回调，返回真值表示命中（可直接返回检测结果）。
            segments: 分段数，默认 40 段。
            step_ratio: 每段旋转位移占屏幕宽度的比例，默认 0.1。
            steps: 相对鼠标移动的平滑步数。
            delay: 相对鼠标移动每一步的间隔秒数。
            between_delay: 每段旋转后、检测前的等待秒数。

        Returns:
            check_func 的命中结果；整圈未命中返回 None。
        """
        segment_dx = max(1, int(self.width * step_ratio))
        for _ in range(segments):
            self.active_and_send_mouse_delta(
                dx=segment_dx,
                dy=0,
                activate=True,
                steps=steps,
                delay=delay,
            )
            if between_delay > 0:
                self.sleep(between_delay)
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
    ):
        """WASD 轻微移动搜索，每次移动后调用一次 check_func，命中即返回其结果。

        Args:
            check_func: 无参回调，返回真值表示命中（可直接返回检测结果）。
            passes: 各方向往复轮数，默认 3 轮；None 表示不限制轮数（配合 time_out 使用）。
            duration: 每次按移动键的持续秒数。
            keys: 依次尝试的移动键，默认 W/A/S/D 前后左右。
            time_out: 总搜索时限（秒），<=0 表示不限制。

        Returns:
            check_func 的命中结果；未命中返回 None。
        """
        start = self.active_time() if time_out > 0 else None
        count = 0
        while passes is None or count < passes * len(keys):
            for key in keys:
                self.move_keys(key, duration=duration)
                result = check_func()
                if result:
                    return result
                count += 1
                if start is not None and self.active_time() - start >= time_out:
                    return None
        return None
