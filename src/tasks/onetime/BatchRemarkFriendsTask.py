"""批量给好友添加备注。

只处理列表里**还没有备注**的好友：备注已经存在的好友一律跳过，因此可以反复运行。

流程
----
1. 大世界 → Alt+点左上角菜单 → 「好友」→ 好友列表（与批量加好友同一套导航）；
2. 用好友行里的「进船」按钮定位行，OCR 读出「名字#编号（备注）」；
3. 名字里没有括号 = 没有备注 → 点开该好友资料 → 点「备注」→ 输入新备注 → 确认 → 返回列表；
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
from src.tasks.onetime.FriendVisitAdvanced import CLUE_AVAILABLE_OFFSET, FriendVisitAdvancedMixin

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


class FriendListLostError(RuntimeError):
    """无法确认已经回到好友列表。

    此时下一轮会在好友资料页上按列表行扫描，处理的是错误的画面，因此调用方应结束
    本次任务，而不是继续尝试。
    """


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
            try:
                if self.remark_one(row, remark):
                    handled += 1
                    self.log_info(f"已为好友 {row['text']} 添加备注：{remark}")
                else:
                    self.log_warning(f"好友 {row['text']} 备注失败，跳过", images=[self.frame])
            except FriendListLostError as exc:
                self.log_error(f"{exc}，好友批量备注结束")
                break
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
        region = self.adv_offset_box(row["enter"], CLUE_AVAILABLE_OFFSET)
        return self.adv_find_one(f"{VISIT_TEMPLATE_DIR}/ClueExchangeAvailable.png", region) is not None

    # ---------- 单个好友 ----------

    def remark_one(self, row, remark):
        """给一位好友写备注。

        返回 True 仅当「确认已点过 + 已回到好友列表 + 列表里看得到新备注」全部成立；
        任何一步失败都返回 False，不再被 ``run()`` 算作已处理。回到好友列表属于清理
        步骤，失败时抛 :class:`FriendListLostError`，由 ``run()`` 结束任务。
        """
        self.click(row["name_box"], after_sleep=1.0)
        applied = False
        try:
            if self.open_remark_editor():
                # 回读不到完整备注就不点确认，避免把没写全的内容保存下来
                applied = self.input_remark_text(remark) and self.confirm_remark()
        finally:
            # 打开备注入口也可能失败：无论成败都要关掉好友资料页再扫列表
            returned = self.back_to_friend_list()
        if not returned:
            raise FriendListLostError("无法回到好友列表")
        if not applied:
            return False
        if not self.remark_visible_in_list(row["key"]):
            self.log_warning("返回列表后没有看到新备注，界面可能已经变化，请检查截图", images=[self.frame])
            return False
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
        """把备注写进输入框，返回是否回读到完整备注。

        主路径用 ``input_text``：ok 的 PostMessageInteraction 以 WM_CHAR 逐字把文本
        投递给游戏窗口，支持中文，也不要求游戏在前台。回读不到完整备注时才退回剪贴板
        粘贴，并在粘贴前把游戏窗口切到前台，避免按键落到用户正在使用的其它应用。
        备注框对「无备注好友」本来就是空的，主路径不需要清空操作。

        返回 False 时调用方不能点确认：此时输入框里可能只有部分文本或根本没有文本，
        保存下去会被列表检查当成「已有备注」，下次运行也会跳过该好友。
        """
        self.click(self.adv_box(REMARK_INPUT_ROI), after_sleep=0.6)
        self.input_text(remark)
        self.sleep(0.6)
        if self.remark_text_visible(remark):
            return True
        self.log_warning("备注输入框回读不到完整备注，改用剪贴板粘贴（会先清空输入框）")
        return self.paste_remark_text(remark)

    def remark_text_visible(self, remark):
        """OCR 回读备注输入框：读到完整备注文本才视为输入已生效。

        OCR 可能把一段文本拆成多个框、或在汉字之间插入空格，因此把整块区域的识别
        结果拼起来、去掉空白后，再比对完整的备注文本（而不是只要有字就算数）。
        """
        target = re.sub(r"\s+", "", remark)
        if not target:
            return False
        boxes = self.ocr(box=self.adv_box(REMARK_INPUT_ROI), threshold=0.5)
        recognized = re.sub(r"\s+", "", "".join(str(box.name) for box in boxes))
        return target in recognized

    def paste_remark_text(self, remark):
        """剪贴板兜底：先确认游戏已在前台，再用 pyautogui 粘贴（支持中文）。"""
        import pyautogui
        import pyperclip

        # EfInteraction 在 send_key_down 里会把游戏窗口置前，并等待它真正成为前台
        self.send_key_down("ctrl")
        self.send_key_up("ctrl")
        if not self.game_window_in_foreground():
            self.log_warning("游戏窗口没有成为前台，已放弃剪贴板粘贴，避免按键发给其它应用")
            return False
        # 主路径可能其实已经写入、只是 OCR 没回读到：先清空再粘贴，避免文本重复
        pyautogui.hotkey("ctrl", "a")
        pyautogui.press("delete")
        pyperclip.copy(remark)
        self.sleep(0.2)
        pyautogui.hotkey("ctrl", "v")
        self.sleep(0.6)
        visible = self.remark_text_visible(remark)
        if not visible:
            self.log_warning("剪贴板粘贴后仍回读不到完整备注，取消保存")
        return visible

    def game_window_in_foreground(self):
        """游戏窗口当前是否是系统前台窗口。"""
        hwnd = self.get_game_hwnd()
        if not hwnd:
            return False
        try:
            import win32gui
        except ImportError:  # 非 Windows 环境没有 pywin32
            return False
        return win32gui.GetForegroundWindow() == hwnd

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
