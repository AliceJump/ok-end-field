"""编队判定测试任务：通过 BattleMixin.detect_team() 识别战斗头像，循环判定编队组成。"""

from qfluentwidgets import FluentIcon

from src.tasks.mixin.battle_mixin import BattleMixin


class TeamCompositionDetectTask(BattleMixin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "编队判定测试"
        self.group_name = "工具与调试"
        self.description = "循环判定战斗界面左下角4个角色的编队组成（框架 find_one 模板匹配）"
        self.icon = FluentIcon.ROBOT
        self.visible = self.debug
        self.default_config = {
            "扫描间隔(秒)": 0.5,
            "最小匹配分数": 0.5,
        }
        self.config_description = {
            "扫描间隔(秒)": "每次判定之间的间隔时间（秒）",
            "最小匹配分数": "匹配分数低于该值的角色判定为未知",
        }

    def run(self):
        interval = max(0.1, float(self.config.get("扫描间隔(秒)", 0.5) or 0.5))
        min_score = float(self.config.get("最小匹配分数", 0.5) or 0.5)
        self.log_info(self.tr("编队判定启动（框架 find_one 模式）"), notify=True)
        detect_count = 0
        try:
            while True:
                frame = self.next_frame()
                if frame is None:
                    self.sleep(interval)
                    continue
                detect_count += 1
                # 带分数版本（调试用）
                team_with_scores = self.detect_team_with_scores(frame)
                team_text = " | ".join(
                    f"{name}({score:.2f})" if score >= min_score else self.tr("未知({score:.2f})").format(score=score)
                    for name, score in team_with_scores
                )
                self.log_info(self.tr("编队判定[{count}]: {team}").format(count=detect_count, team=team_text))
                self.sleep(interval)
        finally:
            self.log_info(self.tr("编队判定结束: 共判定 {count} 次").format(count=detect_count), notify=True)
