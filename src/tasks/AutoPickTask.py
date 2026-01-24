import time

import cv2
import numpy as np
from qfluentwidgets import FluentIcon

from ok import Logger, TriggerTask
from src.tasks.BaseEfTask import BaseEfTask

logger = Logger.get_logger(__name__)


class AutoPickTask(BaseEfTask, TriggerTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动拾取"
        self.description = "大世界自动拾取"
        self.icon = FluentIcon.SHOPPING_CART
        self.default_config = {'_enabled': True}
        self.last_box_name = None
        self.last_pick_time = 0
        self.default_config.update({
            "白名单": ["采集", "萤壳虫", "打开", "荞花", "柑实"]
        })
        self.hint_time = 0

    def run(self):
        if self.in_world():
            while button_f := self.find_f():
                text_zone = button_f.copy(x_offset=button_f.width * 6, width_offset=button_f.width * 12,
                                          y_offset=-button_f.height, height_offset=button_f.height * 12)
                texts = self.wait_ocr(box=text_zone)
                if not texts:
                    self.log_error('pick can not ocr texts')
                    return
                start = time.time()
                icon_zone = button_f.copy(x_offset=button_f.width * 3.3, width_offset=button_f.width * 0.8,
                                          y_offset=-button_f.height * 0.2, height_offset=button_f.height * 0.85,
                                          name='choice')
                white_percent = 0
                if any(keyword in texts[0].name for keyword in self.config.get("白名单", [])):
                    if self.debug:
                        self.screenshot('pick')
                    self.log_debug('pick white_list {}'.format(texts[0].name))
                    self.pick()
                    self.sleep(0.2)
                    return
                while time.time() - start < 0.3:
                    white_percent = self.calculate_color_percentage(white_color, icon_zone)
                    if white_percent > 0.1:
                        break
                    self.sleep(0.01)
                text_count = len(texts)
                # self.draw_boxes('icon_zone', icon_zone)
                # gray_percent = is_mostly_grayscale(icon_zone.crop_frame(self.frame))
                # self.screenshot('icon_zone', frame=icon_zone)
                self.log_debug(f'pick_up text_count {text_count} / {white_percent}')
                # if self.debug:
                #     self.screenshot('pick')
                if white_percent < 0.1:
                    if self.debug:
                        self.screenshot('pick')
                        self.screenshot('pick_wg', frame=icon_zone.crop_frame(self.frame))
                    self.log_info('pick because not gray/white icon {} {}'.format(texts, white_percent))
                    self.pick(text_count)
                    return
                if time.time() - self.hint_time > 5:
                    self.log_info('not in white_list {}'.format(texts[0].name))
                    self.hint_time = time.time()
                self.sleep(0.2)

    def pick(self, count=1):
        for _ in range(count):
            self.send_key('f', after_sleep=0.1)


white_color = {
    'r': (245, 255),  # Red range
    'g': (245, 255),  # Green range
    'b': (245, 255)  # Blue range
}

gray_color = {
    'r': (40, 90),  # Red range
    'g': (40, 90),  # Green range
    'b': (40, 90),  # Blue range
}


def is_mostly_grayscale(frame, threshold=10):
    # Split the frame into B, G, and R channels
    b, g, r = cv2.split(frame)

    # Calculate absolute differences between channels
    diff_rg = cv2.absdiff(r, g)
    diff_gb = cv2.absdiff(g, b)
    diff_br = cv2.absdiff(b, r)

    # A pixel is "gray" if all channel differences are below the threshold
    gray_mask = (diff_rg < threshold) & (diff_gb < threshold) & (diff_br < threshold)

    # Calculate the percentage of gray pixels
    gray_percentage = (np.sum(gray_mask) / frame.size * 3)
    return gray_percentage
