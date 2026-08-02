from qfluentwidgets import FluentIcon

from src.data.FeatureList import FeatureList as fL
from src.tasks.mixin.navigation_mixin import NavigationMixin

secondary_objective_direction_dot = [
    fL.secondary_objective_direction_dot,
    fL.secondary_objective_direction_dot_light,
    fL.secondary_objective_direction_dot_light_two,
    fL.secondary_objective_direction_dot_light_three,
]


class TestBlueDotAlign(NavigationMixin):
    """蓝点归中测试（对应 Test.py 历史版本：418056a）。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "蓝点归中测试"
        self.group_name = "测试"
        self.group_icon = FluentIcon.DEVELOPER_TOOLS
        self.description = "将次要目标方向蓝点对齐到屏幕中心"
        self.visible = self.debug

    def run(self):
        self.align_ocr_or_find_target_to_center(
            ocr_match_or_feature_name_list=secondary_objective_direction_dot,
            threshold=0.8,
            only_x=True,
            ocr=False,
            raise_if_fail=False,
        )
