"""批量添加好友（陌生人推荐）。

移植自 `MaaEnd/MaaEnd <https://github.com/MaaEnd/MaaEnd>`_（AGPL-3.0）：

- 流程：``assets/resource/pipeline/BatchAddFriends.json`` 陌生人分支；
- 场景导航：``assets/resource/pipeline/SceneManager/SceneMenu.json``；
- 模板：``assets/resource/image/BatchAddFriends/``、``assets/resource/image/SceneManager/``。

识别方式与其保持一致，未自造资源：

1. 大世界左上角菜单按钮做模板匹配，**Alt + 左键**打开菜单（MaaEnd ``AutoAltClickAction``）；
2. 菜单列表里 OCR「好友」并点击；
3. 好友菜单顶部模板匹配「添加好友」入口并点击；
4. 陌生人列表右侧 OCR 定位可点的「添加好友」按钮，点一下发一个申请；
5. 一屏没有按钮就下滑，滑到底再点「换一批」；
6. 每次点击后检查「好友列表已满」，满员提前结束。

分辨率
------
所有 ROI 与手势坐标都按 **1280x720** 标定（MaaEnd 原值），因此本任务**仅支持 720P**：
其它分辨率会在开始前提示并直接退出，不做任何点击，避免坐标错位误点。
"""

import os
import re

import cv2
from qfluentwidgets import FluentIcon

from src.tasks.mixin.common import Common

#: ROI 标定基准；本任务只接受这个分辨率
BASE_WIDTH = 1280
BASE_HEIGHT = 720

# ---- MaaEnd 原始 ROI（1280x720 基准）----
#: 大世界左上角菜单按钮
WORLD_MENU_ROI = (0, 0, 200, 200)
#: 菜单总列表（左侧全高），里面有「好友」项
MENU_LIST_ROI = (0, 0, 400, 720)
#: 好友菜单顶部，含「添加好友」入口图标
ADD_ENTRY_ROI = (620, 0, 200, 120)
#: 陌生人列表右侧区域，每个条目「添加好友」按钮所在列
LIST_ROI = (1122, 102, 157, 570)
#: 「换一批」按钮
CHANGE_BATCH_ROI = (1151, 648, 55, 26)
#: 「好友列表已满」提示条
FRIEND_FULL_ROI = (481, 108, 304, 29)
#: 添加好友页左上角标题
ADD_PAGE_TITLE_ROI = (0, 0, 200, 60)

#: 模板根目录（取自 MaaEnd）
TEMPLATE_ROOT = os.path.join("assets", "templates")
#: 大世界菜单按钮模板
WORLD_MENU_TEMPLATES = ("scene_manager/WorldMenu.png",)
#: 好友菜单顶部「添加好友」入口模板
ADD_ENTRY_TEMPLATES = (
    "batch_add_friends/addFriend.png",
    "batch_add_friends/addFriend2.png",
)
#: 模板匹配阈值（MaaEnd 原值 0.8）
TEMPLATE_THRESHOLD = 0.8

FRIEND_ITEM_TEXT = re.compile(r"好友|(?i:friends)|フレンド|친구")
ADD_BUTTON_TEXT = re.compile(r"添加好友|新增好友|(?i:add\s*friend)|フレンド追加")
CHANGE_BATCH_TEXT = re.compile(r"换一批|換一批|(?i:refresh\s*list)|リスト更新")
FRIEND_FULL_TEXT = re.compile(r"好友列表已满|好友列表已滿")


class BatchAddFriendsTask(Common):
    """陌生人推荐批量发送好友申请。仅支持 720P。"""

    CFG_MAX_COUNT = "最大添加数量"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "批量添加好友"
        self.icon = FluentIcon.PEOPLE
        self.group_name = "日常任务"
        self.group_icon = FluentIcon.CALENDAR
        self.description = (
            "自动批量发送好友申请（陌生人推荐）。注意：过于频繁的添加好友可能会造成游戏风控。仅支持 720P 分辨率。"
        )
        self.default_config.update({self.CFG_MAX_COUNT: 20})
        self.config_description.update({self.CFG_MAX_COUNT: "达到数量后自动停止。"})
        self._template_cache = {}

    def run(self):
        if not self.ensure_720p():
            return
        if not self.enter_friend_add_page():
            self.log_error(self.tr("未能进入「好友 → 添加好友」页面，已停止"))
            return
        self.add_strangers()

    # ---------- 前置检查 ----------

    def ensure_720p(self):
        """ROI 全部按 1280x720 标定，其它分辨率不执行，避免误点。"""
        width, height = self.screen_width, self.screen_height
        if (width, height) == (BASE_WIDTH, BASE_HEIGHT):
            return True
        self.log_error(
            self.tr("批量添加好友仅支持 720P（1280x720），当前分辨率 {width}x{height}").format(
                width=width, height=height
            )
        )
        return False

    # ---------- 场景导航 ----------

    def enter_friend_add_page(self):
        """从当前界面一路走到「好友 → 添加好友」，最多尝试四轮。"""
        for _ in range(4):
            if self.on_add_page():
                return True
            # 已在好友菜单：点顶部「添加好友」入口
            entry = self.find_template(ADD_ENTRY_ROI, ADD_ENTRY_TEMPLATES)
            if entry is not None:
                self.log_info(self.tr("点击「添加好友」入口"))
                self.click(entry[0], entry[1])
                self.sleep(1.2)
                continue
            # 已在菜单总列表：点「好友」
            friend = self.find_friend_menu_item()
            if friend is not None:
                self.log_info(self.tr("点击菜单里的「好友」"))
                self.click_box(friend, after_sleep=1.2)
                continue
            # 在大世界：Alt+左键打开菜单
            if not self.open_world_menu():
                break
        return self.on_add_page()

    def open_world_menu(self):
        """大世界左上角菜单按钮：Alt + 左键打开菜单。"""
        position = self.find_template(WORLD_MENU_ROI, WORLD_MENU_TEMPLATES)
        if position is None:
            return False
        self.send_key_down("alt")
        try:
            self.click(position[0], position[1])
        finally:
            # Alt 必须抬起，否则后续按键全变成 Alt 组合键
            self.send_key_up("alt")
        self.sleep(1.5)
        return True

    def on_add_page(self):
        return bool(self.ocr(box=self._roi(ADD_PAGE_TITLE_ROI), match=ADD_BUTTON_TEXT, threshold=0.7))

    def find_friend_menu_item(self):
        results = self.ocr(box=self._roi(MENU_LIST_ROI), match=FRIEND_ITEM_TEXT, threshold=0.7)
        return results[0] if results else None

    # ---------- 陌生人批量添加 ----------

    def add_strangers(self):
        max_count = int(self.config.get(self.CFG_MAX_COUNT, 20))
        added = 0
        scrolled = False
        while added < max_count and not self.exit_is_set():
            button = self.find_add_button()
            if button is not None:
                self.click_box(button, after_sleep=0.3)
                added += 1
                scrolled = False
                self.log_info(self.tr("已发送好友申请 {added}/{max}").format(added=added, max=max_count))
                if self.friend_list_full():
                    self.log_info(self.tr("好友列表已满，提前结束"))
                    break
                continue
            if not scrolled:
                # 当前屏没有，再往下滚一屏；仍没有才认为到底
                scrolled = True
                self.scroll_list()
                continue
            if self.change_batch():
                scrolled = False
                self.sleep(1.5)
                continue
            self.log_info(self.tr("陌生人列表里没有更多可添加的好友"))
            break
        if added == 0:
            self.log_info(self.tr("没有发送任何好友申请"))
        else:
            self.log_info(self.tr("批量添加好友结束，共发送 {count} 个好友申请").format(count=added))

    # ---------- 识别与操作 ----------

    def find_add_button(self):
        """在右侧列表区域找「添加好友」按钮；无结果返回 None。"""
        results = self.ocr(box=self._roi(LIST_ROI), match=ADD_BUTTON_TEXT, threshold=0.7)
        return results[0] if results else None

    def friend_list_full(self):
        return bool(self.ocr(box=self._roi(FRIEND_FULL_ROI), match=FRIEND_FULL_TEXT, threshold=0.7))

    def change_batch(self):
        results = self.ocr(box=self._roi(CHANGE_BATCH_ROI), match=CHANGE_BATCH_TEXT, threshold=0.7)
        if not results:
            return False
        self.log_info(self.tr("点击「换一批」"))
        self.click_box(results[0], after_sleep=0)
        return True

    def scroll_list(self):
        """滚动陌生人列表：鼠标滚轮比拖拽可靠（维护组推荐用法）。"""
        self.scroll_relative(0.5, 0.5, -1)
        self.sleep(0.8)

    def find_template(self, roi, relative_paths):
        """在 roi 内做模板匹配，返回命中中心点或 None。"""
        box = self._roi(roi)
        frame = self.frame
        if frame is None:
            return None
        crop = frame[box.y : box.y + box.height, box.x : box.x + box.width]
        if crop.size == 0:
            return None
        best = None
        for relative_path in relative_paths:
            template = self.load_template(relative_path)
            if template is None:
                continue
            height, width = template.shape[:2]
            if height > crop.shape[0] or width > crop.shape[1]:
                continue
            result = cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED)
            _, score, _, location = cv2.minMaxLoc(result)
            if score >= TEMPLATE_THRESHOLD and (best is None or score > best[0]):
                best = (score, box.x + location[0] + width // 2, box.y + location[1] + height // 2)
        if best is None:
            return None
        return best[1], best[2]

    def load_template(self, relative_path):
        if relative_path not in self._template_cache:
            self._template_cache[relative_path] = cv2.imread(os.path.join(TEMPLATE_ROOT, relative_path))
        return self._template_cache[relative_path]

    # ---------- 坐标换算 ----------

    def _roi(self, roi):
        x, y, width, height = roi
        return self.box_of_screen_scaled(BASE_WIDTH, BASE_HEIGHT, x, y, width_original=width, height_original=height)
