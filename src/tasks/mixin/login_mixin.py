import re

import pyautogui
import win32gui
from src.core.BaseEfTask import BaseEfTask, back_window
from src.data.FeatureList import FeatureList as fL
from src.interaction.Mouse import run_at_window_pos
from ok import Box
class LoginMixin(BaseEfTask):

    def login_flow(self, username: str, password: str | None = None):
        """
        执行登录流程：登出当前账号并尝试用指定账号登录。

        该方法会：
        - 检查是否已登录并返回主界面；
        - 点击“最近”列表并尝试选择指定账号后登录；
        - 等待登录成功或超时并返回结果/抛出异常。

        Args:
            username (str): 要登录的账号标识（一般为手机号）。可传完整手机号或仅后四位。
            password (str | None): 兼容旧接口的参数，可传入但不会被存储或用于身份判定（保留以便向后兼容）。

        Returns:
            None

        Raises:
            RuntimeError: 在未找到登出按钮或登录确认失败时抛出异常。

        Notes:
            - 选择账号时先尝试使用完整 `username` 匹配；若 UI 中仅能识别后四位且后四位在列表中唯一，则使用后四位进行点击匹配。
            - 由于 OCR/界面识别的不确定性，使用后四位匹配存在点击到错误账号的风险；建议确保最近账号列表中后四位唯一。
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
        # 前置动作：后续「最近/账号/登录」点击走 pyautogui（只作用于前台窗口），
        # 必须先把游戏窗口置前；后台模式下先记录切换前的前台窗口，交互结束后恢复。
        prev_hwnd = None
        if self.input_mode() == "background":
            prev_hwnd = win32gui.GetForegroundWindow()
        try:
            self.active_and_send_mouse_delta(0, 0, activate=True, only_activate=True)
            if not self.wait_click_feature(feature=fL.log_out_confirm, time_out=5, raise_if_not_found=False):  # 点击登出确认
                self.log_error("未找到登出确认按钮")
                return False
            self._logged_in = False
            result = self.click_text(re.compile("最近"), box=self.box.center, success_match=self.lang.login_mixin.k_20275ef2,
                                     need_wait_disappear=False)  # 点击当前账号（假设是唯一的）"最近", box=self.box.center, need_wait_disappear=False)  # 点击当前账号（假设是唯一的）
            if not result:
                self.log_error("未找到‘最近’按钮，可能未成功返回登录界面")
                raise RuntimeError("未找到‘最近’按钮，可能未成功返回登录界面")
            self.click_text(re.compile(username[-4:]),
                            box=self.box_of_screen(0, (result[0].y + result[0].height) / self.height, 1,
                                                   1))  # 点击最近登录的账号（假设是唯一的）
            self.click_text("登录", box=self.box.center)  # 点击登录按钮
        finally:
            # 后台模式：点击登录后不再有前台点击（后续仅轮询截图确认登录），
            # 无论正常走完、提前返回还是中途异常都恢复切换账号前的前台窗口，避免游戏霸占前台。
            if prev_hwnd is not None:
                if back_window(prev_hwnd):
                    self.log_info("后台模式：已恢复切换账号前的前台窗口")
                else:
                    self.log_debug("后台模式：无需恢复或未能恢复切换账号前的前台窗口")
        if not self._confirm_logged_in():
            raise RuntimeError("登录失败")

    def _confirm_logged_in(self, time_out: int = 120) -> bool:
        """
        等待并确认当前是否已登录（通过查找登出按钮判断）。

        Args:
            time_out (int): 最长等待秒数，超过则返回 False。

        Returns:
            bool: 如果在超时时间内检测到登出按钮返回 True，否则返回 False。

        Notes:
            - 该方法会在检测到登出按钮后立即返回 True；未检测到则记录错误日志并返回 False。
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
        """
        将给定文本粘贴到当前焦点控件以实现可靠输入（支持中文）。

        说明：使用剪贴板+粘贴的方式比逐字符模拟输入更可靠，尤其在输入中文或特殊字符时。

        Args:
            text (str): 要输入的文本。

        Returns:
            None
        """
        import pyperclip

        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")

    def click_text(
            self,
            match: str,
            box=None,
            need_wait_disappear: bool = True,
            success_match: str | None = None,
    ) -> Box | None:
        """
        OCR 查找并点击文本。

        Args:
            match: 要点击的目标文本
            box: 搜索区域
            need_wait_disappear:
                True 时点击后等待目标消失
            success_match:
                点击后若检测到该文本，也视为成功
        """
        if box is None:
            box = self.box.bottom

        start_time = self.active_time()
        clicked_result = None

        # 调用方可能传 str 或 re.Pattern；日志统一用可读文本，
        # 避免 Pattern 对象被 str() 成 "re.compile(...)" 污染日志与 i18n 收集
        match_text = match.pattern if isinstance(match, re.Pattern) else str(match)
        success_match_text = (
            success_match.pattern if isinstance(success_match, re.Pattern) else str(success_match)
        ) if success_match else None

        while self.active_time() - start_time < 60:

            ocr_result = self.login_ocr(
                match=match,
                box=box,
                need_active=False
            )

            # 没找到目标
            if not ocr_result:

                # 如果需要等待消失
                if clicked_result and need_wait_disappear:
                    self.log_info(f"点击并确认目标已消失: {match_text}")
                    return clicked_result

                self.sleep(1)
                continue

            # 找到目标 -> 点击
            target = ocr_result[0]

            run_at_window_pos(
                self.get_game_hwnd(),
                pyautogui.click,
                target.x + target.width // 2,
                target.y + target.height // 2,
            )

            clicked_result = ocr_result

            # 不需要等待
            if not need_wait_disappear and not success_match:
                return clicked_result

            # ---------- 检测 success_match ----------
            if success_match:
                success = self.login_ocr(
                    match=success_match,
                    box=box,
                    need_active=False
                )

                if success:
                    self.log_info(
                        f"点击后检测到成功目标: {success_match_text}"
                    )
                    return clicked_result

            # ---------- 检测原目标是否消失 ----------
            if need_wait_disappear:
                check = self.login_ocr(
                    match=match,
                    box=box,
                    need_active=False
                )

                if not check:
                    self.log_info(
                        f"点击后目标已消失: {match_text}"
                    )
                    return clicked_result

            self.log_debug(
                f"点击后仍检测到'{match_text}'，准备重试"
            )

            self.sleep(1)

        self.log_error(f"点击{match_text}超时或未成功")
        return None
