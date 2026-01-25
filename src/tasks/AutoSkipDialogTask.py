import time

from qfluentwidgets import FluentIcon

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
        if self.find_one('skip_dialog_esc', horizontal_variance=0.05):
            self.send_key('esc', after_sleep=0.1)
            start = time.time()
            clicked_confirm = False
            while time.time() - start < 3:
                confirm = self.find_confirm()
                if confirm:
                    self.click(confirm, after_sleep=0.4)
                    clicked_confirm = True
                elif clicked_confirm:
                    self.log_debug('AutoSkipDialogTask no confirm break')
                    break
                self.next_frame()
