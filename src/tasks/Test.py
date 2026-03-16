import re

from src.tasks.BaseEfTask import BaseEfTask
from src.tasks.daily.daily_liaison_mixin import DailyLiaisonMixin
from src.data.FeatureList import FeatureList as fL
from src.tasks.mixin.common import Common
from src.tasks.mixin.battle_mixin import BattleMixin
from src.tasks.AutoCombatLogic import AutoCombatLogic


class Test(BattleMixin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.refresh_count = 0
        self.refresh_cost_list = [80, 120, 160, 200]
        self.credit_good_search_box = None

    def run(self):
        while True:
            AutoCombatLogic(self).run()

    def refresh(self, sum_credit):
        if self.refresh_count >= len(self.refresh_cost_list):
            return False, sum_credit
        cost = self.refresh_cost_list[self.refresh_count]
        if sum_credit - cost > 200:
            if not self.wait_click_ocr(match=re.compile("刷新"), time_out=5, box=self.box.bottom_right, after_sleep=2):
                return False, sum_credit
            if not self.wait_click_ocr(match=re.compile("确认"), time_out=5, box=self.box.center, after_sleep=2):
                return False, sum_credit
            sum_credit -= cost
            self.refresh_count += 1
            return True, sum_credit
        return False, sum_credit

    def back_shop(self):
        while not self.wait_ocr(match=re.compile("采购"), time_out=1):
            self.back(after_sleep=2)

    def get_cost(self):

        result = self.wait_ocr(
            match=re.compile(r"\d+"),
            box=self.box_of_screen(1510 / 1920, 750 / 1080, 1660 / 1920, 800 / 1080)
        )

        if result:
            for r in result:
                m = re.search(r"\d+", r.name)
                if m:
                    return int(m.group())

        self.back_shop()
        return 0

    def buy_once(self, sum_credit):
        search_list = [fL.weapon_quota, fL.orobertyl, fL.discount_95_percent_icon]
        results = []
        for search in search_list:
            results.extend(self.find_feature(feature_name=search, box=self.credit_good_search_box))
        for result in results:
            self.click(result, after_sleep=2)
            cost = self.get_cost()
            if cost <= 0:
                continue
            if not self.wait_click_ocr(match=re.compile("确认"), after_sleep=2, time_out=4, box=self.box.bottom_right):
                self.back_shop()
                return False, sum_credit, False
            self.wait_pop_up()
            sum_credit -= cost
        if sum_credit <= 300:
            return True, sum_credit, True
        return False, sum_credit, True

    def credit_shop(self):
        self.credit_good_search_box = self.box_of_screen(200 / 3840, 280 / 2160, 3620 / 3840, 1550 / 2160)
        self.press_key("f5", after_sleep=2)
        if not self.wait_click_ocr(match=re.compile("信用"), time_out=5, box=self.box.top_right, after_sleep=2):
            return False
        sum_credit = self.detect_ticket_number()
        while sum_credit > 0:
            finish, sum_credit, success = self.buy_once(sum_credit)
            if finish:
                return True
            if not success:
                return False
            success, sum_credit = self.refresh(sum_credit)
            if not success:
                if sum_credit <= 300:
                    return True
                else:
                    return self.buy_left(sum_credit)
        return True

    def buy_left(self, sum_credit):
        results = self.find_feature(feature_name=fL.credit_can_buy, box=self.credit_good_search_box)
        for result in results:
            self.click(result, after_sleep=2)
            cost = self.get_cost()
            if cost <= 0:
                continue
            if not self.wait_click_ocr(match=re.compile("确认"), after_sleep=2, time_out=4, box=self.box.bottom_right):
                self.back_shop()
                return False
            self.wait_pop_up()
            sum_credit -= cost
            if sum_credit <= 300:
                return True
        return True
