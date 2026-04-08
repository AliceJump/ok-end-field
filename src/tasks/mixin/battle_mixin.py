"""
BattleMixin

自动战斗相关逻辑模块。

主要功能：
- 战斗状态检测
- 技能释放
- 自动普通攻击
- 自动索敌与位移
- 战斗结束判断
- 技能条识别
- 自动战斗流程控制

依赖：
    AutoCombatLogic
    OpenCV (cv2)
    numpy
"""

import re
import time

import cv2
import numpy as np

from src.tasks.sequence_parser import parse_sequence
from src.tasks.AutoCombatLogic import AutoCombatLogic
from src.tasks.BaseEfTask import BaseEfTask


class BattleMixin(BaseEfTask):
    """
    自动战斗 Mixin。

    提供完整战斗能力：

    功能包括：
        - 战斗状态识别
        - 技能释放
        - 自动普通攻击
        - 自动闪避
        - 自动索敌
        - 战斗结束检测
    """

    def __init__(self, *args, **kwargs):
        """初始化战斗状态变量"""
        super().__init__(*args, **kwargs)

        self.last_no_number_action_time = 0
        self.last_skill_time = 0
        self.exit_check_count = 0
        self.last_op_time = 0
        self.config_description.update({
            "技能释放": (
                "「战技」释放角色顺序，比如123。\n"
                "建议只放3个技能。"
            ),
            "启动技能点数": (
                "当「技力条」达到该数值时，\n"
                "开始执行技能序列。取值范围1-3。"
            ),
            "无数字操作间隔": (
                "战斗中周期触发锁敌+向前闪避的最小间隔秒数。\n"
                "取值不小于6。"
            ),
            "启用排轴": (
                "是否启用排轴功能。\n"
                "启用后会根据「排轴序列」配置的顺序优先释放对应角色的技能，\n"
                "当排轴失败时回退到非排轴状态"
            ),
            "排轴序列": (
                "仅接受'1,2,3,4,ult_1,ult_2,ult_3,ult_4,e,sleep_[n]'这些值的逗号分隔字符串，\n"
                "代表技能释放优先级顺序\n"
                "例如'ult_2,1,e,ult_1'表示优先尝试干员2的终结技，再干员1的战技，\n"
                "再尝试连携，再干员1的终极技\n"
                "启用排轴功能后，系统会按照该配置的顺序尝试释放技能，\n"
                "一旦成功释放一个技能，就会等待下一个技能，而不是继续尝试后续技能\n"
                "这可以用于更精细地控制技能释放顺序，\n"
                "例如优先释放某个干员的技能来配合特定的战术需求。"
            ),
        })
        # 用于识别 LV 或等级文字
        self.lv_regex = re.compile(r"(?i)lv|\d{2}")

    def _parse_skill_sequence(self, raw_config: str) -> list[str]:
        """
        解析技能释放顺序，兼容两种格式：

        1️⃣ 老格式（纯数字）：
            "123" -> ["1","2","3"]

        2️⃣ 新格式（逗号分隔）：
            "ult_1,1,2,e,sleep_2,3"
        """

        if not raw_config:
            return ["1", "2", "3"]

        trimmed = raw_config.strip()

        # =========================
        # ✅ 新格式：逗号分隔（支持中文逗号）
        # =========================
        if "," in trimmed or "，" in trimmed:
            sequence = []
            tokens = parse_sequence(trimmed)

            valid_skills = {"1", "2", "3", "4", "e"}

            for token in tokens:
                if token in valid_skills:
                    sequence.append(token)

                elif token.startswith("ult_"):
                    if token[4:] in {"1", "2", "3", "4"}:
                        sequence.append(token)
                    else:
                        self.log_info(f"无效 ult 技能: {token}")

                elif token.startswith("sleep_"):
                    try:
                        float(token[6:])
                        sequence.append(token)
                    except ValueError:
                        self.log_info(f"无效 sleep 参数: {token}")

                else:
                    self.log_info(f"忽略无效技能: {token}")

            return sequence if sequence else ["1", "2", "3"]

        # =========================
        # ✅ 老格式：纯数字逐字符
        # =========================
        else:
            sequence = []
            valid_skills = {"1", "2", "3", "4"}

            for char in trimmed:
                if char in valid_skills:
                    sequence.append(char)

            return sequence if sequence else ["1", "2", "3"]

    def use_ult(self, ult_sequence: str = None):
        """
        尝试释放终极技。

        依次检测技能键：
            1 -> 2 -> 3 -> 4

        Returns:
            bool
                True  : 成功释放
                False : 未找到可释放技能
        """
        if ult_sequence is None:
            ults = ['1', '2', '3', '4']
        else:
            ults = [ult_sequence]

        for ult in ults:
            if self.find_one("ult_" + ult):
                self.send_key_down(ult)  # 确认使用send_key：终极技键位为游戏固定不可配置键，不经过KeyConfigManager管理

                # 等待技能释放导致战斗状态变化
                self.wait_until(lambda: not self.in_combat())

                self.send_key_up(ult)  # 确认使用send_key：终极技键位为游戏固定不可配置键，释放按键

                # 等待重新进入战斗
                self.sleep(4)

                self.last_op_time = time.time()

                return True

        return False

    def use_link_skill(self):
        """
        使用连携技能。
        """

        if self.find_one("default_link_skill", threshold=0.7):
            self.press_combat_key("e")
            self.last_op_time = time.time()
            return True

        return False

    def in_combat(self, required_yellow=0):
        """
        判断当前是否处于战斗中。

        条件：
            - 技能条数量 >= required_yellow
            - 在队伍状态
            - 非等级界面

        Returns:
            bool
        """

        return (
                self.get_skill_bar_count() >= required_yellow
                and self.in_team()
                and not self.ocr_lv()
        )

    def in_team(self):
        """
        判断当前是否处于队伍状态。
        """

        return all([
            self.find_one('skill_1') is not None,
            self.find_one('skill_2') is not None,
            self.find_one('skill_3') is not None,
            self.find_one('skill_4') is not None,
        ])

    def is_combat_ended(self):
        """
        检查战斗是否结束。

        需要 **连续两次检测成功** 才判定结束。
        """

        if self._check_single_exit_condition():
            self.exit_check_count += 1

            if self.exit_check_count >= 2:
                self.exit_check_count = 0
                return True
        else:
            self.exit_check_count = 0

        return False

    def _check_single_exit_condition(self):
        """
        单次战斗结束判定。
        """
        # UI状态检测
        has_lv = self.ocr_lv()
        in_team = self.in_team()

        if not (has_lv or not in_team):
            self.log_info(
                f"退出检查失败: UI状态不符 "
                f"(has_lv={has_lv}, in_team={in_team})"
            )
            return False

        self.log_info(
            f"退出检查通过:"
            f" has_lv={has_lv},"
            f" in_team={in_team},"
        )

        return True

    def _check_center_area_has_number(self):
        """
        检测屏幕中心是否存在伤害数字。
        """

        try:
            box = self.box_of_screen(0.20, 0.00, 0.80, 0.65)

            self.next_frame()

            center_area = self.ocr(
                match=r"^\d+$",
                box=box,
                name="center_number",
                log=True
            )

            if len(center_area) > 0:
                self.log_info(
                    f"中间区域识别到数字: {[r.name for r in center_area]}"
                )

            return len(center_area) > 0

        except (ValueError, AttributeError, TypeError) as e:
            self.log_error(f"OCR检测数字失败: {e}")
            return False

    def ocr_lv(self):
        """
        检测是否出现 LV 或等级 UI。
        """

        lv = self.ocr(
            0.02, 0.89, 0.23, 0.93,
            match=self.lv_regex,
            name='lv_text'
        )

        if len(lv) > 0:
            return True

        lv = self.ocr(
            0.02, 0.89, 0.23, 0.93,
            frame_processor=isolate_white_text_to_black,
            match=self.lv_regex,
            name='lv_text'
        )

        return len(lv) > 0

    def wait_in_combat(self, time_out=3, click=False):
        """
        等待进入战斗状态。
        """

        start = time.time()

        while time.time() - start < time_out:

            if self.in_combat():
                return True

            elif click:
                self.perform_attack_weave()
            else:
                self.sleep(0.003)

        return False

    def perform_attack_weave(self):
        """
        执行普通攻击（平A）。
        """

        attack_interval = 0.12

        if time.time() - getattr(self, 'last_op_time', 0) > attack_interval:
            self.click(move=False, key='left', down_time=0.005)

            self.last_op_time = time.time()

    def approach_enemy(self):
        """战斗中周期触发操作（无伤害数字）"""
        interval = self.config.get("无数字操作间隔", 6)
        interval = max(6.0, min(float(interval), 30.0))
        if time.time() - getattr(self, 'last_no_number_action_time', 0) < interval:
            return
        self.log_info("战斗中周期触发：执行索敌+向前闪避（贴近敌人）")
        self.click(key='middle', down_time=0.002)
        self.dodge_forward(pre_hold=0.05, dodge_down_time=0.03, after_sleep=0.02)
        self.last_no_number_action_time = time.time()
        self.last_op_time = time.time()

    def get_skill_bar_count(self):
        """
        获取当前技能条数量。

        Returns:
            int
                -1 表示未检测到
        """

        skill_area_box = self.box_of_screen_scaled(
            3840, 2160,
            1586, 1940,
            2266, 1983
        )

        skill_area = skill_area_box.crop_frame(self.frame)

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

            if self.check_is_pure_color_in_4k(
                    x1, y_start, x2, y_end,
                    yellow_skill_color
            ):
                count += 1
            else:
                break

        if count == 0:
            has_white_left = self.check_is_pure_color_in_4k(
                1604, y_start, 1614, y_end,
                white_skill_color,
                threshold=0.1
            )

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

    def auto_battle(self, no_battle: bool = False):
        """
        自动战斗主循环
        """

        start_time = time.time()
        last_battle_time = None
        sleep_time = self.config.get("进入战斗后的初始等待时间", 3)

        while True:

            # 全局超时保护
            if time.time() - start_time > 420:
                self.log_info("自动战斗超时")
                return False

            # 战斗结束判定
            if last_battle_time and time.time() - last_battle_time > 15:
                self.log_info("战斗完成")
                return True

            # 检测战斗
            battle_detected = AutoCombatLogic(self).run(start_sleep=sleep_time, no_battle=no_battle)

            if battle_detected:
                last_battle_time = time.time()
                sleep_time = 0.1
            else:
                self.sleep(0.1)


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
        if w > min_width and w > h > 10:
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
