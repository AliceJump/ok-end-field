"""高级好友拜访（移植自 MaaEnd ``VisitFriends``）。

「收信用」原拜访分支保持不变；本模块只在开启高级开关后替换拜访阶段，
识别方式与 MaaEnd 一致，未自造参数：

- 列表与选友：``assets/resource/pipeline/VisitFriends/{Main,MenuScan,Recognition}.json``
- 进船与访客终端：``assets/resource/pipeline/VisitFriends/Exectue.json``
- 模板：``assets/resource/image/VisitFriends/``（已落到 ``assets/templates/friend_visit/``）

与原分支的差别（也就是「抄」过来的东西）：

1. 以列表右上角的「助力剩余次数 / 线索交换剩余次数」决定何时停止，
   不再假设固定 5 次；
2. 进船后走「访客终端」：接待室线索交换 + 总控中枢 / 制造舱 / 培养舱助力，
   每个动作成功后用 MaaEnd 的结算文案（额外奖励 / 心情恢复 / 制造加速）确认；
3. 支持「只拜访有备注的好友」与「优先拜访名单」（名单按好友名子串匹配）；
4. 支持「快速拜访下一位」，连续拜访时不必每次退回列表；
5. 列表到底用「静帧相似度」判定，滚动后画面稳定再比，避免惯性误判。

坐标
----
所有 ROI 与偏移都按 **1280x720** 标定（MaaEnd 原值），运行时按当前分辨率等比
换算：ROI 交给 ``box_of_screen_scaled``，模板按 ``screen_width / 1280`` 缩放后再
匹配，因此 1080P 同样可用。

语种
----
OCR 文案只写简体中文（上游 MaaEnd 的多语种表未移植）。其它语种界面请继续使用
原拜访分支。
"""

import os
import re

import cv2
import numpy as np
from ok.feature.Box import Box

from src.data.FeatureList import FeatureList as fL

#: ROI 标定基准分辨率
BASE_WIDTH = 1280
BASE_HEIGHT = 720

#: 模板根目录（相对进程工作目录）
TEMPLATE_ROOT = os.path.join("assets", "templates")
#: 本次移植的模板子目录
VISIT_TEMPLATE_DIR = "friend_visit"
#: 通用场景模板子目录
SCENE_TEMPLATE_DIR = "scene_manager"

#: 模板匹配阈值（MaaEnd 原值）
THRESHOLD = 0.9

# ---- 列表页(1280x720) ----
#: 「已在好友列表」锚点
ON_FRIEND_LIST_ROI = (0, 0, 191, 136)
#: 好友列表滚动区域
LIST_REGION_ROI = (46, 114, 1052, 498)
#: 行内「进船」按钮所在竖条（MaaEnd 原值 [-180,135,180,460]，右锚）
ENTER_BUTTON_ROI = (1100, 135, 180, 460)
#: 由进船按钮反推好友名框：dx,dy,dw,dh
NAME_OFFSET = (-1069, -29, 218, 5)
#: 由好友名框反推进船按钮
ROW_ENTER_OFFSET = (956, -18, 33, 106)
#: 行内「可线索交换」标记搜索框（相对进船按钮）
CLUE_AVAILABLE_OFFSET = (-10, -20, 70, 50)
#: 右上角次数图标区域（MaaEnd 原值 [-230,0,230,130]）
COUNTS_ROI = (1050, 0, 230, 130)
#: 次数数字相对图标的偏移
COUNT_NUMBER_OFFSET = (0, 0, 25, 0)

# ---- 进船 / 船内 ----
#: 好友船左上角退出按钮
SHIP_ESC_ROI = (0, 0, 100, 100)
#: 「打开访客终端」交互提示
TERMINAL_HINT_ROI = (10, 200, 230, 200)
#: 访客终端标题
TERMINAL_TITLE_ROI = (0, 0, 200, 70)
#: 「快速拜访下一位」提示（MaaEnd 原值 [-400,70,400,90]，右锚）
QUICK_VISIT_ROI = (880, 70, 400, 90)
#: 快速拜访提示上的线索交换标记（MaaEnd 原值 [-230,80,60,70]，右锚）
QUICK_VISIT_CLUE_ROI = (1050, 80, 60, 70)

# ---- 好友详情（点进船按钮后弹出）----
#: 详情页「情报交流进行中」文案
FRIEND_CLUE_ROI = (312, 316, 658, 172)
#: 详情页三舱助力图标
FRIEND_DETAIL_ROI = (700, 280, 300, 150)
#: 详情页可助力舱室模板（MaaEnd 原图）
DETAIL_ASSIST_TEMPLATES = (
    "DetailControlNexusAssist.png",
    "DetailMFGCabinAssist.png",
    "DetailGrowthChamberAssist.png",
)

# ---- 访客终端内 ----
#: 接待室（线索交换）
RECEPTION_ROI = (410, 400, 200, 200)
#: 线索交换按钮搜索区域（相对接待室）
CLUE_BUTTON_REGION = (0, 0, 200, 200)
#: 线索交换已满的文案区域
CLUE_FULL_ROI = (80, 360, 420, 110)
#: 总控中枢
NEXUS_ROI = (40, 400, 260, 200)
#: 制造舱 1 / 2
MFG_CABIN_ROIS = ((610, 400, 200, 200), (810, 400, 200, 200))
#: 培养舱（先左滑再识别）
GROWTH_ROI = (810, 400, 200, 200)
#: 「助力次数已满」提示
ASSIST_FULL_ICON_ROI = (980, 300, 300, 250)
ASSIST_FULL_TEXT_ROI = (480, 290, 800, 270)

# ---- OCR 文案（仅简体中文）----
TEXT_TERMINAL_HINT = re.compile(r"打开访客终端")
TEXT_TERMINAL_TITLE = re.compile(r"访客终端")
TEXT_CLUE_SUCCESS = re.compile(r"额外奖励")
TEXT_CLUE_FULL = re.compile(r"情报交流")
TEXT_NEXUS_SUCCESS = re.compile(r"心情恢复")
TEXT_MFG_SUCCESS = re.compile(r"制造加速")
TEXT_ASSIST_FULL = re.compile(r"明日再来")
TEXT_QUICK_VISIT = re.compile(r"快速拜访下位")
TEXT_FRIEND_NAME = re.compile(r"#")
#: 好友详情页「情报交流进行中」
TEXT_CLUE_IN_PROGRESS = re.compile(r"情报交流进行中")
#: MaaEnd ``ExpendableRecognition`` 的 key 归一化，必须照抄，
#: 否则同一个人「带备注 / 不带备注」会被当成两个人重复拜访。
ROW_KEY_PATTERN = re.compile(r"(?:[（(])?([^（（）)]*#\d+)")
#: 备注标记：好友名里出现括号即视为已有备注
REMARK_PATTERN = re.compile(r"[（(]")
#: 名单分隔符
NAME_SPLIT_PATTERN = re.compile(r"[,，;；、\s]+")

#: 列表到底的静帧相似度阈值（MaaEnd 原值 0.98）
LIST_COMPLETE_THRESHOLD = 0.98


class FriendVisitAdvancedMixin:
    """MaaEnd 式好友拜访。需要与 BaseTask 子类混入，且任务类提供日志/配置能力。"""

    #: 助力舱室可选项
    ASSIST_NEXUS = "总控中枢"
    ASSIST_MFG = "制造舱"
    ASSIST_GROWTH = "培养舱"
    ASSIST_TARGETS = (ASSIST_NEXUS, ASSIST_MFG, ASSIST_GROWTH)

    # ---------- 入口 ----------

    def advanced_visit(self):
        """执行高级拜访。返回是否正常结束（不抛异常）。"""
        self._visit_templates = {}
        self._list_snapshot = None
        targets = self.adv_assist_targets()
        remark_only = bool(self.config.get(getattr(self, "CFG_REMARK_ONLY", ""), False))
        priority = self.adv_priority_names()
        priority_re = self.adv_priority_regex(priority)
        max_visits = int(self.config.get(getattr(self, "CFG_MAX_VISITS", ""), 0) or 0)

        self.adv_collect_pending_credit()

        if not self.adv_enter_friend_list():
            self.log_error("未能进入好友列表，高级拜访结束")
            return False

        visited = set()
        visited_count = 0
        modes = []
        if priority_re is not None:
            modes.append("priority")
        if remark_only:
            modes.append("remark")
        modes.append("normal")
        mode_index = 0
        rounds = 0

        while not self.exit_is_set():
            rounds += 1
            if rounds > 200:
                self.log_warning("高级拜访循环次数过多，疑似卡在列表中，提前结束")
                break
            assist_left, clue_left = self.adv_remaining_counts()
            if assist_left == 0 and clue_left == 0:
                self.log_info("好友助力与情报交流次数均已用尽，结束拜访")
                break
            rows = self.adv_scan_rows()
            row = None
            if rows:
                row = self.adv_pick_row(rows, visited, modes[mode_index], priority_re)
            if row is None:
                if rows and self.adv_list_at_bottom():
                    if mode_index + 1 < len(modes):
                        mode_index += 1
                        self.log_info(f"当前筛选方式已扫到底，切换到{self.adv_mode_name(modes[mode_index])}继续拜访")
                        self._list_snapshot = None
                        if not self.adv_reopen_friend_list():
                            self.log_error("重新打开好友列表失败，高级拜访结束")
                            return False
                        continue
                    self.log_info("好友列表已扫到底，结束拜访")
                    break
                if not rows:
                    if self.adv_list_at_bottom():
                        if mode_index + 1 < len(modes):
                            mode_index += 1
                            self._list_snapshot = None
                            if not self.adv_reopen_friend_list():
                                return False
                            continue
                        break
                    self.adv_scroll_list()
                    continue
                self.adv_scroll_list()
                continue

            visited.add(row["key"])
            if not self.adv_visit_one(row, targets):
                self.log_info(f"好友 {row['text']} 本次未拜访（无可交流助力内容或进船失败）")
            visited_count += 1
            if max_visits and visited_count >= max_visits:
                self.log_info(f"已达到最大拜访次数 {max_visits}，结束拜访")
                break

        self.log_info(f"高级拜访结束，本次共拜访 {visited_count} 位好友")
        return True

    @staticmethod
    def adv_mode_name(mode):
        return {"priority": "优先名单", "remark": "仅备注好友", "normal": "全部好友"}.get(mode, mode)

    # ---------- 配置 ----------

    def adv_assist_targets(self):
        raw = self.config.get(getattr(self, "CFG_ASSIST_TARGETS", ""), list(self.ASSIST_TARGETS))
        if not isinstance(raw, list):
            return []
        return [t for t in raw if t in self.ASSIST_TARGETS]

    def adv_priority_names(self):
        raw = self.config.get(getattr(self, "CFG_PRIORITY", ""), "")
        if not isinstance(raw, str):
            return []
        return [n for n in NAME_SPLIT_PATTERN.split(raw) if n]

    @staticmethod
    def adv_priority_regex(names):
        """名单编译成「包含任一名字」的正则；空名单返回 None（等价永不命中）。"""
        if not names:
            return None
        return re.compile("|".join(re.escape(n) for n in names))

    # ---------- 前置：收待领信用 ----------

    def adv_collect_pending_credit(self):
        """与 ``collect_credit`` 前段保持一致；原分支保持不变，故此处单独实现。"""
        try:
            self.info_set("current_task", "collect_credit")
            self.press_key("f5")
            self.wait_click_ocr(
                match=self.lang.daily_routine_mixin.k_7be4248b, box=self.box.top, time_out=5, recheck_time=1
            )
            result = self.wait_click_ocr(
                match=[self.lang.daily_routine_mixin.k_b693e51a, self.lang.daily_routine_mixin.k_f646bcd5],
                box=self.box.bottom_left,
                time_out=7,
                recheck_time=1,
            )
            if not result:
                self.log_info("未找到可收取信用或无待领取信用的选项")
                return False
            if "收取信用" in result[0].name:
                self.wait_pop_up()
            self.ensure_main()
            return True
        except Exception as exc:  # 收取信用失败不应阻断拜访
            self.log_warning(f"收取待领取信用时出错，继续拜访：{exc}")
            return False

    # ---------- 场景 ----------

    def adv_enter_friend_list(self):
        """从大世界进入好友列表：Alt+点左上角菜单 → 好友 → 好友列表。"""
        self.ensure_main()
        if self.adv_in_friend_list() and self.adv_wait_list_loaded():
            return True
        for _ in range(4):
            if self.adv_in_friend_list() and self.adv_wait_list_loaded():
                return True
            menu = self.adv_find_one(f"{SCENE_TEMPLATE_DIR}/WorldMenu.png", self.adv_box((0, 0, 200, 200)))
            if menu is not None:
                self.log_info("打开大世界菜单")
                self.adv_click_alt(menu)
                self.sleep(1.5)
                continue
            friend_item = self.adv_find_text(self.adv_box((880, 0, 400, 720)), re.compile(r"好友"))
            if friend_item is not None:
                self.log_info("点击菜单里的「好友」")
                self.click(friend_item, after_sleep=1.2)
                continue
            entry = self.adv_find_one(
                f"{SCENE_TEMPLATE_DIR}/FriendsList1.png", self.adv_box((450, 0, 400, 70))
            ) or self.adv_find_one(f"{SCENE_TEMPLATE_DIR}/FriendsList2.png", self.adv_box((450, 0, 400, 70)))
            if entry is not None:
                self.log_info("进入好友列表")
                self.click(entry, after_sleep=1.2)
                continue
            if not self.safe_back(feature=fL.friend_page, time_out=5):
                break
        return self.adv_in_friend_list() and self.adv_wait_list_loaded()

    def adv_reopen_friend_list(self):
        """重置列表状态后重新进入（对应 MaaEnd 的 RemarkFallbackReopen）。"""
        self._list_snapshot = None
        self.ensure_main()
        return self.adv_enter_friend_list()

    def adv_in_friend_list(self):
        return self.adv_find_one(f"{VISIT_TEMPLATE_DIR}/OnFriendList.png", self.adv_box(ON_FRIEND_LIST_ROI)) is not None

    def adv_wait_list_loaded(self, time_out=10):
        """等列表项加载完成：出现进船按钮且能 OCR 到含 # 的好友名。"""
        start = self.active_time()
        while self.active_time() - start < time_out:
            if self.adv_scan_rows():
                return True
            self.sleep(0.5)
        return False

    # ---------- 列表扫描 ----------

    def adv_scan_rows(self):
        """扫描当前可见的好友行。"""
        rows = []
        buttons = self.adv_find(
            f"{VISIT_TEMPLATE_DIR}/EnterButton.png", self.adv_box(ENTER_BUTTON_ROI), threshold=THRESHOLD
        )
        for button in buttons:
            name_box = self.adv_offset_box(button, NAME_OFFSET)
            texts = self.adv_find_text(name_box, TEXT_FRIEND_NAME, threshold=0.55)
            if texts is None:
                continue
            text = str(texts.name).strip()
            key = self.adv_row_key(text)
            if not key:
                continue
            rows.append(
                {
                    "text": text,
                    "key": key,
                    "remark": bool(REMARK_PATTERN.search(text)),
                    "enter": button,
                    "name_box": name_box,
                }
            )
        return rows

    @staticmethod
    def adv_row_key(text):
        match = ROW_KEY_PATTERN.search(text)
        return match.group(1).strip() if match else None

    def adv_pick_row(self, rows, visited, mode, priority_re):
        for row in rows:
            if row["key"] in visited:
                continue
            if mode == "priority" and not (priority_re and priority_re.search(row["text"])):
                continue
            if mode == "remark" and not row["remark"]:
                continue
            return row
        return None

    def adv_scroll_list(self):
        """滚动列表。MaaEnd 要求松手后等画面冻结，否则静帧判定会失败。"""
        self.scroll_relative(0.5, 0.6, -3)
        self.sleep(0.6)
        self.wait_ui_stable(stable_time=0.4, max_wait=2, refresh_interval=0.3)

    def adv_list_at_bottom(self):
        """与上一次快照比较列表区域，静止即认为到底（MaaEnd ListCompleteRecognition）。"""
        frame = self.frame
        if frame is None:
            return False
        box = self.adv_box(LIST_REGION_ROI)
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

    # ---------- 剩余次数 ----------

    def adv_remaining_counts(self):
        """读取右上角「助力 / 线索交换」剩余次数；读不到返回 (None, None)。"""
        assist = self.adv_read_count(f"{VISIT_TEMPLATE_DIR}/AssistCountIcon.png")
        clue = self.adv_read_count(f"{VISIT_TEMPLATE_DIR}/ClueExchangeCountIcon.png")
        self.log_debug(f"剩余次数：助力={assist} 情报交流={clue}")
        return assist, clue

    def adv_read_count(self, template_name):
        hits = self.adv_find(template_name, self.adv_box(COUNTS_ROI), threshold=0.85)
        for hit in hits:
            number = self.adv_find_text(self.adv_offset_box(hit, COUNT_NUMBER_OFFSET), re.compile(r"\d+"), 0.5)
            if number is None:
                continue
            digits = re.sub(r"\D", "", str(number.name))
            if digits:
                return int(digits)
        return None

    # ---------- 单个好友 ----------

    def adv_visit_one(self, row, targets):
        if not self.adv_enter_ship(row, targets):
            return False
        try:
            while not self.exit_is_set():
                if self.adv_open_guest_terminal():
                    self.adv_terminal_actions(targets)
                    self.adv_exit_guest_terminal()
                else:
                    self.log_warning("未找到访客终端入口，跳过该好友的终端操作")
                if not self.adv_quick_visit_next(targets):
                    break
        finally:
            # 出船/换人后列表必然不在尽头，清空静帧快照（MaaEnd attach.ready=false）
            self._list_snapshot = None
        self.adv_leave_ship()
        return True

    def adv_enter_ship(self, row, targets):
        """点进船按钮 → 好友帝江号详情 → 判定有可做内容 → 再点一次进船。"""
        self.log_info(f"拜访好友 {row['text']}")
        self.click(row["enter"], after_sleep=0.8)
        if not self.adv_friend_has_activity(targets):
            self.log_info(f"好友 {row['text']} 当前没有可交流或可助力的内容，跳过")
            self.back()
            self.sleep(0.8)
            return False
        self.click(row["enter"], after_sleep=0.8)
        self.click_confirm(time_out=5, recheck_time=0)
        if self.wait_until(self.in_friend_boat, time_out=60, settle_time=0.5):
            return True
        if self.ensure_in_friend_boat():
            return True
        self.log_warning("未能进入好友帝江号")
        return False

    def adv_friend_has_activity(self, targets):
        """好友详情页：有情报交流进行中，或所选舱室可助力（MaaEnd VisitFriendsCheckAssist）。"""
        if self.adv_find_text(self.adv_box(FRIEND_CLUE_ROI), TEXT_CLUE_IN_PROGRESS, 0.6) is not None:
            return True
        if not targets:
            return False
        for name in DETAIL_ASSIST_TEMPLATES:
            if self.adv_find_one(f"{VISIT_TEMPLATE_DIR}/{name}", self.adv_box(FRIEND_DETAIL_ROI)) is not None:
                return True
        return False

    def adv_leave_ship(self):
        for _ in range(3):
            esc = self.adv_find_one(f"{VISIT_TEMPLATE_DIR}/ShipEscButton.png", self.adv_box(SHIP_ESC_ROI))
            if esc is None:
                break
            self.adv_click_alt(esc)
            if self.wait_until(self.adv_in_friend_list, time_out=8):
                self.adv_wait_list_loaded(time_out=5)
                return True
        if self.safe_back(feature=fL.friend_page, time_out=10):
            self.adv_wait_list_loaded(time_out=5)
            return True
        self.log_warning("未能确认已返回好友列表")
        return False

    # ---------- 访客终端 ----------

    def adv_in_terminal(self):
        return self.adv_find_text(self.adv_box(TERMINAL_TITLE_ROI), TEXT_TERMINAL_TITLE, 0.6) is not None

    def adv_open_guest_terminal(self):
        """Alt+点「打开访客终端」，最多三轮（MaaEnd RepeatUntilFoundAction）。"""
        for _ in range(3):
            hint = self.adv_find_text(self.adv_box(TERMINAL_HINT_ROI), TEXT_TERMINAL_HINT, 0.6)
            if hint is None:
                return self.adv_in_terminal()
            self.adv_click_alt(hint)
            if self.wait_until(self.adv_in_terminal, time_out=6):
                return True
        return False

    def adv_exit_guest_terminal(self):
        if not self.adv_in_terminal():
            return True
        for _ in range(3):
            self.back()
            self.sleep(0.8)
            if not self.adv_in_terminal():
                return True
        self.log_warning("关闭访客终端失败")
        return False

    def adv_terminal_actions(self, targets):
        """MaaEnd 的 SubTask(continue=true)：任何一项失败都不要中断。"""
        actions = [("情报交流", self.adv_exchange_clue)]
        for target in targets:
            actions.append((f"{target}助力", lambda t=target: self.adv_assist(t)))
        for name, action in actions:
            try:
                if not action():
                    self.log_info(f"{name}未执行（不可用或已满）")
            except Exception as exc:  # 单项失败不阻断
                self.log_warning(f"{name}执行出错：{exc}")

    def adv_exchange_clue(self):
        if self.adv_clue_full():
            return False
        icon = self.adv_find_one(f"{VISIT_TEMPLATE_DIR}/TerminalReceptionRoom.png", self.adv_box(RECEPTION_ROI))
        if icon is None:
            return False
        region = self.adv_offset_box(icon, CLUE_BUTTON_REGION)
        button = self.adv_find_one(f"{VISIT_TEMPLATE_DIR}/ClueExchange.png", region)
        if button is None:
            return False
        self.click(button, after_sleep=0.8)
        if self.wait_until(lambda: self.adv_find_text(region, TEXT_CLUE_SUCCESS, 0.6) is not None, time_out=6):
            self.log_info("情报交流成功")
            return True
        return False

    def adv_clue_full(self):
        text = self.adv_find_text(self.adv_box(CLUE_FULL_ROI), TEXT_CLUE_FULL, 0.6)
        if text is None:
            return False
        zero = self.adv_find_text(self.adv_box(CLUE_FULL_ROI), re.compile(r"^\s*0\s*$"), 0.6)
        return zero is not None

    def adv_assist(self, target):
        if self.adv_assist_full():
            return False
        if target == self.ASSIST_NEXUS:
            return self.adv_assist_card(
                NEXUS_ROI,
                f"{VISIT_TEMPLATE_DIR}/TerminalControlNexus.png",
                TEXT_NEXUS_SUCCESS,
                (0, 0, 250, 200),
            )
        if target == self.ASSIST_MFG:
            done = False
            for roi in MFG_CABIN_ROIS:
                if self.adv_assist_card(
                    roi, f"{VISIT_TEMPLATE_DIR}/TerminalMFGCabin.png", TEXT_MFG_SUCCESS, (0, 0, 200, 200)
                ):
                    done = True
            return done
        if target == self.ASSIST_GROWTH:
            # 培养舱在右侧屏幕外，先左滑（MaaEnd 原值 1000,500 -> 600,500）
            self.smooth_drag((1000 / BASE_WIDTH, 500 / BASE_HEIGHT), (600 / BASE_WIDTH, 500 / BASE_HEIGHT))
            self.sleep(0.8)
            return self.adv_assist_card(
                GROWTH_ROI,
                f"{VISIT_TEMPLATE_DIR}/TerminalGrowthChamber.png",
                None,
                (0, 0, 200, 200),
                threshold=0.8,
                wait_reward_popup=True,
            )
        return False

    def adv_assist_card(self, roi, template_name, success_text, region, threshold=THRESHOLD, wait_reward_popup=False):
        card = self.adv_find_one(template_name, self.adv_box(roi), threshold=threshold)
        if card is None:
            return False
        region_box = self.adv_offset_box(card, region)
        button = self.adv_find_one(f"{VISIT_TEMPLATE_DIR}/ProductionAssist.png", region_box)
        if button is None:
            return False
        self.click(button, after_sleep=0.8)
        if success_text is not None:
            if self.wait_until(lambda: self.adv_find_text(region_box, success_text, 0.6) is not None, time_out=6):
                self.log_info("助力成功")
                return True
            return False
        self.wait_pop_up(time_out=6)
        return True

    def adv_assist_full(self):
        icon = self.adv_find_one(f"{VISIT_TEMPLATE_DIR}/TerminalNotice.png", self.adv_box(ASSIST_FULL_ICON_ROI))
        if icon is None:
            return False
        return self.adv_find_text(self.adv_box(ASSIST_FULL_TEXT_ROI), TEXT_ASSIST_FULL, 0.6) is not None

    # ---------- 快速拜访 ----------

    def adv_quick_visit_next(self, targets):
        """点「快速拜访下一位」直接进入下一位好友的船；成功返回 True。"""
        for _ in range(3):
            hint = self.adv_find_text(self.adv_box(QUICK_VISIT_ROI), TEXT_QUICK_VISIT, 0.55)
            if hint is None:
                return False
            # 未勾选任何助力时，MaaEnd 还要求提示上带线索交换标记
            if not targets and (
                self.adv_find_one(
                    f"{VISIT_TEMPLATE_DIR}/QuickNextClueExchangeAvailable.png", self.adv_box(QUICK_VISIT_CLUE_ROI)
                )
                is None
            ):
                return False
            self.adv_click_alt(hint)
            if self.wait_until(self.adv_in_terminal, time_out=10):
                self.log_info("快速拜访下一位成功")
                return True
        return False

    # ---------- 基础工具 ----------

    def adv_box(self, roi):
        """1280x720 像素 ROI → 当前分辨率 Box。"""
        x, y, width, height = roi
        return self.box_of_screen_scaled(BASE_WIDTH, BASE_HEIGHT, x, y, width_original=width, height_original=height)

    def adv_scale(self):
        return (self.screen_width or BASE_WIDTH) / BASE_WIDTH

    def adv_offset_box(self, box, offset):
        """按 1280x720 标定的 (dx,dy,dw,dh) 偏移一个已换算到屏幕坐标的 Box。"""
        dx, dy, dw, dh = offset
        scale = self.adv_scale()
        return Box(
            box.x + round(dx * scale),
            box.y + round(dy * scale),
            max(1, box.width + round(dw * scale)),
            max(1, box.height + round(dh * scale)),
            name=box.name,
        )

    def adv_load_template(self, relative_path):
        cache = getattr(self, "_visit_templates", None)
        if cache is None:
            cache = self._visit_templates = {}
        if relative_path not in cache:
            cache[relative_path] = cv2.imread(os.path.join(TEMPLATE_ROOT, relative_path))
        return cache[relative_path]

    def adv_match(self, relative_path, box, threshold=THRESHOLD):
        """在 box 内做模板匹配，返回屏幕坐标 Box 列表（多命中时按行优先）。"""
        frame = self.frame
        template = self.adv_load_template(relative_path)
        if frame is None or template is None:
            return []
        scale = self.adv_scale()
        if scale != 1:
            template = cv2.resize(
                template,
                (max(1, round(template.shape[1] * scale)), max(1, round(template.shape[0] * scale))),
                interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA,
            )
        x0, y0 = max(box.x, 0), max(box.y, 0)
        x1 = min(box.x + box.width, frame.shape[1])
        y1 = min(box.y + box.height, frame.shape[0])
        if x1 - x0 < template.shape[1] or y1 - y0 < template.shape[0]:
            return []
        crop = frame[y0:y1, x0:x1]
        result = cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(result >= threshold)
        found = []
        for x, y in sorted(zip(xs.tolist(), ys.tolist(), strict=True), key=lambda item: (item[1], item[0])):
            if any(
                abs((x + x0) - existing.x) < template.shape[1] * 0.6
                and abs((y + y0) - existing.y) < template.shape[0] * 0.6
                for existing in found
            ):
                continue
            found.append(Box(x + x0, y + y0, template.shape[1], template.shape[0], float(result[y, x]), relative_path))
        return found

    def adv_find(self, relative_path, box, threshold=THRESHOLD):
        return self.adv_match(relative_path, box, threshold)

    def adv_find_one(self, relative_path, box, threshold=THRESHOLD):
        found = self.adv_match(relative_path, box, threshold)
        return found[0] if found else None

    def adv_find_text(self, box, pattern, threshold=0.6):
        results = self.ocr(box=box, match=pattern, threshold=threshold)
        return results[0] if results else None

    def adv_click_alt(self, box):
        """Alt+左键；Alt 必须抬起，否则后续按键都会变成组合键。"""
        self.send_key_down("alt")
        try:
            self.click(box)
        finally:
            self.send_key_up("alt")
        self.sleep(0.5)
