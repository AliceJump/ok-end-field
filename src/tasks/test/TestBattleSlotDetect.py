from qfluentwidgets import FluentIcon
from ok import Box

from src.core.BaseEfTask import BaseEfTask


class TestBattleSlotDetect(BaseEfTask):
    """检测战斗技能和终结技四个独立位置。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "战斗技能位置检测"
        self.group_name = "测试"
        self.group_icon = FluentIcon.DEVELOPER_TOOLS
        self.description = "逐个检测四个技能位置并输出模板匹配结果"
        self.visible = self.debug

    def run(self):
        features = [f"skill_{index}" for index in range(1, 5)]
        features += [f"ult_{index}" for index in range(1, 5)]
        initial_boxes = []

        for index in range(1, 5):
            try:
                initial_boxes.append(self.get_box_by_name(f"skill_{index}"))
            except (ValueError, AttributeError):
                continue

        if not initial_boxes:
            self.log_warning("未找到技能模板初始位置")
            return False

        max_width = max(box.width for box in initial_boxes)
        max_height = max(box.height for box in initial_boxes)
        for feature in features:
            try:
                template = self.get_feature_by_name(feature)
            except (ValueError, AttributeError):
                continue
            if template is not None:
                max_width = max(max_width, template.width)
                max_height = max(max_height, template.height)

        boxes = []
        for initial_box in sorted(initial_boxes, key=lambda box: box.x):
            center_x = initial_box.x + initial_box.width // 2
            center_y = initial_box.y + initial_box.height // 2
            x = max(0, center_x - max_width // 2)
            y = max(0, center_y - max_height // 2)
            x = min(x, max(0, self.width - max_width))
            y = min(y, max(0, self.height - max_height))
            boxes.append(Box(x, y, width=max_width, height=max_height))

        self.log_debug(
            f"战斗位置检测搜索框: 数量={len(boxes)}, 最大尺寸={max_width}x{max_height}"
        )
        results = []
        for position, box in enumerate(boxes, start=1):
            matches = self.find_feature(feature_name=features, box=box)
            match_info = [
                f"{match.name}@({match.x},{match.y}) conf={match.confidence:.3f}"
                for match in matches
            ]
            result = (
                f"位置{position}: 初始框=({box.x},{box.y},{box.width},{box.height}), "
                f"检测到={match_info or ['无']}"
            )
            results.append(result)
            self.log_info(result)

        if not results:
            self.log_warning("未找到技能模板初始位置")
            return False

        self.info_set("战斗位置检测", results)
        return True
