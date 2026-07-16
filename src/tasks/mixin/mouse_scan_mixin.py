from src.interaction.Mouse import click_down, click_up
from src.core.BaseEfTask import BaseEfTask


class MouseScanMixin(BaseEfTask):

    def drag_scan_area(
        self,
        start_pos: tuple[float, float],
        end_pos: tuple[float, float],
        coverage_ratio: float = 0.05,
        overlap: float = 0.2,
        key: str = "left",
        drag_duration: float = 0.12,
        debug_lines: bool = False,
    ):
        """
        按住鼠标蛇形扫描整个矩形区域（归一化坐标）。

        Args:
            start_pos:
                起点(归一化坐标)

            end_pos:
                终点(归一化坐标)

            coverage_ratio:
                扫描带宽占矩形短边比例。
                推荐 0.02~0.10。

            overlap:
                相邻扫描带重叠比例。
                推荐 0.10~0.30。

            key:
                鼠标按键。

            drag_duration:
                每条扫描线耗时。

            debug_lines:
                是否输出每一条扫描线日志。
        """

        coverage_ratio = max(0.001, min(1.0, coverage_ratio))
        overlap = max(0.0, min(0.95, overlap))

        left = min(start_pos[0], end_pos[0])
        right = max(start_pos[0], end_pos[0])

        top = min(start_pos[1], end_pos[1])
        bottom = max(start_pos[1], end_pos[1])

        width = right - left
        height = bottom - top

        if width <= 0 or height <= 0:
            self.log_info("扫描区域为空，取消扫描。")
            return

        short_side = min(width, height)

        coverage = short_side * coverage_ratio
        step = coverage * (1 - overlap)

        self.log_info(
            f"开始区域扫描: start={start_pos}, end={end_pos}"
        )

        self.log_info(
            f"扫描参数: "
            f"width={width:.4f}, "
            f"height={height:.4f}, "
            f"coverage={coverage:.4f}, "
            f"step={step:.4f}, "
            f"overlap={overlap:.2f}"
        )

        self.active_and_send_mouse_delta(
            only_activate=True,
        )

        scan_start = (left, top)

        if start_pos != scan_start:
            self.smooth_drag(
                start_pos,
                scan_start,
                duration=0.05,
            )

        click_down(
            self.hwnd.hwnd,
            int(scan_start[0]*self.width),
            int(scan_start[1]*self.height),
            key,
        )

        line = 0

        try:

            y = top
            reverse = False

            while y <= bottom + 1e-9:

                if reverse:
                    line_start = (right, y)
                    line_end = (left, y)
                else:
                    line_start = (left, y)
                    line_end = (right, y)

                line += 1

                if debug_lines:
                    self.log_info(
                        f"扫描第 {line} 行: {line_start} -> {line_end}"
                    )

                self.smooth_drag(
                    line_start,
                    line_end,
                    duration=drag_duration,
                )

                next_y = min(bottom, y + step)

                if next_y <= y:
                    break

                if reverse:
                    connect_start = (left, y)
                    connect_end = (left, next_y)
                else:
                    connect_start = (right, y)
                    connect_end = (right, next_y)

                self.smooth_drag(
                    connect_start,
                    connect_end,
                    duration=0.03,
                )

                reverse = not reverse
                y = next_y

        finally:

            click_up(self.hwnd.hwnd,key)

            self.log_info(
                f"区域扫描完成，共扫描 {line} 行。"
            )