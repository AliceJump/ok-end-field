import re
import time
from typing import Any

import win32con

from ok import BaseTask
from src.essence.essence_recognizer import EssenceInfo, read_essence_info


class BaseEfTask(BaseTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._logged_in = False

    def in_bg(self):
        return not self.hwnd.is_foreground()

    def find_confirm(self):
        return self.find_one('skip_dialog_confirm', horizontal_variance=0.05, vertical_variance=0.05)

    def in_combat_world(self):
        in_combat_world = self.find_one('top_left_tab')
        if in_combat_world:
            self._logged_in = True
        return in_combat_world

    def find_f(self):
        return self.find_one('pick_f', vertical_variance=0.05)

    def ensure_main(self, esc=True, time_out=30):
        self.info_set('current task', f'wait main esc={esc}')
        if not self.wait_until(lambda: self.is_main(esc=esc), time_out=time_out, raise_if_not_found=False):
            raise Exception('Please start in game world and in team!')
        self.info_set('current task', f'in main esc={esc}')

    def in_world(self):
        in_world = self.find_one('esc') and self.find_one('b') and self.find_one('c')
        if in_world:
            self._logged_in = True
        return in_world

    def is_main(self, esc=False):
        if self.in_world():
            self._logged_in = True
            return True
        if self.wait_login():
            return True
        if esc:
            self.back(after_sleep=1.5)

    def wait_login(self):
        if not self._logged_in:
            if self.in_world():
                self._logged_in = True
                return True
            elif self.find_one('monthly_card') or self.find_one('logout'):
                self.click(after_sleep=1)
                return False
            elif close := (self.find_one(
                    'reward_ok', horizontal_variance=0.1, vertical_variance=0.1, ) or self.find_one(
                'one_click_claim', horizontal_variance=0.1, vertical_variance=0.1) or self.find_one('check_in_close',
                                                                                                    horizontal_variance=0.1,
                                                                                                    vertical_variance=0.1,
                                                                                                    threshold=0.75)):
                self.click(close, after_sleep=1)
                return False

    def read_essence_info(self) -> EssenceInfo | None:
        return read_essence_info(self)
