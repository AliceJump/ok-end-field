from qfluentwidgets import FluentIcon

from src.tasks.mixin.battle_mixin import BattleMixin


class TestCombatTemplateMatch(BattleMixin):
    """持续检测技能和终结技模板在对应技能框中的匹配结果。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "战斗模板持续检测"
        self.group_name = "测试"
        self.group_icon = FluentIcon.DEVELOPER_TOOLS
        self.description = "持续检测 skill 和 ult 模板并输出命中位置与匹配分数"
        self.visible = self.debug
        self.default_config.update({
            "检测间隔(秒)": 0.5,
        })
        self.config_description.update({
            "检测间隔(秒)": "每轮模板检测之间的等待时间，最小 0.05 秒",
        })

    def run(self):
        interval = max(0.05, float(self.config.get("检测间隔(秒)", 0.5) or 0.5))
        scan_count = 0

        self.log_info(f"开始战斗模板持续检测，间隔 {interval:.2f} 秒")
        try:
            while True:
                scan_count += 1
                checks = []
                for prefix in ("skill", "ult"):
                    boxes = self._battle_feature_boxes(prefix)
                    for box_index, box in enumerate(boxes, start=1):
                        feature = f"{prefix}_{box_index}"
                        result = self.find_one(feature, box=box)
                        position = f"({result.x},{result.y})" if result is not None else "-"
                        score = f"{result.confidence:.3f}" if result is not None else "-"
                        checks.append(
                            f"{feature}->框{box_index}"
                            f"({box.x},{box.y},{box.width},{box.height}) "
                            f"{'命中' if result is not None else '未命中'}@{position}, score={score}"
                        )

                self.log_info(f"[{scan_count}] {'; '.join(checks)}")
                self.sleep(interval)
        finally:
            self.log_info(f"战斗模板持续检测结束，共检测 {scan_count} 轮")
