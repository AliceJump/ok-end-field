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
        if self._logged_in:
            self.ensure_main()
            self.back(after_sleep=2)
            for _ in range(5):
                result=self.find_one(fL.main_out,vertical_variance=0.05,horizontal_variance=0.1,threshold=0.6)
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
            raise RuntimeError("未找到登出按钮")
        self.click(result[0], after_sleep=1)
        self.wait_click_ocr(match=re.compile("确认"), time_out=10, box=self.box.bottom_right, after_sleep=2)
        start_time = time.time()
        while time.time() - start_time < 60:
            result = self.login_ocr(match=re.compile("密码"), box=self.box.bottom)
            if result:
                break
            self.sleep(1)
        if not result:
            raise RuntimeError("未找到密码登录按钮")
        self.sleep(1)
        while time.time() - start_time < 60:
            result = self.login_ocr(match=re.compile("密码"), box=self.box.bottom)
            if result:
                break
            self.sleep(1)
        if not result:
            raise RuntimeError("未找到密码登录按钮")
        run_at_window_pos(self.hwnd.hwnd,pyautogui.click,result[0].x+result[0].width//2,result[0].y+result[0].height//2)
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
    def _type_text(self, text: str):
        """
        通用输入（支持中文）
        """
        import pyperclip

        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
