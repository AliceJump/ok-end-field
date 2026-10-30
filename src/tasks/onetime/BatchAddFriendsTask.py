"""批量添加好友（陌生人推荐）。

流程与 MaaEnd 的 ``BatchAddFriends`` 陌生人分支一致：

1. 大世界左上角菜单按钮做模板匹配，**Alt + 左键**打开菜单；
2. 菜单总列表里 OCR「好友」并点击；
3. 好友菜单顶部模板匹配「添加好友」入口并点击；
4. 陌生人列表右侧 OCR 定位可点的「添加好友」按钮，点一下发一个申请；
5. 一屏没有按钮就继续下滑，滑到列表底部再点「换一批」；
6. 每轮开始与每次点击后都检查「好友列表已满」；满员导致的失败申请不计入已发送。

坐标
----
所有 ROI 都按 **1280x720** 标定，运行时按 ``screen_width / 1280`` 等比换算 ROI，
模板也按同样比例缩放，因此在项目支持的 16:9 分辨率（1080P/2K 等）下同样可用。
"""

import os
import re

import cv2
from qfluentwidgets import FluentIcon

from src.tasks.mixin.common import Common

#: ROI 与模板的标定基准分辨率
BASE_WIDTH = 1280
BASE_HEIGHT = 720

# ---- MaaEnd 原始 ROI（1280x720 基准，负值表示从右/下边缘算）----
#: 大世界菜单按钮（MaaEnd 原值 [-200,0,200,200]，右锚 → 1080）
WORLD_MENU_ROI = (1080, 0, 200, 200)
#: 菜单总列表（MaaEnd 原值 [-400,0,400,720]，右锚 → 880），里面有「好友」项
MENU_LIST_ROI = (880, 0, 400, 720)
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
#: 陌生人列表区域，用于判断是否已经滑到底
LIST_REGION_ROI = (46, 114, 1052, 498)

#: 模板根目录
TEMPLATE_ROOT = os.path.join("assets", "templates")
#: 大世界菜单按钮模板
WORLD_MENU_TEMPLATES = ("scene_manager/WorldMenu.png",)
#: 好友菜单顶部「添加好友」入口模板
ADD_ENTRY_TEMPLATES = (
    "batch_add_friends/addFriend.png",
    "batch_add_friends/addFriend2.png",
)
#: 模板匹配阈值
TEMPLATE_THRESHOLD = 0.8
#: 单批内最多下滑几次；到底后不再滑
MAX_SCROLLS_PER_BATCH = 6
#: 连续多少批一个都没加上就认为没有更多可添加的好友
MAX_EMPTY_BATCHES = 3
#: 列表到底的静帧相似度阈值
LIST_COMPLETE_THRESHOLD = 0.98

FRIEND_ITEM_TEXT = re.compile(r"好友|(?i:friends)|フレンド|친구")
ADD_BUTTON_TEXT = re.compile(r"添加好友|新增好友|(?i:add\s*friend)|フレンド追加")
CHANGE_BATCH_TEXT = re.compile(r"换一批|換一批|(?i:refresh\s*list)|リスト更新")
FRIEND_FULL_TEXT = re.compile(r"好友列表已满|好友列表已滿")


class BatchAddFriendsTask(Common):
    """陌生人推荐批量发送好友申请。"""

    CFG_MAX_COUNT = "最大添加数量"
    CFG_BATCH_LIMIT = "换一批前最大添加数"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "批量添加好友"
        self.icon = FluentIcon.PEOPLE
        self.group_name = "日常任务"
        self.group_icon = FluentIcon.CALENDAR
        self.description = "自动批量发送好友申请（陌生人推荐）。注意：过于频繁的添加好友可能会造成游戏风控。"
        self.default_config.update({self.CFG_MAX_COUNT: 20, self.CFG_BATCH_LIMIT: 10})
        self.config_type[self.CFG_BATCH_LIMIT] = {"min": 0, "max": 200}
        self.config_description.update(
            {
                self.CFG_MAX_COUNT: "达到数量后自动停止。",
                self.CFG_BATCH_LIMIT: (
                    "同一批陌生人最多添加多少个就点「换一批」，默认 10。\n0 表示不加限制，把当前这一批能加的都加完。"
                ),
            }
        )
        self._template_cache = {}
        self._list_snapshot = None

    def run(self):
        self._template_cache = {}
        self._list_snapshot = None
        self.log_info(f"当前分辨率 {self.screen_width}x{self.screen_height}，好友坐标按 1280x720 等比换算")
        # 游戏可能还停在加载、剧情、弹窗或别的菜单上，先回大世界再导航。
        self.ensure_main(time_out=180)
        if not self.enter_friend_add_page():
            self.log_error(self.tr("未能进入「好友 → 添加好友」页面，已停止"))
            return
        self.add_strangers()

    # ---------- 场景导航 ----------

    def enter_friend_add_page(self):
        """从当前界面一路走到「好友 → 添加好友」。

        界面可能还在切换（加载、剧情、弹窗），单轮什么都匹配不到时不要立刻放弃：
        先等画面稳定，再尝试退回大世界重走一遍。
        """
        for attempt in range(6):
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
            if self.open_world_menu():
                continue
            # 三个候选都没匹配到：界面可能还没就绪，或卡在别的弹窗里
            if attempt >= 2:
                self.log_info(self.tr("未识别到好友入口，先返回大世界再重试"))
                self.ensure_main(time_out=60)
            else:
                self.log_info(self.tr("未识别到好友入口，等待界面稳定后重试"))
                self.wait_ui_stable(stable_time=0.5, max_wait=3, refresh_interval=0.3)
                self.sleep(0.8)
        return self.on_add_page()

    def open_world_menu(self):
        """大世界菜单按钮：Alt + 左键打开菜单。"""
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
        batch_limit = int(self.config.get(self.CFG_BATCH_LIMIT, 10) or 0)
        added = 0
        added_in_batch = 0
        empty_batches = 0
        scrolls = 0
        while added < max_count and not self.exit_is_set():
            # 列表已满时再点也不会发出申请：先在循环开头确认，避免把失败算成已发送
            if self.friend_list_full():
                self.log_info(self.tr("好友列表已满，提前结束"))
                break
            # 同一批加到上限就先换一批，避免把一批陌生人加穿导致风控
            if batch_limit and added_in_batch >= batch_limit and self.change_batch():
                added_in_batch = 0
                scrolls = 0
                self.sleep(1.5)
                continue
            button = self.find_add_button()
            if button is not None:
                self.click_box(button, after_sleep=0.3)
                # 点击后出现满员提示说明这次申请没有发出去，不能计入已发送
                if self.friend_list_full():
                    self.log_info(self.tr("好友列表已满，本次申请未发出，提前结束"))
                    break
                added += 1
                added_in_batch += 1
                scrolls = 0
                empty_batches = 0
                self.log_info(self.tr("已发送好友申请 {added}/{max}").format(added=added, max=max_count))
                continue
            # 当前屏没有按钮：继续下滑，滑到底或判定到底后再换一批
            if scrolls < MAX_SCROLLS_PER_BATCH and not self.list_at_bottom():
                scrolls += 1
                self.scroll_list()
                continue
            if not self.change_batch():
                self.log_info(self.tr("陌生人列表里没有更多可添加的好友"))
                break
            # 连续几批都一个没加上，说明确实没人可加了
            empty_batches = empty_batches + 1 if added_in_batch == 0 else 0
            if empty_batches >= MAX_EMPTY_BATCHES:
                self.log_info(self.tr("连续 {count} 批都没有可添加的好友，提前结束").format(count=empty_batches))
                break
            added_in_batch = 0
            scrolls = 0
            self.sleep(1.5)
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
        """滚动陌生人列表：鼠标滚轮比拖拽可靠。"""
        self.scroll_relative(0.5, 0.5, -3)
        self.sleep(0.6)
        self.wait_ui_stable(stable_time=0.4, max_wait=2, refresh_interval=0.3)

    def list_at_bottom(self):
        """与上一次快照比较列表区域，静止即认为到底。"""
        frame = self.frame
        if frame is None:
            return False
        box = self._roi(LIST_REGION_ROI)
        crop = frame[box.y : box.y + box.height, box.x : box.x + box.width]
        if crop.size == 0:
            return False
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        previous = self._list_snapshot
        self._list_snapshot = gray
        if previous is None or previous.shape != gray.shape:
            return False
        score = float(cv2.matchTemplate(gray, previous, cv2.TM_CCOEFF_NORMED)[0][0])
        return score >= LIST_COMPLETE_THRESHOLD

    # ---------- 模板匹配 ----------

    def find_template(self, roi, relative_paths):
        """在 roi 内做模板匹配，返回命中中心点或 None。"""
        box = self._roi(roi)
        frame = self.frame
        if frame is None:
            return None
        x0, y0 = max(box.x, 0), max(box.y, 0)
        x1 = min(box.x + box.width, frame.shape[1])
        y1 = min(box.y + box.height, frame.shape[0])
        if x1 <= x0 or y1 <= y0:
            return None
        crop = frame[y0:y1, x0:x1]
        best = None
        for relative_path in relative_paths:
            template = self.load_template(relative_path)
            if template is None:
                continue
            template = self.rescale_template(template)
            height, width = template.shape[:2]
            if height > crop.shape[0] or width > crop.shape[1]:
                continue
            result = cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED)
            _, score, _, location = cv2.minMaxLoc(result)
            if score >= TEMPLATE_THRESHOLD and (best is None or score > best[0]):
                best = (score, x0 + location[0] + width // 2, y0 + location[1] + height // 2)
        if best is None:
            return None
        return best[1], best[2]

    def rescale_template(self, template):
        """模板按 1280x720 截图，运行时按当前宽度等比缩放。"""
        scale = (self.screen_width or BASE_WIDTH) / BASE_WIDTH
        if scale == 1:
            return template
        return cv2.resize(
            template,
            (max(1, round(template.shape[1] * scale)), max(1, round(template.shape[0] * scale))),
            interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA,
        )

    def load_template(self, relative_path):
        if relative_path not in self._template_cache:
            self._template_cache[relative_path] = cv2.imread(os.path.join(TEMPLATE_ROOT, relative_path))
        return self._template_cache[relative_path]

    # ---------- 坐标换算 ----------

    def _roi(self, roi):
        x, y, width, height = roi
        return self.box_of_screen_scaled(BASE_WIDTH, BASE_HEIGHT, x, y, width_original=width, height_original=height)
