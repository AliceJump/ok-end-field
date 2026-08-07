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

import cv2
import numpy as np
from ok import Box

from src.data.FeatureList import FeatureList as fL
from src.data.world_map import STAGE_CATEGORY_ENERGY_POOLING
from src.data.world_map_utils import is_world_map_text
from src.core.sequence_parser import parse_sequence
from src.tasks.onetime.AutoCombatLogic import AutoCombatLogic
from src.core.BaseEfTask import BaseEfTask
from src.core.BattleConfig import (
    BATTLE_CONFIG_DESCRIPTION,
    BATTLE_CONFIG_MODE_GLOBAL,
    BATTLE_CONFIG_MODE_INDEPENDENT,
    BATTLE_CONFIG_MODE_KEY,
    BATTLE_CONFIG_NAME,
    BATTLE_CONFIG_TYPE,
    BattleConfigManager,
    DEFAULT_BATTLE_CONFIG,
    KEY_COND_ENABLED,
    KEY_COND_SEQUENCE,
    KEY_INSTANT_LINK,
    KEY_INSTANT_ULT,
)
from src.core.global_config_store import get_global_config


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
        self.exit_check_count = 0
        self._battle_member_count = 0
        self.battle_config_manager = BattleConfigManager(get_global_config(BATTLE_CONFIG_NAME))
        self._register_battle_config()
        # 用于识别 LV 或等级文字
        self.lv_regex = re.compile(r"(?i)lv|\d{2}")

    def _register_battle_config(self):
        if not hasattr(self, "default_config") or self.default_config is None:
            self.default_config = {}
        if not hasattr(self, "config_description") or self.config_description is None:
            self.config_description = {}
        if not hasattr(self, "config_type") or self.config_type is None:
            self.config_type = {}
        battle_mode_type = {
            "type": "drop_down",
            "options": [BATTLE_CONFIG_MODE_GLOBAL, BATTLE_CONFIG_MODE_INDEPENDENT],
            "sub_configs": {
                BATTLE_CONFIG_MODE_GLOBAL: [],
                # 实时条件的 3 个内部数据 key（序列/立即释放开关）不单独展开为行——
                # 它们由「启用实时条件」面板承载（KEY_COND_ENABLED 渲染为面板行，随模式显隐）
                BATTLE_CONFIG_MODE_INDEPENDENT: [
                    key for key in DEFAULT_BATTLE_CONFIG
                    if key not in (KEY_COND_SEQUENCE, KEY_INSTANT_ULT, KEY_INSTANT_LINK)
                ],
            },
        }

        # 每个使用战斗能力的任务都可以选择自己的战斗参数来源。
        self.default_config.update({
            BATTLE_CONFIG_MODE_KEY: BATTLE_CONFIG_MODE_GLOBAL,
            **DEFAULT_BATTLE_CONFIG,
        })
        self.config_description.update(BATTLE_CONFIG_DESCRIPTION)
        self.config_description[BATTLE_CONFIG_MODE_KEY] = "选择当前任务使用全局或独立战斗配置。"
        self.config_type.update(BATTLE_CONFIG_TYPE)
        self.config_type[BATTLE_CONFIG_MODE_KEY] = battle_mode_type

    def get_battle_config(self, key: str, default=None):
        global_value = self.battle_config_manager.get(key, DEFAULT_BATTLE_CONFIG.get(key, default))
        raw_config_get = getattr(self, "_raw_cfg_get", None)
        if callable(raw_config_get):
            try:
                mode = raw_config_get(BATTLE_CONFIG_MODE_KEY, BATTLE_CONFIG_MODE_GLOBAL)
            except (TypeError, AttributeError):
                mode = self.config.get(BATTLE_CONFIG_MODE_KEY, BATTLE_CONFIG_MODE_GLOBAL)
        else:
            mode = self.config.get(BATTLE_CONFIG_MODE_KEY, BATTLE_CONFIG_MODE_GLOBAL)
        if mode != BATTLE_CONFIG_MODE_INDEPENDENT:
            return global_value
        if callable(raw_config_get):
            try:
                return raw_config_get(key, global_value)
            except (TypeError, AttributeError):
                pass
        return self.config.get(key, global_value)

    def get_account_config_base_value(self, key: str, default=None):
        return dict.get(self.config, key, default)

    def _parse_skill_sequence(self, raw_config) -> list[str]:
        """
        解析技能释放顺序（逗号分隔格式，用于排轴序列）：
            "ult_1,1,2,e,sleep_2,3"
        """
        if not raw_config:
            return ["1", "2", "3"]

        sequence = []
        valid_skills = {"1", "2", "3", "4", "e"}

        for token in parse_sequence(raw_config):
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
            elif token.startswith("normal_"):
                try:
                    val = float(token[7:])
                    if val <= 0:
                        raise ValueError
                    sequence.append(token)
                except ValueError:
                    self.log_info(f"无效 normal 持续时间: {token}")
            else:
                self.log_info(f"忽略无效技能: {token}")

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
            if self._find_battle_ult("ult_" + ult):
                self.send_key_down(ult)  # 确认使用send_key：终极技键位为游戏固定不可配置键，不经过KeyConfigManager管理
                # 等待技能释放导致战斗状态变化
                self.wait_until(lambda: not self.in_combat(), time_out=1)
                self.send_key_up(ult)  # 确认使用send_key：终极技键位为游戏固定不可配置键，释放按键
                self.wait_until(lambda: self.in_team(), time_out=3)
                return True

        return False

    def use_link_skill(self):
        """
        使用连携技能。
        """
        if self.find_one("default_link_skill", threshold=0.7, vertical_variance=0.005, horizontal_variance=0.005):
            self.press_combat_key("e")
            return True

        return False

    def in_combat(self, required_yellow=1):
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

        found_skills = 0
        sequence_valid = False
        skill_checks = []
        boxes = self._battle_feature_boxes("skill")
        for box_index, box in enumerate(boxes, start=1):
            result = self.find_one("skill_1", box=box)
            match_position = f"({result.x},{result.y})" if result is not None else "-"
            match_score = f"{result.confidence:.3f}" if result is not None else "-"
            skill_checks.append(
                f"skill_1->框{box_index}({box.x},{box.y},{box.width},{box.height}) "
                f"{'命中' if result is not None else '未命中'}@{match_position}, score={match_score}"
            )
            if result is None:
                continue

            if box_index == len(boxes):
                # skill_1 位于最后一个框时，表示单人队伍。
                found_skills = 1
                sequence_valid = True
                break

            matched_skills = 1
            for skill_offset in range(1, len(boxes) - box_index + 1):
                skill_number = skill_offset + 1
                next_box = boxes[box_index + skill_offset - 1]
                next_result = self.find_one(f"skill_{skill_number}", box=next_box)
                next_position = (
                    f"({next_result.x},{next_result.y})" if next_result is not None else "-"
                )
                next_score = f"{next_result.confidence:.3f}" if next_result is not None else "-"
                skill_checks.append(
                    f"skill_{skill_number}->框{box_index + skill_offset}"
                    f"({next_box.x},{next_box.y},{next_box.width},{next_box.height}) "
                    f"{'命中' if next_result is not None else '未命中'}@{next_position}, "
                    f"score={next_score}"
                )
                if next_result is not None:
                    matched_skills += 1
                    if matched_skills >= 2:
                        # 起始框决定队伍人数；第二个技能模板命中后即可确认队伍状态。
                        found_skills = len(boxes) - box_index + 1
                        sequence_valid = True
                        break

            if sequence_valid:
                break
        self._battle_member_count = found_skills
        self.log_debug(
            f"队伍人数检测: {found_skills} 人，"
            f"检查结果: {'; '.join(skill_checks)}"
        )
        return sequence_valid and found_skills >= 1

    def _battle_feature_boxes(self, prefix: str):
        """按模板初始位置的 x 坐标返回四个独立搜索框。"""
        initial_boxes = []
        for index in range(1, 5):
            try:
                initial_boxes.append(self.get_box_by_name(f"{prefix}_{index}"))
            except (ValueError, AttributeError):
                continue

        if not initial_boxes:
            return []

        max_width = max(box.width for box in initial_boxes)
        max_height = max(box.height for box in initial_boxes)
        for index in range(1, 5):
            try:
                template = self.get_feature_by_name(f"{prefix}_{index}")
            except (ValueError, AttributeError):
                continue
            if template is not None:
                max_width = max(max_width, template.width)
                max_height = max(max_height, template.height)

        if prefix == "skill":
            max_width = round(max_width * 1.25)

        boxes = []
        for initial_box in sorted(initial_boxes, key=lambda box: box.x):
            center_x = initial_box.x + initial_box.width // 2
            center_y = initial_box.y + initial_box.height // 2
            x = max(0, center_x - max_width // 2)
            y = max(0, center_y - max_height // 2)
            x = min(x, max(0, self.width - max_width))
            y = min(y, max(0, self.height - max_height))
            boxes.append(Box(x, y, width=max_width, height=max_height))
        return boxes

    def _find_battle_feature(self, feature: str):
        """在按 x 排序的独立模板框中查找技能，避免跨框误匹配。"""
        prefix, _, _ = feature.rpartition("_")
        if prefix not in ("skill", "ult"):
            return self.find_one(feature)
        for box in self._battle_feature_boxes(prefix):
            if result := self.find_one(feature, box=box):
                return result
        return None

    def _find_battle_ult(self, feature: str):
        """根据本次队伍人数，将终结技模板映射到实际技能框。"""
        boxes = self._battle_feature_boxes("ult")
        if len(boxes) != 4 or not self._battle_member_count:
            return self._find_battle_feature(feature)

        try:
            original_index = int(feature.rsplit("_", 1)[1])
        except (IndexError, ValueError):
            return self._find_battle_feature(feature)

        current_index = original_index + (4 - self._battle_member_count) - 1
        if current_index < 0 or current_index >= len(boxes):
            self.log_debug(
                f"终结技目标映射失败: {feature}, 队伍人数={self._battle_member_count}, "
                f"计算框索引={current_index + 1}"
            )
            return None
        self.log_debug(
            f"终结技目标映射: {feature}, 队伍人数={self._battle_member_count}, "
            f"检测第 {current_index + 1} 个框(x={boxes[current_index].x})"
        )
        result = self.find_one(feature, box=boxes[current_index])
        match_position = f"({result.x},{result.y})" if result is not None else "-"
        match_score = f"{result.confidence:.3f}" if result is not None else "-"
        self.log_debug(
            f"模板匹配结果: {feature}->框{current_index + 1}, "
            f"{'命中' if result is not None else '未命中'}@{match_position}, score={match_score}"
        )
        return result

    def is_combat_ended(self, exit_condition=None):
        """
        检查战斗是否结束。

        需要 **连续两次检测成功** 才判定结束。
        """

        if exit_condition is None:
            exit_condition = self._check_single_exit_condition()

        if exit_condition:
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
        # 结算模板优先检查：检测到 fL.b 结算模板同样判定战斗结束
        if self.find_feature(feature=fL.b):
            self.log_info("退出检查通过: 检测到结算模板 fL.b")
            return True

        # UI状态检测
        has_lv = self.ocr_lv()
        in_team = self.in_team()

        if not (has_lv or not in_team):
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

        start = self.active_time()

        while self.active_time() - start < time_out:

            if self.in_combat():
                return True

            elif click:
                self.perform_attack_weave()
            else:
                self.sleep(0.003)

        return False

    def approach_enemy(self):
        """战斗中周期触发操作（无伤害数字）"""
        interval = self.get_battle_config("无数字操作间隔", 6)
        interval = max(1.0, min(float(interval), 30.0))
        if self.active_time() - getattr(self, 'last_no_number_action_time', 0) < interval:
            return
        self.log_info("战斗中周期触发：执行索敌+向前闪避（贴近敌人）")
        self.click(key='middle', down_time=0.002)
        self.dodge_forward(pre_hold=0.05, dodge_down_time=0.03, after_sleep=0.02)
        self.last_no_number_action_time = self.active_time()

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
    
    def is_battle_settlement(self) -> bool:
        """
        判断是否进入战斗结算状态
        """

        return any((
            self.find_feature(feature=fL.b),
            self.find_feature(feature=fL.battle_space_ok),
            self.find_feature(feature=fL.battle_gather_ok),
            self.find_feature(feature=fL.restart_battle),
            self.find_feature(
                feature=fL.restart_battle,
                box=self.box_of_screen(0.550, 0.896, 0.574, 0.943)
            ),
        ))
    
    def auto_battle(self, no_battle: bool = False):
        """
        自动战斗主循环
        """

        start_time = self.active_time()
        deadline = start_time + 420
        last_battle_time = None
        sleep_time = self.get_battle_config("进入战斗后的初始等待时间", 3)

        while True:

            # 全局超时保护
            if self.active_time() >= deadline:
                self.log_info("自动战斗超时")
                return False

            # 内层退出后继续走战斗检测：结算模板立即结束，
            # 未检测到战斗时最多等待 15 秒。
            if last_battle_time is not None:
                battle_elapsed = self.active_time() - last_battle_time
                self.next_frame()

                if self.is_battle_settlement():
                    self.log_info("检测到战斗结算状态，战斗完成")
                    return True

                if battle_elapsed >= 15:
                    self.log_info("战斗结束状态等待超时，视为战斗已结束")
                    return True

            # 检测战斗
            battle_detected = AutoCombatLogic(self).run(
                start_sleep=sleep_time,
                no_battle=no_battle,
                deadline=deadline,
            )

            if battle_detected:
                last_battle_time = self.active_time()
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
