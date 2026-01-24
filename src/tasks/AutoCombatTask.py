import re
import time

import cv2
import numpy as np
from numpy.ma.core import is_string_or_list_of_strings
from qfluentwidgets import FluentIcon

from ok import TriggerTask, Logger
from src.tasks.BaseEfTask import BaseEfTask

logger = Logger.get_logger(__name__)


class AutoCombatTask(BaseEfTask, TriggerTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config = {'_enabled': True}
        self.name = "自动战斗"
        self.description = "自动战斗(进入战斗后自动战斗直到结束)"
        self.icon = FluentIcon.ACCEPT
        self.default_config.update({
            "技能释放": "1234",
            # "攻击快捷键": "",
            "后台结束战斗通知": True
        })
        self.config_description.update({
            "技能释放": "满技能时, 开始释放技能, 如1123",
            # "攻击快捷键": "如果设置则使用攻击按键代替鼠标左键",
        })
        self.lv_regex = re.compile(r"(?i)lv|\d{2}")

    def run(self):
        if not self.in_combat(required_yellow=1):
            return
        raw_skill_config = self.config.get("技能释放", "")

        # Parse to local variable
        skill_sequence = self._parse_skill_sequence(raw_skill_config)
        if self.debug:
            self.screenshot('enter_combat')
        self.click(key='middle')
        while True:
            skill_count = self.get_skill_bar_count()
            if skill_count < 0 and (self.ocr_lv() or not self.in_team()):
                if self.debug:
                    self.screenshot('out_of_combat')
                self.log_info("自动战斗结束!", notify=self.config.get("后台结束战斗通知") and self.in_bg())
                break
            elif self.use_e_skill():
                continue
            elif self.use_ult():
                continue
            elif skill_count == 3:
                last_count = skill_count
                i = 0
                while True:
                    current_count = self.get_skill_bar_count()
                    if current_count <= 0:
                        self.log_debug("skill count less than 0 while using skills {}".format(current_count))
                        break
                    elif self.use_e_skill():
                        continue
                    elif current_count != last_count:
                        i += 1
                        self.log_debug("skill success use next".format(i))
                        if i >= len(skill_sequence):
                            break
                    # use skill
                    start = time.time()
                    last_attack = start
                    while time.time() - start < 6:
                        count = self.get_skill_bar_count()
                        if count == current_count:
                            self.send_key(skill_sequence[i], after_sleep=0.1)
                        elif self.use_e_skill():
                            continue
                        elif count < 0:
                            self.log_debug('skill -1 when using skills {}'.format(count))
                            break
                        elif count < current_count:
                            self.log_debug('use skill success')
                            break
                        elif time.time() - last_attack > 0.3:
                            last_attack = time.time()
                            self.click(after_sleep=0.1)
                        self.next_frame()
            else:
                self.click(after_sleep=0.2)
            self.sleep(0.01)

    def _parse_skill_sequence(self, raw_config: str) -> list[str]:
        """
        Parses and validates the skill configuration string.
        """
        if not raw_config:
            return []

        trimmed_config = raw_config.strip()
        sequence = []
        valid_skills = {'1', '2', '3', '4'}

        for char in trimmed_config:
            if char not in valid_skills:
                raise ValueError(f"Invalid skill character '{char}' detected. Skills can only be 1-4.")
            sequence.append(char)

        return sequence

    def use_ult(self):
        ults = ['1', '2', '3', '4']
        for ult in ults:
            if self.find_one("ult_" + ult):
                self.send_key_down(ult)
                self.wait_until(lambda :not self.in_combat())
                self.send_key_up(ult)
                self.wait_in_combat(time_out=8)
                return True

    def wait_in_combat(self, time_out=3, click=False):
        start = time.time()
        while time.time() - start < time_out:
            if self.in_combat():
                return True
            elif click:
                self.click(after_sleep=0.4)
            else:
                self.sleep(0.1)

    def ocr_lv(self):
        lv = self.ocr(0.02, 0.89, 0.23,0.93,
                       match=self.lv_regex, name='lv_text')
        # logger.debug('lvs {}'.format(lv))
        if len(lv) > 0:
            return True
        lv = self.ocr(0.02, 0.89, 0.23,0.93, frame_processor=isolate_white_text_to_black,
                       match=self.lv_regex, name='lv_text')
        if len(lv) > 0:
            return True

    def use_e_skill(self):
        if skill_e := self.find_one('skill_e', threshold=0.7):
            self.log_debug('found skill e {}'.format(skill_e))
            self.send_key('e', after_sleep=0.1)
            return True

    def in_combat(self, required_yellow=0):
        return self.get_skill_bar_count() >= required_yellow and self.in_team() and not self.ocr_lv()

    def in_team(self):
        return self.find_one('skill_1') and self.find_one('skill_2') and self.find_one('skill_3') and self.find_one('skill_4')

    def get_skill_bar_count(self):

        skill_area = self.frame[self.height_of_screen(1940 / 2160):self.height_of_screen(1983 / 2160),
              self.width_of_screen(1586 / 3840):self.width_of_screen(2266 / 3840)]
        # self.screenshot('skill_area', frame=skill_area)
        if not has_rectangles(skill_area):
            # logger.debug('no rectangles found')
            return -1

        count = 0
        y_start = 1958
        y_end = 1970
        if self.check_is_pure_color_in_4k(1604, y_start, 1796, y_end, yellow_skill_color):
            count += 1
            if self.check_is_pure_color_in_4k(1824, y_start, 2013, y_end, yellow_skill_color):
                count += 1
                if self.check_is_pure_color_in_4k(2043, y_start, 2231, y_end, yellow_skill_color):
                    count += 1
        if count == 0:
            # self.log_debug('count is 0, check left white')
            has_white_left = self.check_is_pure_color_in_4k(1604, y_start, 1614, y_end, white_skill_color, threshold=0.1)
            if not has_white_left:
                count = -1
        return count

    def check_is_pure_color_in_4k(self, x1, y1, x2, y2, color_range=None, threshold=0.9):
        bar = self.frame[self.height_of_screen(y1 / 2160):self.height_of_screen(y2 / 2160),
              self.width_of_screen(x1 / 3840):self.width_of_screen(x2 / 3840)]
        # self.screenshot('check_is_pure_color_in_4k', frame=bar)
        if bar.size == 0:
            return False

        height, width, _ = bar.shape
        consecutive_matches = 0

        # Iterate through every horizontal line (row) in the bar
        for i in range(height):
            row_pixels = bar[i]  # Shape is (Width, 3)

            # Find unique colors and their counts for this specific row
            unique_colors, counts = np.unique(row_pixels, axis=0, return_counts=True)

            # Find the most frequent color in this row
            most_frequent_index = np.argmax(counts)
            dominant_count = counts[most_frequent_index]
            dominant_color = unique_colors[most_frequent_index]

            # Determine if this row is valid
            is_valid_row = True

            # 1. Check if the dominant color constitutes at least threshold %
            if (dominant_count / width) < threshold:
                is_valid_row = False

            # 2. If color_range is provided, ensure this row's dominant color fits the range
            if is_valid_row and color_range:
                b, g, r = dominant_color
                if not (color_range['r'][0] <= r <= color_range['r'][1]):
                    is_valid_row = False
                elif not (color_range['g'][0] <= g <= color_range['g'][1]):
                    is_valid_row = False
                elif not (color_range['b'][0] <= b <= color_range['b'][1]):
                    is_valid_row = False

            # Check consecutive streak
            if is_valid_row:
                consecutive_matches += 1
                if consecutive_matches >= 2:
                    return True
            else:
                consecutive_matches = 0

        return False


def has_rectangles(frame):
    """
    Detects if there is a bar structure in the frame by strictly looking for rectangular
    shapes/edges, regardless of color (White, Yellow, Dark Gray).

    Optimized for small/low-res UI elements (e.g. 120x8 pixels).
    """
    if frame is None:
        return False

    original_h, original_w = frame.shape[:2]

    # 1. UPSCALE THE IMAGE
    # Small UI elements (8px high) are too small for standard edge detection kernels.
    # We upscale by 4x to make borders distinct (1px border becomes 4px).
    scale_factor = 4
    resized = cv2.resize(frame, None, fx=scale_factor, fy=scale_factor, interpolation=cv2.INTER_CUBIC)

    # 2. PRE-PROCESSING
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)

    # 3. EDGE DETECTION (Canny)
    # Using Canny is safer than Thresholding here because "Dark Gray" bars
    # might have the same brightness as the background, but they usually have a BORDER.
    # Canny detects the border.
    # We use relatively low thresholds to catch faint borders of empty bars.
    edges = cv2.Canny(gray, 50, 100)

    # 4. MORPHOLOGICAL CLOSING
    # This connects the top and bottom borders of the bar into a single solid block.
    # Because we upscaled by 4, we can use a larger kernel to bridge gaps.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    closed_edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)

    # 5. FIND CONTOURS
    contours, _ = cv2.findContours(closed_edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # Calculate target minimum width (25% of the scaled width)
    min_width = (original_w * scale_factor) * 0.25

    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)

        # CHECK: Width
        if w < min_width:
            continue

        # CHECK: Aspect Ratio
        # A bar must be wider than it is tall.
        if w > h:
            # CHECK: Solidity/Noise
            # Ensure it's not a thin horizontal noise line.
            # Since we upscaled by 4, a real bar should be at least ~10px tall in the upscaled image
            # (which corresponds to 2.5px in the original).
            if h > 10:
                return True

    return False

lower_white_none_inclusive = np.array([222, 222, 222], dtype=np.uint8)
black = np.array([0, 0, 0], dtype=np.uint8)

def isolate_white_text_to_black(cv_image):
    """
    Converts pixels in the near-white range (244-255) to black,
    and all others to white.
    Args:
        cv_image: Input image (NumPy array, BGR).
    Returns:
        Black and white image (NumPy array), where matches are black.
    """
    match_mask = cv2.inRange(cv_image, black, lower_white_none_inclusive)
    output_image = cv2.cvtColor(match_mask, cv2.COLOR_GRAY2BGR)

    return output_image

yellow_skill_color = {
    'r': (230, 255),  # Red range
    'g': (180, 255),  # Green range
    'b': (0, 85)  # Blue range
}

white_skill_color = {
    'r': (190, 255),  # Red range
    'g': (190, 255),  # Green range
    'b': (190, 255)  # Blue range
}


