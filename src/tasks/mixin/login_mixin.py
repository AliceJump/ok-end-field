import re

import pyautogui
from ok import Box

from src.core.BaseEfTask import BaseEfTask
from src.data.FeatureList import FeatureList as fL
from src.interaction.Mouse import run_at_window_pos
from src.tasks.account.account_identity import visible_account_label, visible_account_parts


class LoginMixin(BaseEfTask):
    def login_flow(self, username: str):
        """
        执行登录流程：登出当前账号并尝试用指定账号登录。

        该方法会：
        - 检查是否已登录并返回主界面；
        - 打开“最近”账号列表；
        - 对同一帧账号列表 OCR 结果分别匹配手机号前三位和后四位，再按几何位置合并账号候选；
        - 可见号码重复时，结合账号文字下方的“最近”标识与切换前账号身份确认目标；
        - 点击登录并等待登录成功。

        Args:
            username (str): 要登录的账号手机号。

        Returns:
            None；若窗口无法激活或登出确认失败则返回 False。

        Raises:
            RuntimeError: 在未找到登出按钮、无法确定目标账号或登录确认失败时抛出异常。
        """
        self._logged_in = False
        start_time = self.active_time()
        while self.active_time() - start_time < 3:
            result = self.wait_ocr(match=self.lang.login_mixin.ms, time_out=1, box=self.box.bottom_left)
            if result:
                self._logged_in = True
                break
        if self._logged_in:
            self.ensure_main()
            self.back()
            result = self.wait_feature(
                feature=fL.main_out,
                vertical_variance=0.05,
                horizontal_variance=0.1,
                threshold=0.6,
                time_out=5,
                raise_if_not_found=False,
            )
            if result:
                self.click(result)
                self.click_confirm()
            else:
                self.log_error("未找到主界面退出按钮，可能未成功返回登录界面")
        result = self.wait_feature(feature=fL.logout, time_out=120, raise_if_not_found=False)
        if not result:
            raise RuntimeError("未找到登出按钮，可能没有先登录，请先登录任意账号")
        self.click(result)
        # 后续「最近/账号/登录」点击走 pyautogui（只作用于前台窗口），必须先把游戏窗口置前。
        if not self.active_and_send_mouse_delta(0, 0, activate=True, only_activate=True):
            self.log_error("无法激活游戏窗口，已取消登录以避免误点其他窗口")
            return False
        if not self.wait_click_feature(feature=fL.log_out_confirm, time_out=5, raise_if_not_found=False):
            self.log_error("未找到登出确认按钮")
            return False

        self._logged_in = False
        recent_tab = self.click_text(
            re.compile("最近"),
            box=self.box.center,
            success_match=self.lang.login_mixin.k_20275ef2,
            need_wait_disappear=False,
        )
        if not recent_tab:
            self.log_error("未找到‘最近’按钮，可能未成功返回登录界面")
            raise RuntimeError("未找到‘最近’按钮，可能未成功返回登录界面")

        # “最近”入口下方就是账号列表。整块区域只 OCR 一次，再分别匹配前三位和后四位。
        account_box = self.box_of_screen(0, (recent_tab[0].y + recent_tab[0].height) / self.height, 1, 1)
        if not self._click_account_from_recent_list(username, account_box):
            label = visible_account_label(username)
            raise RuntimeError(f"无法在最近账号列表中唯一确定账号 {label}")

        self.click_text("登录", box=self.box.center)
        if not self._confirm_logged_in():
            raise RuntimeError("登录失败")

    @staticmethod
    def _ocr_box_text(box: Box) -> str:
        """Normalize OCR text for local prefix/suffix matching."""
        return re.sub(r"\s+", "", str(getattr(box, "name", "") or ""))

    @staticmethod
    def _contains_account_part(text: str, part: str) -> bool:
        """Match a visible phone segment without allowing it to be embedded in a longer digit run."""
        if not text or not part:
            return False
        return re.search(rf"(?<!\d){re.escape(part)}(?!\d)", text) is not None

    @staticmethod
    def _same_account_row(prefix_box: Box, suffix_box: Box) -> bool:
        """Return whether two OCR boxes plausibly belong to the same masked phone row."""
        if prefix_box is suffix_box:
            return True

        prefix_center_y = prefix_box.y + prefix_box.height / 2
        suffix_center_y = suffix_box.y + suffix_box.height / 2
        row_tolerance = max(prefix_box.height, suffix_box.height, 1) * 0.8
        if abs(prefix_center_y - suffix_center_y) > row_tolerance:
            return False

        prefix_center_x = prefix_box.x + prefix_box.width / 2
        suffix_center_x = suffix_box.x + suffix_box.width / 2
        if suffix_center_x <= prefix_center_x:
            return False

        gap = suffix_box.x - (prefix_box.x + prefix_box.width)
        max_gap = max(prefix_box.width, suffix_box.width, 1) * 6
        return gap <= max_gap

    @staticmethod
    def _merge_account_part_boxes(prefix_box: Box, suffix_box: Box) -> Box:
        """Merge the visible prefix/suffix OCR boxes into one clickable account-row box."""
        if prefix_box is suffix_box:
            return prefix_box

        left = min(prefix_box.x, suffix_box.x)
        top = min(prefix_box.y, suffix_box.y)
        right = max(prefix_box.x + prefix_box.width, suffix_box.x + suffix_box.width)
        bottom = max(prefix_box.y + prefix_box.height, suffix_box.y + suffix_box.height)
        return Box(int(left), int(top), int(right - left), int(bottom - top))

    def _account_candidates_from_ocr_results(self, ocr_results: list[Box], username: str) -> list[Box]:
        """Build account candidates by independently matching prefix/suffix boxes, then pairing by geometry."""
        prefix, suffix = visible_account_parts(username)
        if not prefix or not suffix:
            return []

        prefix_boxes: list[Box] = []
        suffix_boxes: list[Box] = []
        for result in ocr_results or []:
            text = self._ocr_box_text(result)
            if self._contains_account_part(text, prefix):
                prefix_boxes.append(result)
            if self._contains_account_part(text, suffix):
                suffix_boxes.append(result)

        if not prefix_boxes or not suffix_boxes:
            return []

        candidates: list[Box] = []
        used_suffix_ids: set[int] = set()
        for prefix_box in sorted(prefix_boxes, key=lambda item: (item.y, item.x)):
            eligible = [
                suffix_box
                for suffix_box in suffix_boxes
                if id(suffix_box) not in used_suffix_ids and self._same_account_row(prefix_box, suffix_box)
            ]
            if not eligible:
                continue

            def pair_score(suffix_box: Box):
                if suffix_box is prefix_box:
                    return (-1, -1)
                prefix_center_y = prefix_box.y + prefix_box.height / 2
                suffix_center_y = suffix_box.y + suffix_box.height / 2
                gap = max(0, suffix_box.x - (prefix_box.x + prefix_box.width))
                return (abs(prefix_center_y - suffix_center_y), gap)

            suffix_box = min(eligible, key=pair_score)
            used_suffix_ids.add(id(suffix_box))
            candidate = self._merge_account_part_boxes(prefix_box, suffix_box)
            signature = (candidate.x, candidate.y, candidate.width, candidate.height)
            if not any((item.x, item.y, item.width, item.height) == signature for item in candidates):
                candidates.append(candidate)

        return sorted(candidates, key=lambda item: (item.y, item.x))

    def _recent_marker_box(self, candidate: Box, next_candidate: Box | None = None):
        """Return the narrow vertical band immediately below one masked account row."""
        top_px = candidate.y + candidate.height
        if next_candidate is not None:
            bottom_px = next_candidate.y
        else:
            # 最后一项没有下一行可作为边界；“最近”就在账号文字下方，只取一个较短的局部带。
            bottom_px = min(self.height, top_px + max(candidate.height * 2, 40))
        if bottom_px <= top_px:
            bottom_px = min(self.height, top_px + max(candidate.height, 20))
        return self.box_of_screen(0, top_px / self.height, 1, bottom_px / self.height)

    def _choose_account_candidate(self, candidates: list[Box], username: str) -> Box | None:
        """Choose one masked account row using the previous identity and the “最近” marker."""
        if not candidates:
            return None
        candidates = sorted(candidates, key=lambda item: (item.y, item.x))
        if len(candidates) == 1:
            return candidates[0]

        recent_candidates: list[Box] = []
        non_recent: list[Box] = []
        for index, candidate in enumerate(candidates):
            next_candidate = candidates[index + 1] if index + 1 < len(candidates) else None
            marker_box = self._recent_marker_box(candidate, next_candidate)
            recent_marker = self.login_ocr(match=re.compile("最近"), box=marker_box, need_active=False)
            if recent_marker:
                recent_candidates.append(candidate)
            else:
                non_recent.append(candidate)

        previous_username = str(getattr(self, "_previous_account_user", "") or "").strip()
        if not previous_username:
            self.log_error("可见账号匹配冲突，但切换前账号身份未知，已取消自动选择")
            return None

        if previous_username == username:
            if len(recent_candidates) == 1:
                self.log_info("目标账号就是切换前账号，已选择标记为‘最近’的账号")
                return recent_candidates[0]
            self.log_error(
                f"目标账号是切换前账号，但‘最近’候选数量为 {len(recent_candidates)}，无法唯一确定"
            )
            return None

        if visible_account_parts(previous_username) == visible_account_parts(username):
            if len(recent_candidates) == 1 and len(non_recent) == 1:
                self.log_info("检测到两个相同可见账号，已排除标记为‘最近’的切换前账号")
                return non_recent[0]
            self.log_error("可见账号与切换前账号相同，但无法通过唯一‘最近’标识区分目标账号")
            return None

        self.log_error("多个账号具有相同目标可见号码，但切换前账号不是其中之一，无法唯一确定目标账号")
        return None

    def _click_account_from_recent_list(self, username: str, box) -> Box | None:
        """OCR the account list once per retry, pair visible prefix/suffix boxes, then click the selected row."""
        label = visible_account_label(username)
        start_time = self.active_time()
        while self.active_time() - start_time < 60:
            ocr_results = self.login_ocr(box=box, need_active=False)
            candidates = self._account_candidates_from_ocr_results(ocr_results, username)
            target = self._choose_account_candidate(candidates, username)
            if target is None:
                self.sleep(1)
                continue

            run_at_window_pos(
                self.get_game_hwnd(),
                pyautogui.click,
                target.x + target.width // 2,
                target.y + target.height // 2,
            )
            self.log_info(f"已选择登录账号: {label}")
            return target

        self.log_error(f"查找登录账号超时或无法唯一确定: {label}")
        return None

    def _confirm_logged_in(self, time_out: int = 120) -> bool:
        """
        等待并确认当前是否已登录（通过查找登出按钮判断）。

        Args:
            time_out (int): 最长等待秒数，超过则返回 False。

        Returns:
            bool: 如果在超时时间内检测到登出按钮返回 True，否则返回 False。
        """
        start_time = self.active_time()
        while self.active_time() - start_time < time_out:
            result = self.find_feature(feature=fL.logout)
            if result:
                self.log_info("登录成功")
                return True
            self.sleep(1)
        self.log_error("登录确认超时，疑似登录失败")
        return False

    def _type_text(self, text: str) -> None:
        """将给定文本粘贴到当前焦点控件以实现可靠输入（支持中文）。"""
        import pyperclip

        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")

    def click_text(
        self,
        match: str | re.Pattern,
        box=None,
        need_wait_disappear: bool = True,
        success_match: str | re.Pattern | None = None,
    ) -> Box | None:
        """OCR 查找并点击文本。"""
        if box is None:
            box = self.box.bottom

        start_time = self.active_time()
        clicked_result = None

        match_text = match.pattern if isinstance(match, re.Pattern) else str(match)
        success_match_text = (
            (success_match.pattern if isinstance(success_match, re.Pattern) else str(success_match))
            if success_match
            else None
        )

        while self.active_time() - start_time < 60:
            ocr_result = self.login_ocr(match=match, box=box, need_active=False)

            if not ocr_result:
                if clicked_result and need_wait_disappear:
                    self.log_info(f"点击并确认目标已消失: {match_text}")
                    return clicked_result
                self.sleep(1)
                continue

            target = ocr_result[0]
            run_at_window_pos(
                self.get_game_hwnd(),
                pyautogui.click,
                target.x + target.width // 2,
                target.y + target.height // 2,
            )
            clicked_result = ocr_result

            if not need_wait_disappear and not success_match:
                return clicked_result

            if success_match:
                success = self.login_ocr(match=success_match, box=box, need_active=False)
                if success:
                    self.log_info(f"点击后检测到成功目标: {success_match_text}")
                    return clicked_result

            if need_wait_disappear:
                check = self.login_ocr(match=match, box=box, need_active=False)
                if not check:
                    self.log_info(f"点击后目标已消失: {match_text}")
                    return clicked_result

            self.log_debug(f"点击后仍检测到'{match_text}'，准备重试")
            self.sleep(1)

        self.log_error(f"点击{match_text}超时或未成功")
        return None
