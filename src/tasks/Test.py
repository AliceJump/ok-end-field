import re

from src.tasks.BaseEfTask import BaseEfTask
from src.tasks.daily.daily_liaison_mixin import DailyLiaisonMixin
from src.data.FeatureList import FeatureList as fL
from src.tasks.mixin.common import Common
from src.tasks.mixin.login_mixin import LoginMixin
from src.tasks.AutoCombatLogic import AutoCombatLogic
from src.interaction.Mouse import run_at_window_pos
import pyautogui
from ok import TaskDisabledException


class Test(LoginMixin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.refresh_count = 0
        self.refresh_cost_list = [80, 120, 160, 200]
        self.credit_good_search_box = None

    def run(self):
        self.active_and_send_mouse_delta(activate=True, only_activate=True)
        self.sleep(1)
        for _ in range(10):
            pyautogui.scroll(8)
    def _type_text(self, text: str):
        """
        通用输入（支持中文）
        """
        import pyperclip

        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
