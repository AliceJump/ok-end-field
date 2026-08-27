"""编队判定测试任务：固定区域裁剪战斗头像 + battle_icon 模板多尺度匹配，循环判定编队组成。"""
import json
from pathlib import Path

import cv2
import numpy as np
from qfluentwidgets import FluentIcon

from src.core.BaseEfTask import BaseEfTask

OK_TEMPLATES = Path(__file__).resolve().parent.parent.parent.parent / "ok_templates"
COCO_JSON = OK_TEMPLATES / "coco_annotations.json"
CHARACTERS_JSON = Path(__file__).resolve().parent.parent.parent.parent / "assets" / "data" / "characters.json"

# 左下角4个头像固定区域（归一化坐标，基于1920x1080）
PORTRAIT_ROIS = [
    (40 / 1920, 927 / 1080),
    (156 / 1920, 927 / 1080),
    (273 / 1920, 927 / 1080),
    (390 / 1920, 927 / 1080),
]
PORTRAIT_SIZE = (54 / 1920, 46 / 1080)
MATCH_SCALES = [0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2]


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
        self._contacts = None
        self._char_names = {}

    def _load_char_names(self):
        if self._char_names:
            return self._char_names
        with open(CHARACTERS_JSON, encoding="utf-8") as f:
            data = json.load(f)
        for info in data.values():
            en = info.get("en")
            zh = info.get("zh")
            if en and zh:
                self._char_names[en] = zh
        return self._char_names

    def _load_contacts(self):
        """从 coco_annotations.json 加载 battle_icon 模板（同风格圆形战斗头像，缓存）"""
        if self._contacts is not None:
            return self._contacts
        with open(COCO_JSON, encoding="utf-8") as f:
            data = json.load(f)
        cat_map = {c["id"]: c["name"] for c in data["categories"]}
        img_map = {i["id"]: i for i in data["images"]}
        contacts = {}
        for ann in data["annotations"]:
            name = cat_map.get(ann["category_id"], "")
            if not name.endswith("_battle_icon"):
                continue
            img = img_map.get(ann["image_id"])
            if img is None:
                continue
            path = OK_TEMPLATES / img["file_name"]
            if not path.exists():
                continue
            shot = cv2.imread(str(path))
            if shot is None:
                continue
            x, y, w, h = [int(v) for v in ann["bbox"]]
            contacts[name] = shot[y:y + h, x:x + w].copy()
        self._contacts = contacts
        return contacts

    def _detect_battle_portraits(self, frame):
        """按固定区域裁剪左下角4个头像"""
        height, width = frame.shape[:2]
        pw = int(PORTRAIT_SIZE[0] * width)
        ph = int(PORTRAIT_SIZE[1] * height)
        portraits = []
        for rx, ry in PORTRAIT_ROIS:
            x1 = int(rx * width)
            y1 = int(ry * height)
            portraits.append(frame[y1:y1 + ph, x1:x1 + pw].copy())
        return portraits

    @staticmethod
    def _hist_sim(img1, img2):
        h1 = cv2.calcHist([cv2.cvtColor(img1, cv2.COLOR_BGR2HSV)], [0, 1], None, [30, 32], [0, 180, 0, 256])
        h2 = cv2.calcHist([cv2.cvtColor(img2, cv2.COLOR_BGR2HSV)], [0, 1], None, [30, 32], [0, 180, 0, 256])
        cv2.normalize(h1, h1)
        cv2.normalize(h2, h2)
        return cv2.compareHist(h1, h2, cv2.HISTCMP_CORREL)

    @staticmethod
    def _phash(img, size=16):
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (size, size))
        return small > small.mean()

    def _match_portrait(self, portrait, template):
        t_h, t_w = template.shape[:2]
        p_h, p_w = portrait.shape[:2]
        t_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        p_gray = cv2.cvtColor(portrait, cv2.COLOR_BGR2GRAY)
        best = -1.0
        best_scale = 1.0
        best_pos = None
        for scale in MATCH_SCALES:
            tw = max(10, int(p_w * scale))
            th = max(10, int(p_h * scale))
            if tw > t_w or th > t_h:
                continue
            tmpl = cv2.resize(p_gray, (tw, th))
            result = cv2.matchTemplate(t_gray, tmpl, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            if max_val > best:
                best = max_val
                best_scale = scale
                best_pos = max_loc
        hist = 0.0
        phash_sim = 0.0
        if best_pos is not None:
            tw = int(p_w * best_scale)
            th = int(p_h * best_scale)
            region = template[best_pos[1]:best_pos[1] + th, best_pos[0]:best_pos[0] + tw]
            region = cv2.resize(region, (p_w, p_h))
            hist = self._hist_sim(portrait, region)
            p1 = self._phash(portrait)
            p2 = self._phash(region)
            phash_sim = 1.0 - np.count_nonzero(p1 != p2) / p1.size
        return (best + hist + phash_sim) / 3

    def _identify(self, portrait, templates, names):
        best_name = None
        best_score = 0.0
        for label, template in templates.items():
            score = self._match_portrait(portrait, template)
            if score > best_score:
                best_score = score
                best_name = label
        if best_name:
            best_name = best_name.replace("_battle_icon", "")
        return best_name, best_score

    def run(self):
        interval = max(0.1, float(self.config.get("扫描间隔(秒)", 0.5) or 0.5))
        min_score = float(self.config.get("最小匹配分数", 0.5) or 0.5)
        contacts = self._load_contacts()
        char_names = self._load_char_names()
        if not contacts:
            self.log_info(self.tr("未加载到任何 battle_icon 模板，请检查 ok_templates/coco_annotations.json"))
            return
        self.log_info(self.tr("编队判定启动: 共 {count} 个战斗头像模板").format(count=len(contacts)), notify=True)
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
                for i, p in enumerate(portraits):
                    label, score = self._identify(p, contacts, char_names)
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