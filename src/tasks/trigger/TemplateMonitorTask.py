from ok import Logger, TriggerTask

from src.core.BaseEfTask import BaseEfTask
from src.data.FeatureList import FeatureList as fL
from src.icons import Icons
from src.image.hsv_config import HSVRange as hR

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
            "模板HSV处理器": "",
        }

        self.config_type = {
            "模板ID": {"type": "drop_down", "options": feature_options},
            "模板HSV处理器": {"type": "drop_down", "options": [""] + hsv_options},
        }

        self.config_description = {
            "模板ID": "要检测的模板ID（FeatureList枚举值）。必填。",
            "模板HSV处理器": "处理模板图像，只保留指定颜色区域参与匹配。留空表示不使用。",
        }

    def _make_hsv_processor(self, config_key):
        hsv_name = self.config.get(config_key, "")
        if not hsv_name:
            return None
        try:
            hsv_range = hR[hsv_name]
            return self.make_hsv_isolator(hsv_range, invert=False)
        except KeyError:
            return None

    def run(self):
        self.check_resolution()
        now = self.next_frame()

        feature_name = self.config.get("模板ID", fL.b.value)
        mask_function = self._make_hsv_processor("模板HSV处理器")

        kwargs = {"frame": now}
        if mask_function is not None:
            kwargs["mask_function"] = mask_function

        result = self.find_one(feature_name, **kwargs)
        if result:
            self.log_info(f"检测到模板: {feature_name}")
            return True
        self.log_info(f"未检测到模板: {feature_name}")
        return False
