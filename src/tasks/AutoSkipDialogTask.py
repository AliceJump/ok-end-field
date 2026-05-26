import time

from qfluentwidgets import FluentIcon
from src.data.FeatureList import FeatureList as fL
from ok import TriggerTask, Logger
from src.tasks.BaseEfTask import BaseEfTask

logger = Logger.get_logger(__name__)


class AutoSkipDialogTask(BaseEfTask, TriggerTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config = {'_enabled': True}
        self.name = "自动跳过剧情"
        self.icon = FluentIcon.ACCEPT

    def run(self):
        if self.find_one(fL.skip_dialog_esc, horizontal_variance=0.05):
            self.send_key('esc', after_sleep=0.1)  # 确认使用send_key：esc为系统通用退出键，非游戏可配置热键
            start = time.time()
            clicked_confirm = False
            while time.time() - start < 3:
                confirm = self.find_confirm()
                if confirm:
                    self.click(confirm, after_sleep=0.4)
                    clicked_confirm = True
                elif clicked_confirm:
                    self.log_debug('AutoSkipDialogTask no confirm break')
                    return
                self.next_frame()
        if self.find_one(fL.baker_icon, horizontal_variance=0.05, vertical_variance=0.05):
            self.next_frame()
            if result:= self.find_one(fL.baker_click, horizontal_variance=0.05, vertical_variance=0.05):
                self.click(result, after_sleep=0.4)
            if result:= self.ocr(match=self.lang.AutoSkipDialogTask.k_92399078, box=self.box_of_screen(1294/1920, 806/1080, 1412/1920, 860/1080)):
                self.click(result, after_sleep=0.4)
                return
        
            
