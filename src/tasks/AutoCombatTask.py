import re
import time
import cv2
import numpy as np
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
            "技能释放": "123",
            "启动技能点数": 2,
            "后台结束战斗通知": True
        })
        self.config_description.update({
            "技能释放": "满技能时, 开始释放技能, 如123, 建议只放3个技能",
            "启动技能点数": "当技能点达到该数值时，开始执行技能序列, 1-3",
        })
        self.lv_regex = re.compile(r"(?i)lv|\d{2}")
        self.last_op_time = 0
        self.last_skill_time = 0

    def run(self):
        if not self.in_combat(required_yellow=1):
            return

        raw_skill_config = self.config.get("技能释放", "123")
        start_trigger_count = self.config.get("启动技能点数", 2)
        skill_sequence = self._parse_skill_sequence(raw_skill_config)

        if self.debug:
            self.screenshot('enter_combat')

        self.click(key='middle')

        while True:
            # Check combat status and resource
            skill_count = self.get_skill_bar_count()

            # Exit condition
            if skill_count < 0 and (self.ocr_lv() or not self.in_team()):
                if self.debug:
                    self.screenshot('out_of_combat')
                self.log_info("自动战斗结束!", notify=self.config.get("后台结束战斗通知") and self.in_bg())
                break

            # High priority actions (E/Ult) always checked first
            if self.use_e_skill() or self.use_ult():
                continue

            # Logic: If we meet the start trigger, we execute the ENTIRE sequence
            if skill_count >= start_trigger_count:
                self.log_debug(f"Triggering sequence at {skill_count} points")

                # Execute the full sequence
                for skill_key in skill_sequence:
                    # Break loop if combat ends mid-sequence
                    if not self.in_combat():
                        break

                    # Wait for conditions: 1. Enough Points (1), 2. Skill Cooldown (1s)
                    # While waiting, we perform normal attacks (weave)
                    while True:
                        current_points = self.get_skill_bar_count()
                        time_since_last_skill = time.time() - self.last_skill_time

                        # Break condition: Have point AND cooldown ready
                        if current_points >= 1 and time_since_last_skill >= 1.0:
                            break

                        # High priority interrupts inside the wait loop
                        if self.use_e_skill() or self.use_ult():
                            continue

                        # If combat ended
                        if current_points < 0 and (self.ocr_lv() or not self.in_team()):
                            break

                        self.perform_attack_weave()
                        self.sleep(0.05)

                    # Double check combat didn't end during the wait loop
                    if not self.in_combat():
                        break

                    # Execute the skill
                    self.send_key(skill_key)
                    self.last_skill_time = time.time()
                    self.last_op_time = time.time()  # Update op time to prevent immediate click
                    self.log_debug(f"Used skill {skill_key}")

                self.log_debug("Sequence finished, returning to charge mode")

            else:
                # Charging phase: Just attack
                self.perform_attack_weave()

            self.sleep(0.05)

    def perform_attack_weave(self):
        """Performs a normal attack if the 0.3s operation interval permits."""
        if time.time() - self.last_op_time > 0.3:
            self.click(move=False)
            self.last_op_time = time.time()

    def _parse_skill_sequence(self, raw_config: str) -> list[str]:
        if not raw_config:
            return []
        trimmed_config = raw_config.strip()
        sequence = []
        valid_skills = {'1', '2', '3', '4'}
        for char in trimmed_config:
            if char in valid_skills:
                sequence.append(char)
        return sequence if sequence else ['1', '2', '3']

    def use_ult(self):
        ults = ['1', '2', '3', '4']
        for ult in ults:
            if self.find_one("ult_" + ult):
                self.send_key_down(ult)
                self.wait_until(lambda: not self.in_combat())
                self.send_key_up(ult)
                self.wait_in_combat(time_out=8)
                self.last_op_time = time.time()
                return True
        return False

    def wait_in_combat(self, time_out=3, click=False):
        start = time.time()
        while time.time() - start < time_out:
            if self.in_combat():
                return True
            elif click:
                self.perform_attack_weave()
            else:
                self.sleep(0.1)

    def ocr_lv(self):
        lv = self.ocr(0.02, 0.89, 0.23, 0.93, match=self.lv_regex, name='lv_text')
        if len(lv) > 0:
            return True
        lv = self.ocr(0.02, 0.89, 0.23, 0.93, frame_processor=isolate_white_text_to_black, match=self.lv_regex,
                      name='lv_text')
        return len(lv) > 0

    def use_e_skill(self):
        if self.find_one('skill_e', threshold=0.7):
            self.send_key('e')
            self.last_op_time = time.time()
            return True
        return False

    def in_combat(self, required_yellow=0):
        return self.get_skill_bar_count() >= required_yellow and self.in_team() and not self.ocr_lv()

    def in_team(self):
        one = self.find_one('skill_4')
        self.log_debug('oneconfidence {}'.format(one.confidence))
        return self.find_one('skill_1') and self.find_one('skill_2') and self.find_one('skill_3') and self.find_one(
            'skill_4')

    def get_skill_bar_count(self):
        skill_area_box = self.box_of_screen_scaled(3840, 2160, 1586, 1940, 2266, 1983)
        # self.draw_boxes('skill_area', skill_area_box, color='yellow', debug=True)
        # self.log_debug(f'skill_area_box {skill_area_box}')
        skill_area = skill_area_box.crop_frame(self.frame)
        # self.screenshot('skill_area', frame=skill_area)
        if not has_rectangles(skill_area):
            return -1

        count = 0
        y_start, y_end = 1958, 1970

        bars = [
            (1604, 1796),
            (1824, 2013),
            (2043, 2231)
        ]

        for x1, x2 in bars:
            if self.check_is_pure_color_in_4k(x1, y_start, x2, y_end, yellow_skill_color):
                count += 1
            else:
                break

        if count == 0:
            has_white_left = self.check_is_pure_color_in_4k(1604, y_start, 1614, y_end, white_skill_color,
                                                            threshold=0.1)
            if not has_white_left:
                count = -1
        return count

    def check_is_pure_color_in_4k(self, x1, y1, x2, y2, color_range=None, threshold=0.9):
        skill_area_box = self.box_of_screen_scaled(3840, 2160, x1, y1, x2, y2)
        bar = skill_area_box.crop_frame(self.frame)

        if bar.size == 0:
            return False

        height, width, _ = bar.shape
        consecutive_matches = 0

        for i in range(height):
            row_pixels = bar[i]
            unique_colors, counts = np.unique(row_pixels, axis=0, return_counts=True)
            most_frequent_index = np.argmax(counts)
            dominant_count = counts[most_frequent_index]
            dominant_color = unique_colors[most_frequent_index]

            is_valid_row = (dominant_count / width) >= threshold

            if is_valid_row and color_range:
                b, g, r = dominant_color
                if not (color_range['r'][0] <= r <= color_range['r'][1] and
                        color_range['g'][0] <= g <= color_range['g'][1] and
                        color_range['b'][0] <= b <= color_range['b'][1]):
                    is_valid_row = False

            if is_valid_row:
                consecutive_matches += 1
                if consecutive_matches >= 2:
                    return True
            else:
                consecutive_matches = 0

        return False


def has_rectangles(frame):
    if frame is None:
        return False

    original_h, original_w = frame.shape[:2]
    scale_factor = 4
    resized = cv2.resize(frame, None, fx=scale_factor, fy=scale_factor, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 100)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    closed_edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(closed_edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    min_width = (original_w * scale_factor) * 0.25

    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        if w > min_width and w > h and h > 10:
            return True

    return False


lower_white_none_inclusive = np.array([222, 222, 222], dtype=np.uint8)
black = np.array([0, 0, 0], dtype=np.uint8)


def isolate_white_text_to_black(cv_image):
    match_mask = cv2.inRange(cv_image, black, lower_white_none_inclusive)
    output_image = cv2.cvtColor(match_mask, cv2.COLOR_GRAY2BGR)
    return output_image


yellow_skill_color = {
    'r': (230, 255),
    'g': (180, 255),
    'b': (0, 85)
}

white_skill_color = {
    'r': (190, 255),
    'g': (190, 255),
    'b': (190, 255)
}
