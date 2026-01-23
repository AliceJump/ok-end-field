import re
import time
from typing import Any

import win32con

from ok import BaseTask

class BaseEfTask(BaseTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._logged_in = False

    def in_bg(self):
        return not self.hwnd.is_foreground()

    def find_confirm(self):
        return self.find_one('skip_dialog_confirm', horizontal_variance=0.05, vertical_variance=0.05)

    # def click(self, x=-1, y=-1, move_back=False, name=None, down_time=0.02, move=True, key="left", after_sleep=0.01):
    #     self.executor.interaction.operate(lambda: self.do_click(x, y, down_time=down_time, key=key), block=True)
    #     self.sleep(after_sleep)

    # def do_click(self, x=-1, y=-1, move_back=False, name=None, down_time=0.02, move=True, key="left"):
    #     click_pos = self.executor.interaction.make_mouse_position(x, y)
    #     if key == "left":
    #         btn_down = win32con.WM_LBUTTONDOWN
    #         btn_mk = win32con.MK_LBUTTON
    #         btn_up = win32con.WM_LBUTTONUP
    #     elif key == "middle":
    #         btn_down = win32con.WM_MBUTTONDOWN
    #         btn_mk = win32con.MK_MBUTTON
    #         btn_up = win32con.WM_MBUTTONUP
    #     else:
    #         btn_down = win32con.WM_RBUTTONDOWN
    #         btn_mk = win32con.MK_RBUTTON
    #         btn_up = win32con.WM_RBUTTONUP
    #     self.executor.interaction.post(btn_down, btn_mk, click_pos
    #               )
    #     time.sleep(down_time)
    #     self.executor.interaction.post(btn_up, 0, click_pos
    #               )

    # def send_key(self, key, after_sleep=0, down_time=0.04, **kwargs):
    #     vk_code = self.executor.interaction.get_key_by_str(key)
    #     self.executor.interaction.post(win32con.WM_KEYDOWN, vk_code, 0x1e0001)
    #     if down_time > 0.1:
    #         time.sleep(down_time)
    #     else:
    #         self.executor.interaction.post(win32con.WM_CHAR, vk_code, 0x1e0001)
    #     self.executor.interaction.post(win32con.WM_KEYUP, vk_code, 0xc01e0001)
    #     if down_time <= 0.1:
    #         time.sleep(down_time)
    #     else:
    #         time.sleep(0.02)

    def wait_login(self):
        if not self._logged_in:
            # if self.find_one('login_account', vertical_variance=0.1, threshold=0.7):
            #     self.wait_until(lambda: self.find_one('login_account', threshold=0.7) is None,
            #                     pre_action=lambda: self.click_relative(0.5, 0.9, after_sleep=3), time_out=30)
            #     self.wait_until(lambda: self.find_one('monthly_card', threshold=0.7) or self.in_team_and_world(),
            #                     pre_action=lambda: self.click_relative(0.5, 0.9, after_sleep=3), time_out=120)
            #     self.wait_until(lambda: self.in_team_and_world(),
            #                     post_action=lambda: self.click_relative(0.5, 0.9, after_sleep=3), time_out=5)
            #     self.log_info('Auto Login Success', notify=True)
            #     self._logged_in = True
            #     self.sleep(3)
            #     return True
            texts = self.ocr(log=True)
            if login := self.find_boxes(texts, boundary=self.box_of_screen(0.3, 0.3, 0.7, 0.7), match="登录"):
                self.click(login)
                self.log_info('点击登录按钮!')
                return False
            if self.find_boxes(texts, match=re.compile("请重启游戏")):                
                self.log_info('游戏更新成功, 游戏即将重启')
                self.click(self.find_boxes(texts, match="确认"))
                result = self.start_device()
                self.log_info(f'start_device end {result}')
                self.sleep(30)
                return False            
            if start := self.find_boxes(texts, boundary='bottom_right', match=["开始游戏", re.compile("进入游戏")]):
                if not self.find_boxes(texts, boundary='bottom_right', match="登录"):
                    self.click(start)
                    self.log_info(f'点击开始游戏! {start}')
                    return False



