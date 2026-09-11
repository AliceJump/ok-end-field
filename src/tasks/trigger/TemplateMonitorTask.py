from ok import Logger, TriggerTask

from src.core.BaseEfTask import BaseEfTask
from src.data.FeatureList import FeatureList as fL
from src.image.hsv_config import HSVRange as hR
from src.icons import Icons

logger = Logger.get_logger(__name__)


class TemplateMonitorTask(BaseEfTask, TriggerTask):
    requires_foreground = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "模板监控"
        self.description = "持续检测指定模板，支持HSV帧处理器和反转"
        self.icon = Icons.Navigation
        self.trigger_interval = 0.5

        feature_options = [member.value for member in fL]
        hsv_options = [member.name for member in hR]

        self.default_config = {
            "模板ID": fL.b.value,
            "识别框": "",
            "模板HSV处理器": "",
            "启用反转": False,
        }

        self.config_type = {
            "模板ID": {"type": "drop_down", "options": feature_options},
            "识别框": {"type": "text_edit"},
            "模板HSV处理器": {"type": "drop_down", "options": [""] + hsv_options},
            "启用反转": {"type": "switch"},
        }

        self.config_description = {
            "模板ID": "要检测的模板ID（FeatureList枚举值）。必填。",
            "识别框": "检测区域框，格式：x1,y1,x2,y2（相对坐标0-1）。留空表示全屏。",
            "模板HSV处理器": "处理模板图像，只保留指定颜色区域参与匹配。留空表示不使用。",
            "启用反转": "是否启用HSV处理器的反转功能。",
        }

    def _parse_box(self, box_str):
        if not box_str or not box_str.strip():
            return None
        try:
            parts = [float(x.strip()) for x in box_str.split(",")]
            if len(parts) == 4:
                return self.box_of_screen(*parts)
        except (ValueError, AttributeError):
            pass
        return None

    def _make_hsv_processor(self, config_key):
        hsv_name = self.config.get(config_key, "")
        if not hsv_name:
            return None
        try:
            hsv_range = hR[hsv_name]
            invert = self.config.get("启用反转", False)
            return self.make_hsv_isolator(hsv_range, invert=invert)
        except (KeyError):
            return None

    def run(self):
        self.check_resolution()
        now = self.next_frame()

        feature_name = self.config.get("模板ID", fL.b.value)
        box = self._parse_box(self.config.get("识别框", ""))
        mask_function = self._make_hsv_processor("模板HSV处理器")

        kwargs = {"frame": now}
        if box is not None:
            kwargs["box"] = box
        if mask_function is not None:
            kwargs["mask_function"] = mask_function

        result = self.find_one(feature_name, **kwargs)
        if result:
            self.log_info(f"检测到模板: {feature_name}")
            return True
        return False
