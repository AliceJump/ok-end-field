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
        self.ensure_main()
        self.login_flow(username="test", password_square="test")
    def _type_text(self, text: str):
        """
        通用输入（支持中文）
        """
        import pyperclip

        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
