from ok import TriggerTask, Logger

from src.core.BaseEfTask import BaseEfTask
from src.data.FeatureList import FeatureList as fL
from src.icons import Icons

logger = Logger.get_logger(__name__)


class AutoInteractionTask(BaseEfTask, TriggerTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动交互"
        self.description = "自动跳过剧情和点击传送按钮"
        self.icon = Icons.Interact

        self.default_config = {
            '_enabled': True,
            '自动跳过剧情': True,
            '自动点击传送': True,
        }

    def run(self):
        self.check_resolution()
        now = self.next_frame()
        if self.config.get('自动跳过剧情', True):
            if self.find_one(fL.skip_dialog_esc, horizontal_variance=0.05, frame=now):
                self.press_esc()
                start = self.active_time()
                while self.active_time() - start < 3:
                    self.next_frame()
                    if self.click_confirm():
                        return
            if self.find_one([fL.baker_icon, fL.baker_page_icon], horizontal_variance=0.05, vertical_variance=0.05, frame=now):
                now = self.next_frame()
                if result:= self.find_one(fL.baker_click, horizontal_variance=0.05, vertical_variance=0.1, frame=now):
                    self.click(result, after_sleep=0.4)
                if result:= self.find_one(fL.sentence_part_feature, horizontal_variance=0.05, vertical_variance=0.1, frame=now):
                    self.click(result, after_sleep=0.4)
                if result:= self.ocr(match=self.lang.AutoSkipDialogTask.k_92399078, box=self.box_of_screen(1294/1920, 806/1080, 1412/1920, 860/1080)):
                    self.click(result, after_sleep=0.4)
                    return
        if self.config.get('自动点击传送', True):
            if result := self.find_one(feature=fL.transfer_go, frame=now):
                if self.find_one(feature=fL.in_map,box=self.box_of_screen(0.027, 0.531, 0.051, 0.896) , frame=now):
                    self.click(result, after_sleep=0.4)
