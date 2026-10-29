"""批量添加好友（陌生人推荐）。

移植自 `MaaEnd/MaaEnd <https://github.com/MaaEnd/MaaEnd>`_（AGPL-3.0）：

- 流程来自 ``assets/resource/pipeline/BatchAddFriends.json`` 的陌生人分支；
- 入口模板 ``addFriend.png`` / ``addFriend2.png`` 取自
  ``assets/resource/image/BatchAddFriends/``（同为 AGPL-3.0）。

识别方式与 MaaEnd 一致，不额外造模板：

- 在好友菜单顶部用模板匹配点「添加好友」入口；
- 用 OCR 判据定位列表右侧可点的「添加好友」按钮；
- 列表滑到底后点「换一批」；
- 点完检查「好友列表已满」，满员提前结束。

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
#: 列表下滑手势
SWIPE_FROM = (640, 560)
SWIPE_TO = (640, 220)

#: 入口图标模板（取自 MaaEnd）
TEMPLATE_DIR = os.path.join("assets", "templates", "batch_add_friends")
TEMPLATE_NAMES = ("addFriend.png", "addFriend2.png")
#: 入口模板匹配阈值（MaaEnd 原值 0.8）
ENTRY_THRESHOLD = 0.8

ADD_BUTTON_TEXT = re.compile(r"添加好友|新增好友|Add\s*Friend|フレンド追加", re.IGNORECASE)
CHANGE_BATCH_TEXT = re.compile(r"换一批|換一批|Refresh\s*List|リスト更新", re.IGNORECASE)
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
        self._entry_templates = None

    def run(self):
        if not self.ensure_720p():
            return
        if not self.ensure_add_friend_page():
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

    def ensure_add_friend_page(self):
        if self.on_add_page():
            return True
        entry = self.find_add_entry()
        if entry is not None:
            self.click(entry[0], entry[1])
            self.sleep(1)
            if self.on_add_page():
                return True
        self.log_error(self.tr("未找到「添加好友」入口：请先打开「好友」页面，再运行本任务"))
        return False

    def on_add_page(self):
        return bool(self.ocr(box=self._roi(ADD_PAGE_TITLE_ROI), match=ADD_BUTTON_TEXT, threshold=0.7))

    # ---------- 陌生人批量添加 ----------

    def add_strangers(self):
        max_count = int(self.config.get(self.CFG_MAX_COUNT, 20))
        added = 0
        swiped = False
        while added < max_count and not self.exit_is_set():
            button = self.find_add_button()
            if button is not None:
                self.click_box(button, after_sleep=0.3)
                added += 1
                swiped = False
                self.log_info(self.tr("已发送好友申请 {added}/{max}").format(added=added, max=max_count))
                if self.friend_list_full():
                    self.log_info(self.tr("好友列表已满，提前结束"))
                    break
                continue
            if not swiped:
                # 当前屏没有再往下看一屏，仍没有才认为到底
                swiped = True
                self.swipe_list()
                continue
            if self.change_batch():
                swiped = False
                self.sleep(1)
                continue
            self.log_info(self.tr("陌生人列表里没有更多可添加的好友"))
            break
        if added == 0:
            self.log_info(self.tr("没有发送任何好友申请"))
        else:
            self.log_info(self.tr("批量添加好友结束，共发送 {count} 个好友申请").format(count=added))

    # ---------- 识别与操作 ----------

    def find_add_entry(self):
        """在好友菜单顶部用 MaaEnd 模板找「添加好友」入口，返回点击坐标或 None。"""
        templates = self.entry_templates()
        if not templates:
            return None
        box = self._roi(ADD_ENTRY_ROI)
        frame = self.frame
        if frame is None:
            return None
        crop = frame[box.y : box.y + box.height, box.x : box.x + box.width]
        if crop.size == 0:
            return None
        best = None
        for template in templates:
            height, width = template.shape[:2]
            if height > crop.shape[0] or width > crop.shape[1]:
                continue
            result = cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED)
            _, score, _, location = cv2.minMaxLoc(result)
            if score >= ENTRY_THRESHOLD and (best is None or score > best[0]):
                best = (score, box.x + location[0] + width // 2, box.y + location[1] + height // 2)
        if best is None:
            return None
        return best[1], best[2]

    def entry_templates(self):
        if self._entry_templates is None:
            self._entry_templates = []
            for name in TEMPLATE_NAMES:
                image = cv2.imread(os.path.join(TEMPLATE_DIR, name))
                if image is not None:
                    self._entry_templates.append(image)
        return self._entry_templates

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
        self.click_box(results[0], after_sleep=0)
        return True

    def swipe_list(self):
        start = self._point(*SWIPE_FROM)
        end = self._point(*SWIPE_TO)
        self.swipe(start[0], start[1], end[0], end[1], duration=0.5, after_sleep=1)

    # ---------- 坐标换算 ----------

    def _roi(self, roi):
        x, y, width, height = roi
        return self.box_of_screen_scaled(BASE_WIDTH, BASE_HEIGHT, x, y, width_original=width, height_original=height)

    def _point(self, x, y):
        box = self.box_of_screen_scaled(BASE_WIDTH, BASE_HEIGHT, x, y)
        return box.x, box.y
