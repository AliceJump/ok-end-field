"""编队判定测试任务：固定区域裁剪战斗头像 + battle_icon 模板多尺度匹配，循环判定编队组成。"""
import cv2
from qfluentwidgets import FluentIcon

from src.core.BaseEfTask import BaseEfTask
from src.data.skill_allowlist import (
    _PORTRAIT_ROIS,
    _PORTRAIT_SIZE,
    _best_match_portrait,
    _load_battle_icons,
    _load_char_name_map,
)


class TeamCompositionDetectTask(BaseEfTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "编队判定测试"
        self.group_name = "工具与调试"
        self.description = "循环判定战斗界面左下角4个角色的编队组成（血条定位+contact模板多尺度匹配）"
        self.icon = FluentIcon.ROBOT
        self.visible = self.debug
        self.default_config = {
            "扫描间隔(秒)": 0.5,
            "最小匹配分数": 0.5,
        }
        self.config_description = {
            "扫描间隔(秒)": "每次判定之间的间隔时间（秒）",
            "最小匹配分数": "平均匹配分数低于该值的角色判定为未知",
        }
    def _detect_battle_portraits(self, frame):
        """按固定区域裁剪左下角4个头像"""
        height, width = frame.shape[:2]
        pw = int(_PORTRAIT_SIZE[0] * width)
        ph = int(_PORTRAIT_SIZE[1] * height)
        portraits = []
        for rx, ry in _PORTRAIT_ROIS:
            x1 = int(rx * width)
            y1 = int(ry * height)
            portraits.append(frame[y1:y1 + ph, x1:x1 + pw].copy())
        return portraits

    def _identify(self, portrait, templates):
        best_name = None
        best_score = float("-inf")
        for label, template_list in templates.items():
            score = _best_match_portrait(portrait, template_list)
            if score > best_score:
                best_score = score
                best_name = label
        if best_name:
            best_name = best_name.replace("battle_icon_", "")
        return best_name, max(best_score, 0.0)

    def run(self):
        interval = max(0.1, float(self.config.get("扫描间隔(秒)", 0.5) or 0.5))
        min_score = float(self.config.get("最小匹配分数", 0.5) or 0.5)
        contacts = _load_battle_icons()
        char_names = _load_char_name_map()
        if not contacts:
            self.log_info(self.tr("未加载到任何 battle_icon 模板，请检查 assets/coco_annotations.json"))
            return
        self.log_info(
            self.tr("编队判定启动: 共 {count} 个战斗头像模板").format(
                count=sum(len(template_list) for template_list in contacts.values())
            ),
            notify=True,
        )
        detect_count = 0
        try:
            while True:
                frame = self.next_frame()
                if frame is None:
                    self.sleep(interval)
                    continue
                portraits = self._detect_battle_portraits(frame)
                detect_count += 1
                team = []
                for p in portraits:
                    label, score = self._identify(p, contacts)
                    name = char_names.get(label, label)
                    if score < min_score:
                        team.append(self.tr("未知({score:.2f})").format(score=score))
                    else:
                        team.append(f"{name}({score:.2f})")
                team_text = " | ".join(team)
                self.log_info(self.tr("编队判定[{count}]: {team}").format(count=detect_count, team=team_text))
                self.sleep(interval)
        finally:
            self.log_info(self.tr("编队判定结束: 共判定 {count} 次").format(count=detect_count), notify=True)
