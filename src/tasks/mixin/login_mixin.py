import re
import time
import pyautogui
from src.tasks.BaseEfTask import BaseEfTask
from src.data.FeatureList import FeatureList as fL
from src.interaction.Mouse import run_at_window_pos


class LoginMixin(BaseEfTask):

    def login_flow(self, username: str, password: str):
        """
        登录流程封装

        Args:
            username (str): 账号
            password (str): 密码
        """
        self._logged_in = False
        start_time = time.time()
        while time.time() - start_time < 3:
            result = self.wait_ocr(match=re.compile("ms"), time_out=1, box=self.box.bottom_left)
            if result:
                self._logged_in = True
                break
            self.sleep(1)
        if self._logged_in:
            self.ensure_main()
            self.back(after_sleep=2)
            for _ in range(5):
                result = self.find_one(fL.main_out, vertical_variance=0.05, horizontal_variance=0.1, threshold=0.6)
                if result:
                    break
                self.sleep(1)
            if result:
                self.click(result, after_sleep=1)
                self.click_confirm()
            else:
                self.log_error("未找到主界面退出按钮，可能未成功返回登录界面")
        start_time = time.time()
        while time.time() - start_time < 120:
            result = self.find_feature(feature_name=fL.logout)
            self.sleep(1)
            if result:
                break
        if not result:
            raise RuntimeError("未找到登出按钮，可能没有先登录，请先登录任意账号")
        self.click(result[0], after_sleep=1)
        self.wait_click_ocr(match=re.compile("确认"), time_out=10, box=self.box.bottom_right, after_sleep=2)
        self._logged_in = False
        start_time = time.time()
        result = None
        one_ok = False
        while time.time() - start_time < 60:
            ocr_result = self.login_ocr(match=re.compile("密码登录"), box=self.box.bottom)

            if not ocr_result:
                if one_ok:
                    self.log_error("已经登录后且未检测到‘密码登录’，说明进入登录页面")
                    break
                self.sleep(1)
                continue
            one_ok = True
            # 点
            box = ocr_result[0]
            run_at_window_pos(
                self.hwnd.hwnd,
                pyautogui.click,
                box.x + box.width // 2,
                box.y + box.height // 2
            )

            self.sleep(1)  # 给UI反应时间

            # ✅ recheck：看“密码登录”是否还在
            check = self.login_ocr(match=re.compile("密码登录"), box=self.box.bottom)

            if not check:
                # 👉 成功：按钮消失 or 页面变化
                result = True
                break

            # 👉 还在 → 说明没点到 / 没反应
            self.log_debug("点击后仍检测到‘密码登录’，准备重试")

            self.sleep(1)

        if not result:
            raise RuntimeError("点击密码登录失败（可能未响应或识别异常）")
        account = self.login_ocr(match=re.compile("账号"), box=self.box.center)
        if not account:
            raise RuntimeError("未找到账号输入框")
        password_square = self.login_ocr(match=re.compile("密码"), box=self.box.center)
        if not password_square:
            raise RuntimeError("未找到密码输入框（bottom）")

        run_at_window_pos(self.hwnd.hwnd, pyautogui.click, int(731 / 1920 * self.width), int(630 / 1080 * self.height))
        run_at_window_pos(
            self.hwnd.hwnd, pyautogui.click, account[0].x + account[0].width // 2, account[0].y + account[0].height // 2
        )
        # 输入账号
        self._type_text(username)
        # 输入密码

        run_at_window_pos(
            self.hwnd.hwnd,
            pyautogui.click,
            password_square[0].x + password_square[0].width // 2,
            password_square[0].y + password_square[0].height // 2,
        )

        self._type_text(password)
        pyautogui.press("enter")
        if not self._confirm_logged_in():
            raise RuntimeError("登录失败")
    def _confirm_logged_in(self, time_out=120):
        start_time = time.time()
        while time.time() - start_time < time_out:
            result = self.find_feature(feature_name=fL.logout)
            if result:
                self.log_info("登录成功")
                return True
            self.sleep(1)
        self.log_error("登录确认超时，疑似登录失败")
        return False

    def _type_text(self, text: str):
        """
        通用输入（支持中文）
        """
        import pyperclip

        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
