import re

from src.data.FeatureList import FeatureList as fL
from src.tasks.mixin.common import Common


class DailyShopMixin(Common):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config.update({
            "⭐买信用商店": True,
            "信用商店保留信用": 300,
        })
        self.config_description.update({
            "⭐买信用商店": (
                "是否在「采购中心/信用交易所」采购。\n"
                "自动刷新 且 仅购买「武库配额」「嵌晶玉」。"
            ),
            "信用商店保留信用": (
                "若剩余信用小于这个数值，则终止采购。"
            ),
        })
        self.refresh_count = 0
        self.refresh_cost_list = [80, 120, 160, 201]
        self.credit_good_search_box = None

    def refresh(self, sum_credit):
        if self.refresh_count >= len(self.refresh_cost_list):
            return False, sum_credit
        cost = self.refresh_cost_list[self.refresh_count]
        if sum_credit - cost > 210:
            if not self.back_shop():
                self.log_info("信用商店刷新中断：未能返回采购页面")
                return False, sum_credit
            self.log_info(f"信用商店尝试刷新第{self.refresh_count + 1}次，预计消耗信用: {cost}，当前信用: {sum_credit}")
            shop_retry = 0
            while not self.wait_click_ocr(match=re.compile("刷新"), time_out=1, box=self.box_of_screen(2/3, 0.5, 1, 1)):
                if self.wait_ocr(match=re.compile("购买"),box=self.box.top_left, time_out=1):
                    self.back(after_sleep=1)
                elif not self.back_shop():
                    self.log_info("信用商店刷新中断：未能返回采购页面")
                    return False, sum_credit
                else:
                    shop_retry += 1
                    if shop_retry >= 3:
                        return True, sum_credit
            if not self.wait_click_ocr(match=re.compile("确认"), time_out=5, box=self.box.bottom_right):
                self.log_info("信用商店刷新失败：未找到确认按钮")
                return False, sum_credit
            sum_credit -= cost
            self.refresh_count += 1
            self.wait_ui_stable(refresh_interval=1)
            self.log_info(f"信用商店刷新成功，消耗信用: {cost}，剩余信用: {sum_credit}")
            return True, sum_credit
        return False, sum_credit

    def back_shop(self, max_retry=10):
        for _ in range(max_retry):
            if self.wait_ocr(match=re.compile("采购"), time_out=1):
                return True
            self.back(after_sleep=1)
        self.info_set("信用商店警告", f"返回采购页面失败，已重试{max_retry}次")
        return False

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

        if not self.back_shop():
            self.info_set("信用商店警告", "未能返回采购页面，跳过当前商品")
        return 0

    def buy_once(self, sum_credit):
        self.wait_ui_stable(refresh_interval=0.5)
        results = []
        reserve_credit = self.config.get('信用商店保留信用', 300)
        self.log_info(f"开始信用商店优先购买，当前信用: {sum_credit}，保留信用: {reserve_credit}")
        if not self.back_shop():
            return False, sum_credit, False

        for search in (fL.weapon_quota, fL.orobertyl):
            r = self.find_feature(feature_name=search, box=self.credit_good_search_box)
            if r:
                results.extend(r)

        discount_list = [99, 95]

        result = self.wait_ocr(
            match=[re.compile(str(i)) for i in discount_list],
            box=self.box_of_screen(
                120 / self.width, 156 / self.height,
                1815 / self.width, 211 / self.height
            ),
            time_out=2
        )

        results.extend(result or [])
        for idx, item in enumerate(results, start=1):
            item_name = getattr(item, "name", None) or f"未知商品#{idx}"
            self.log_info(f"尝试购买优先商品: {item_name}，当前信用: {sum_credit}")
            if not self.back_shop():
                self.info_set("信用商店警告", "购买优先商品前未能返回采购页面")
                return False, sum_credit, False
            self.click(item)
            self.wait_ui_stable(refresh_interval=0.5)
            cost = self.get_cost()
            if cost <= 0:
                self.log_info(f"跳过商品: {item_name}，未识别到有效价格")
                continue
            self.log_info(f"商品价格识别成功: {item_name}，价格: {cost}")
            result = self.wait_click_ocr(
                match=[re.compile("确认"), re.compile("不足")], time_out=4, box=self.box.bottom_right
            )
            if not result:
                self.log_info(f"购买流程中断: {item_name}，未找到确认/不足弹窗，尝试返回采购页")
                if not self.back_shop():
                    return False, sum_credit, False
                return True, sum_credit, True
            else:
                if "不足" in result[0].name:
                    self.info_set("信用商店警告", "购买优先商品时信用不足")
                    self.log_info(f"购买失败: {item_name}，原因: 信用不足，当前信用: {sum_credit}，价格: {cost}")
                    self.back_shop()
                    return False, sum_credit, False
            self.wait_pop_up(after_sleep=1)
            sum_credit -= cost
            self.log_info(f"购买成功: {item_name}，消耗信用: {cost}，剩余信用: {sum_credit}")
        if sum_credit <= reserve_credit:
            self.log_info(f"信用降至保留阈值，停止优先购买，剩余信用: {sum_credit}，阈值: {reserve_credit}")
            return True, sum_credit, True
        return False, sum_credit, True

    def credit_shop(self):
        self.credit_good_search_box = self.box_of_screen(200 / 3840, 280 / 2160, 3620 / 3840, 1550 / 2160)
        self.refresh_count = 0
        self.press_key("f5")
        if not self.wait_click_ocr(match=re.compile("信用"), time_out=7, box=self.box.top_right, recheck_time=1):
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
                if sum_credit <=self.config.get('信用商店保留信用',300):
                    return True
                else:
                    return self.buy_left(sum_credit)
        return True

    def buy_left(self, sum_credit):
        reserve_credit = self.config.get('信用商店保留信用', 300)
        self.log_info(f"开始购买剩余可购商品，当前信用: {sum_credit}，保留信用: {reserve_credit}")
        if not self.back_shop():
            return False
        results = self.find_feature(feature_name=fL.credit_can_buy, box=self.credit_good_search_box) or []
        for idx, item in enumerate(results, start=1):
            item_name = getattr(item, "name", None) or f"未知商品#{idx}"
            self.log_info(f"尝试购买剩余商品: {item_name}，当前信用: {sum_credit}")
            if not self.back_shop():
                self.info_set("信用商店警告", "购买剩余商品前未能返回采购页面")
                return False
            self.click(item)
            self.wait_ui_stable(refresh_interval=0.5)
            cost = self.get_cost()
            if cost <= 0:
                self.log_info(f"跳过商品: {item_name}，未识别到有效价格")
                continue
            self.log_info(f"商品价格识别成功: {item_name}，价格: {cost}")
            result = self.wait_click_ocr(
                match=[re.compile("确认"), re.compile("不足")], time_out=4, box=self.box.bottom_right
            )
            if not result:
                self.log_info(f"购买流程中断: {item_name}，未找到确认/不足弹窗，尝试返回采购页")
                self.back_shop()
                return False
            else:
                if "不足" in result[0].name:
                    self.info_set("信用商店警告", "购买剩余商品时信用不足")
                    self.log_info(f"购买失败: {item_name}，原因: 信用不足，当前信用: {sum_credit}，价格: {cost}")
                    self.back_shop()
                    return True
            self.wait_pop_up(after_sleep=1)
            sum_credit -= cost
            self.log_info(f"购买成功: {item_name}，消耗信用: {cost}，剩余信用: {sum_credit}")
            if sum_credit <= reserve_credit:
                self.log_info(f"信用降至保留阈值，停止购买剩余商品，剩余信用: {sum_credit}，阈值: {reserve_credit}")
                return True
        return True
