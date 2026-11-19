"""通过 MCP 从当前游戏位置采集轨迹。"""

from pathlib import Path

from src.tasks.localization.ManualWalkRecorder import ManualWalkRecorder


class Task(ManualWalkRecorder):
    def _trajectory_path(self):
        return Path("logs/navigation/manual_walk.jsonl")
