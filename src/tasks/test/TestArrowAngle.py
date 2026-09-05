from qfluentwidgets import FluentIcon

from src.core.BaseEfTask import BaseEfTask


class TestArrowAngle(BaseEfTask):
    """箭头角度实时读取测试（对应 Test.py 历史版本：505fe60）。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "箭头角度实时读取"
        self.group_name = "测试"
        self.group_icon = FluentIcon.DEVELOPER_TOOLS
        self.description = "持续读取并显示当前箭头的角度"
        self.visible = self.debug

        self.interval = 0.3

    def run(self):
        self.log_info("=== 箭头角度实时检测开始 ===", notify=True)
        self.log_info("按 Ctrl+C 停止\n")

        try:
            iteration = 0
            while True:
                iteration += 1

                angle, score = self.get_arrow_angle(
                    two_stage=True,
                    benchmark_width=2560,
                )

                status = "✓" if score > 0.75 else "⚠"

                self.log_info(f"{status} [#{iteration:03d}] 角度: {angle:6.1f}°    置信度: {score:.4f}")

                if iteration % 10 == 0:
                    self.log_info("-" * 50)

                self.sleep(self.interval)

        except KeyboardInterrupt:
            self.log_info("\n已停止角度检测", notify=True)
        except Exception as e:
            self.log_info(f"发生错误: {e}", notify=True)
