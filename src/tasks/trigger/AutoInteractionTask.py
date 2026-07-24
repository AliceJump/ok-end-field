import time

from qfluentwidgets import FluentIcon
from pynput.keyboard import Controller, Key
from src.data.FeatureList import FeatureList as fL
from ok import TriggerTask, Logger
from src.core.BaseEfTask import BaseEfTask

logger = Logger.get_logger(__name__)


class AutoInteractionTask(BaseEfTask, TriggerTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config = {
            '_enabled': True,
            '自动跳过剧情': True,
            '自动点击传送': True,
        }
        self.keyboard = Controller()
        self.name = "自动交互"
        self.icon = FluentIcon.ACCEPT

    def run(self):
        self.check_resolution()
        now = self.next_frame()
        if self.config.get('自动跳过剧情', True):
            if self.find_one(fL.skip_dialog_esc, horizontal_variance=0.05, frame=now):
                self.keyboard.press(Key.esc)
                self.keyboard.release(Key.esc)
                time.sleep(0.1)
                start = self.active_time()
                clicked_confirm = False
                while self.active_time() - start < 3:
                    confirm = self.find_confirm()
                    if confirm:
                        self.click(confirm, after_sleep=0.4)
                        clicked_confirm = True
                    elif clicked_confirm:
                        self.log_debug('AutoSkipDialogTask no confirm break')
                        return
                    self.next_frame()
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
