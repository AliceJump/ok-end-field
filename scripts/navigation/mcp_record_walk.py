"""MCP 手动路线采集入口，复用小地图定位采样记录。"""

from src.tasks.localization.MinimapPositionRecorder import CONFIG_DURATION, MinimapPositionRecorder


class Task(MinimapPositionRecorder):
    def reset_to_start(self):
        assert self.in_world(), "请先进入大世界"

    def verify_start_state(self):
        assert self.in_world(), "请先进入大世界"

    def run(self):
        original_config = self.config
        self.config = {**original_config, CONFIG_DURATION: 300}
        try:
            return super().run()
        finally:
            self.config = original_config
