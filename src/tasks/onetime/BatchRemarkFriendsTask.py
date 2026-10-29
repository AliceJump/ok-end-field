"""批量给好友添加备注。

只处理列表里**还没有备注**的好友：备注已经存在的好友一律跳过，因此可以反复运行。

流程
----
1. 大世界 → Alt+点左上角菜单 → 「好友」→ 好友列表（与批量加好友同一套导航）；
2. 用好友行里的「进船」按钮定位行，OCR 读出「名字#编号（备注）」；
3. 名字里没有括号 = 没有备注 → 点开该好友资料 → 点「备注」→ 清空并输入新备注 → 确认 → 返回列表；
4. 处理完一屏就滚动，列表到底即结束。

配置
----
- 备注内容：写进备注的文本，默认「自动」；
- 追加序号：开启后按处理顺序追加 1、2、3…，避免所有人备注完全一样；
- 最大处理数量：0 表示不限制；
- 只处理可交流情报的好友：只给列表里带「可情报交流」标记的好友加备注。

坐标按 **1280x720** 标定（与 MaaEnd 一致），运行时按 ``screen_width / 1280`` 等比
换算 ROI 与模板，1080P 同样可用。OCR 文案只写简体中文。
"""

import re

from qfluentwidgets import FluentIcon

from src.tasks.mixin.common import Common
from src.tasks.onetime.FriendVisitAdvanced import FriendVisitAdvancedMixin

VISIT_TEMPLATE_DIR = "friend_visit"

# ---- 1280x720 标定 ----
#: 好友资料页里「备注」按钮的候选区域
PROFILE_REMARK_ROI = (360, 260, 560, 280)
#: 备注输入弹窗：输入框区域
REMARK_INPUT_ROI = (380, 240, 520, 200)
#: 备注输入弹窗：确认按钮区域
REMARK_CONFIRM_ROI = (380, 460, 520, 180)
#: 单次运行最多尝试的好友行数（防止异常空转）
MAX_ROUNDS = 300

TEXT_REMARK = re.compile(r"备注")
TEXT_CONFIRM = re.compile(r"^(确定|确认|保存|完成)$")


class BatchRemarkFriendsTask(FriendVisitAdvancedMixin, Common):
    """给没有备注的好友批量添加备注。"""

    CFG_REMARK_TEXT = "备注内容"
    CFG_MAX_COUNT = "最大处理数量"
    CFG_CLUE_ONLY = "只处理可交流情报的好友"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "好友批量备注"
        self.icon = FluentIcon.PEOPLE
        self.group_name = "日常任务"
        self.group_icon = FluentIcon.CALENDAR
        self.description = "给好友列表里没有备注的好友批量添加备注，已有备注的好友会跳过。"
        self.default_config.update(
            {
                self.CFG_REMARK_TEXT: "",
                self.CFG_MAX_COUNT: 0,
                self.CFG_CLUE_ONLY: False,
            }
        )
        self.config_type[self.CFG_MAX_COUNT] = {"min": 0, "max": 200}
        self.config_description.update(
            {
                self.CFG_REMARK_TEXT: "要写进游戏内好友备注的文本，由你自行填写；只处理还没有备注的好友。",
                self.CFG_MAX_COUNT: "本次最多处理多少个好友，0 表示不限制。",
                self.CFG_CLUE_ONLY: (
                    "开启后只给列表里带「可情报交流」标记的好友加备注，其它好友跳过。\n"
                    "用于「参与情报交流的好友」这一批人。"
                ),
            }
        )

    # ---------- 入口 ----------

    def run(self):
        self._visit_templates = {}
        self._list_snapshot = None
        self.log_info(f"当前分辨率 {self.screen_width}x{self.screen_height}，备注坐标按 1280x720 等比换算")
        if not self.adv_enter_friend_list():
            self.log_error("未能进入好友列表，好友批量备注结束")
            return
        max_count = int(self.config.get(self.CFG_MAX_COUNT, 0) or 0)
        clue_only = bool(self.config.get(self.CFG_CLUE_ONLY, False))
        remark = str(self.config.get(self.CFG_REMARK_TEXT, "") or "").strip()
        if not remark:
            self.log_error("还没有填写「备注内容」，请先在任务配置里填好要写的备注文本")
            return

        handled = 0
        visited = set()
        rounds = 0
        while not self.exit_is_set():
            rounds += 1
            if rounds > MAX_ROUNDS:
                self.log_warning("处理轮次过多，提前结束")
                break
            row = self.pick_row_to_remark(visited, clue_only)
            if row is None:
                if self.adv_list_at_bottom():
                    self.log_info("好友列表已扫到底")
                    break
                self.adv_scroll_list()
                continue
            visited.add(row["key"])
            if self.remark_one(row, remark):
                handled += 1
                self.log_info(f"已为好友 {row['text']} 添加备注：{remark}")
            else:
                self.log_warning(f"好友 {row['text']} 备注失败，跳过", images=[self.frame])
            if max_count and handled >= max_count:
                self.log_info(f"已达到最大处理数量 {max_count}，结束")
                break
        self.log_info(f"好友批量备注结束，本次处理 {handled} 位好友")

    # ---------- 筛选 ----------

    def pick_row_to_remark(self, visited, clue_only):
        for row in self.adv_scan_rows():
            if row["key"] in visited or row["remark"]:
                continue
            if clue_only and not self.row_clue_available(row):
                continue
            return row
        return None

    def row_clue_available(self, row):
        """行内是否带「可情报交流」标记（MaaEnd ClueExchangeAvailable）。"""
        region = self.adv_offset_box(row["enter"], self.CLUE_AVAILABLE_OFFSET)
        return self.adv_find_one(f"{VISIT_TEMPLATE_DIR}/ClueExchangeAvailable.png", region) is not None

    # ---------- 单个好友 ----------

    def remark_one(self, row, remark):
        self.click(row["name_box"], after_sleep=1.0)
        if not self.open_remark_editor():
            return False
        try:
            self.input_remark_text(remark)
            if not self.confirm_remark():
                return False
        finally:
            self.back_to_friend_list()
        if not self.remark_visible_in_list(row["key"]):
            self.log_warning("返回列表后没有看到新备注，界面可能已经变化，请检查截图", images=[self.frame])
        return True

    def remark_visible_in_list(self, key):
        """回列表后确认该好友已经带上备注。"""
        for row in self.adv_scan_rows():
            if row["key"] == key:
                return bool(row["remark"])
        return False

    def open_remark_editor(self):
        """在好友资料页点「备注」。"""
        for _ in range(2):
            button = self.adv_find_text(self.adv_box(PROFILE_REMARK_ROI), TEXT_REMARK, 0.6)
            if button is not None:
                self.click(button, after_sleep=1.0)
                return True
            # 有些界面要先从好友菜单进「好友资料」页
            profile = self.adv_find_one(
                f"{VISIT_TEMPLATE_DIR}/FriendsProfile1.png", self.adv_box((450, 0, 400, 70))
            ) or self.adv_find_one(f"{VISIT_TEMPLATE_DIR}/FriendsProfile2.png", self.adv_box((450, 0, 400, 70)))
            if profile is None:
                break
            self.click(profile, after_sleep=1.2)
        self.log_warning("没有找到好友资料里的「备注」入口")
        return False

    def input_remark_text(self, remark):
        """清空备注输入框并粘贴新文本（剪贴板粘贴，支持中文）。"""
        import pyautogui
        import pyperclip

        self.click(self.adv_box(REMARK_INPUT_ROI), after_sleep=0.6)
        pyautogui.hotkey("ctrl", "a")
        self.sleep(0.2)
        pyautogui.press("delete")
        self.sleep(0.2)
        pyperclip.copy(remark)
        pyautogui.hotkey("ctrl", "v")
        self.sleep(0.6)

    def confirm_remark(self):
        button = self.adv_find_text(self.adv_box(REMARK_CONFIRM_ROI), TEXT_CONFIRM, 0.6)
        if button is not None:
            self.click(button, after_sleep=0.8)
            return True
        if self.click_confirm(time_out=3, recheck_time=0):
            return True
        self.log_warning("没有找到备注确认按钮")
        return False

    def back_to_friend_list(self):
        for _ in range(3):
            self.back()
            self.sleep(0.8)
            if self.adv_in_friend_list() and self.adv_wait_list_loaded(time_out=5):
                return True
        return False
