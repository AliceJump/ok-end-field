from ok import Logger, TriggerTask

from src.core.BaseEfTask import BaseEfTask
from src.data.FeatureList import FeatureList as fL
from src.icons import Icons

logger = Logger.get_logger(__name__)


class AutoInteractionTask(BaseEfTask, TriggerTask):
    requires_foreground = True  # 交互需要移动

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动交互"
        self.description = "自动跳过剧情和点击传送按钮"
        self.icon = Icons.Interact

        self.default_config = {
            "_enabled": True,
            "自动跳过剧情": True,
            "自动点击传送": True,
        }

    def run(self):
        self.check_resolution()
        now = self.next_frame()
        if self.config.get("自动跳过剧情", True):
            if self.find_one(fL.skip_dialog_esc, horizontal_variance=0.05, frame=now):
                self.press_esc()
                start = self.active_time()
                while self.active_time() - start < 3:
                    self.next_frame()
                    if self.click_confirm():
                        return
            if self.find_one(
                [fL.baker_icon, fL.baker_page_icon], horizontal_variance=0.05, vertical_variance=0.05, frame=now
            ):
                now = self.next_frame()
                if result := self.find_one(fL.baker_click, horizontal_variance=0.05, vertical_variance=0.1, frame=now):
                    self.click(result, after_sleep=0.4)
                if result := self.find_one(
                    fL.sentence_part_feature, horizontal_variance=0.05, vertical_variance=0.1, frame=now
                ):
                    self.click(result, after_sleep=0.4)
                if result := self.ocr(
                    match=self.lang.AutoSkipDialogTask.k_92399078,
                    box=self.box_of_screen(1294 / 1920, 806 / 1080, 1412 / 1920, 860 / 1080),
                ):
                    self.click(result, after_sleep=0.4)
                    return
        if self.config.get("自动点击传送", True):
            # 传送按钮只存在于地图界面：先用侧栏 in_map 特征确认界面，
            # 未在地图界面时跳过匹配，避免每周期白跑全屏模板 + Canny 兜底
            in_map_box = self.box_of_screen(0.027, 0.531, 0.051, 0.896)
            result = self.find_one(fL.transfer_go, frame=now)
            if result:
                if self.find_one(fL.in_map, box=in_map_box, frame=now):
                    self.click(result, after_sleep=0.4)
            elif self.find_one(fL.in_map, box=in_map_box, frame=now):
                # 未普通命中且已在地图界面：再尝试一次轮廓（Canny）匹配，对按钮反色/主题变化不敏感
                result = self.find_one(fL.transfer_go, frame=now, canny_lower=50, canny_higher=150, threshold=0.8)
                if result:
                    self.click(result, after_sleep=0.4)
