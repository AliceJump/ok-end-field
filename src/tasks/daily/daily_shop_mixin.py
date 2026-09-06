import re

from src.data.FeatureList import FeatureList as fL
from src.data.lang import LangAccessor

# 优先商品模板标签 → 官方中文名。
# 模板标签（weapon_quota/orobertyl）直接打进日志不可读，先映射为游戏内官方名称
# （解包文本 assets/data/i18n_texts/*.json，key=e38d9b5bba114f81 / 8a3b3efb79c0131f）。
# 带动态值的日志统一走 tr+format：外层静态模板先经 tr 查表（msgid=稳定模板串进 ok.po），
# 内层已知静态文本值也过一层 tr，最后 .format 用已译值填充已译模板；
# 禁止 f-string 整句拼接——填充后的整句作 msgid 无法命中 po 条目，外语 UI 下不翻译。
_PRIORITY_ITEM_NAMES = {
    fL.weapon_quota.value: "武库配额",
    fL.orobertyl.value: "嵌晶玉",
}


class DailyShopFeature:
    # 类型提示：lang 等属性实际由 __getattr__ 转发到 self._task
    lang: LangAccessor
    CFG_BUY_CREDIT_SHOP = "⭐买信用商店"
    CFG_KEEP_CREDIT = "信用商店保留信用"
    CFG_CREDIT_SHOP_WARNING = "信用商店警告"

    def __init__(self, task):
        self._task = task
        task.default_config.update(
            {
                self.CFG_BUY_CREDIT_SHOP: True,
                self.CFG_KEEP_CREDIT: 300,
            }
        )
        task.config_description.update(
            {
                self.CFG_BUY_CREDIT_SHOP: (
                    "是否在「采购中心/信用交易所」采购。\n自动刷新 且 仅购买「武库配额」「嵌晶玉」。"
                ),
                self.CFG_KEEP_CREDIT: ("若剩余信用小于这个数值，则终止采购。"),
            }
        )
        self.refresh_count = 0
        self.refresh_cost_list = [80, 120, 160, 201]
        self.credit_good_search_box = None
        task.default_config_group.update(
            {
                self.CFG_BUY_CREDIT_SHOP: [self.CFG_KEEP_CREDIT],
            }
        )

    def __getattr__(self, name):
        return getattr(self._task, name)

    def refresh(self, sum_credit):
        if self.refresh_count >= len(self.refresh_cost_list):
            return False, sum_credit
        cost = self.refresh_cost_list[self.refresh_count]
        if sum_credit - cost > 210:
            if not self.back_shop():
                self.log_info("信用商店刷新中断：未能返回采购页面")
                return False, sum_credit
            self.log_info(
                self.tr("信用商店尝试刷新第{count}次，预计消耗信用: {cost}，当前信用: {credit}").format(
                    count=self.refresh_count + 1, cost=cost, credit=sum_credit
                )
            )
            shop_retry = 0
            while not self.wait_click_feature(
                feature=fL.credit_shop_refresh,
                time_out=1,
                raise_if_not_found=False,
                vertical_variance=0.005,
                horizontal_variance=0.01,
            ):
                if not self.back_shop():
                    self.log_info("信用商店刷新中断：未能返回采购页面")
                    return False, sum_credit
                else:
                    shop_retry += 1
                    if shop_retry >= 3:
                        self.log_info("信用商店刷新失败：连续3次未找到刷新按钮")
                        return False, sum_credit
            if not self.click_confirm():
                self.mark_task_failure("信用商店刷新失败：未找到确认按钮")
                return False, sum_credit
            sum_credit -= cost
            self.refresh_count += 1
            self.wait_ui_stable(refresh_interval=1)
            temp_sum_credit = self.detect_ticket_number()
            if temp_sum_credit:
                sum_credit = temp_sum_credit
            self.log_info(
                self.tr("信用商店刷新成功，消耗信用: {cost}，剩余信用: {credit}").format(cost=cost, credit=sum_credit)
            )
            return True, sum_credit
        return False, sum_credit

    def back_shop(self, max_retry=10):
        for _ in range(max_retry):
            if self.wait_feature(feature=fL.credit_shop_icon, raise_if_not_found=False, time_out=1):
                return True
            self.back()
        self.info_set(
            self.CFG_CREDIT_SHOP_WARNING, self.tr("返回采购页面失败，已重试{count}次").format(count=max_retry)
        )
        return False

    def get_cost(self):

        result = self.wait_ocr(
            match=re.compile(r"\d+"), box=self.box_of_screen(1510 / 1920, 750 / 1080, 1660 / 1920, 810 / 1080)
        )

        if result:
            for r in result:
                m = re.search(r"\d+", r.name)
                if m:
                    return int(m.group())
        return 0

    def _discount_near_priority(self, discount_box, priority_box):
        """折扣框与优先商品框距离过近时判定为误识别。

        比较折扣框左下角与优先商品框右上角的 xy 距离，
        两者都小于阈值时丢弃该折扣框，避免点击到优先商品上的误识别数字。
        """
        dx = abs(discount_box.x - (priority_box.x + priority_box.width))
        dy = abs((discount_box.y + discount_box.height) - priority_box.y)
        return dx < priority_box.width * 0.5 and dy < priority_box.height

    def buy_once(self, sum_credit):
        self.wait_ui_stable(refresh_interval=0.5)
        normal_results = []
        reserve_credit = self.config.get(self.CFG_KEEP_CREDIT, 300)
        self.log_info(
            self.tr("开始信用商店优先购买，当前信用: {credit}，保留信用: {reserve}").format(
                credit=sum_credit, reserve=reserve_credit
            )
        )
        if not self.back_shop():
            return False, sum_credit, False

        for search in (fL.weapon_quota, fL.orobertyl):
            r = self.find_feature(feature=search, box=self.credit_good_search_box)
            if r:
                normal_results.extend(r)

        discount_list = [99, 95]

        discount_results = self.wait_ocr(
            match=[re.compile(str(i)) for i in discount_list],
            box=self.box_of_screen(120 / self.width, 156 / self.height, 1815 / self.width, 211 / self.height),
            time_out=2,
        )

        if discount_results:
            filtered_discounts = []
            for discount in discount_results:
                if any(self._discount_near_priority(discount, priority) for priority in normal_results):
                    # 折扣框文本是运行时 OCR 结果，仅外层模板过 tr，值不进收集池
                    self.log_info(self.tr("丢弃与优先商品邻近的折扣框: {name}").format(name=discount.name))
                    continue
                filtered_discounts.append(discount)
            discount_results = filtered_discounts

        candidates = []
        candidates.extend((item, False) for item in normal_results)
        candidates.extend((item, True) for item in (discount_results or []))
        for idx, (item, is_discount_item) in enumerate(candidates, start=1):
            # 模板标签（weapon_quota/orobertyl）替换为官方中文名，保证日志可读；
            # 已知官方名是静态值，可安全过 tr 查表翻译（内层值翻译）；
            # 运行时未知名称不过 tr，避免污染 i18n 收集池。
            raw_name = getattr(item, "name", None)
            known_name = _PRIORITY_ITEM_NAMES.get(raw_name or "")
            if known_name:
                item_name = self.tr(known_name)
            else:
                item_name = raw_name or self.tr("未知商品#{idx}").format(idx=idx)
            self.log_info(
                self.tr("尝试购买优先商品: {name}，当前信用: {credit}").format(name=item_name, credit=sum_credit)
            )
            if not self.back_shop():
                self.info_set(self.CFG_CREDIT_SHOP_WARNING, "购买优先商品前未能返回采购页面")
                return False, sum_credit, False
            self.click(item)
            self.wait_ui_stable(refresh_interval=0.5)
            cost = self.get_cost()
            if cost <= 0:
                if is_discount_item:
                    self.log_info(
                        self.tr("商品: {name}，未识别到有效价格，折扣商品设置价格为10.000").format(name=item_name)
                    )
                    cost = 10
                else:
                    self.info_set(self.CFG_CREDIT_SHOP_WARNING, "购买优先商品前未能获取价格信息")
                    self.mark_task_failure(
                        self.tr("购买失败: {name}，原因: 未识别到有效价格且非折扣商品").format(name=item_name)
                    )
                    return False, sum_credit, False
            self.log_info(self.tr("商品价格识别成功: {name}，价格: {cost}").format(name=item_name, cost=cost))
            result = self.wait_click_feature(
                feature=fL.skip_dialog_confirm,
                box=self.box_of_screen(0.816, 0.788, 0.855, 0.841),
                time_out=4,
                raise_if_not_found=False,
            )
            if not result:
                self.log_info(
                    self.tr("购买流程中断: {name}，未找到确认/不足弹窗，尝试返回采购页").format(name=item_name)
                )
                if not self.back_shop():
                    return False, sum_credit, False
                if cost != 10:
                    self.info_set(self.CFG_CREDIT_SHOP_WARNING, "购买优先商品时信用不足")
                    self.mark_task_failure(
                        self.tr("购买失败: {name}，原因: 信用不足，当前信用: {credit}，价格: {cost}").format(
                            name=item_name, credit=sum_credit, cost=cost
                        )
                    )
                    self.back_shop()
                    return False, sum_credit, False
                return False, sum_credit, True
            self.wait_pop_up()
            sum_credit -= cost
            self.log_info(
                self.tr("购买成功: {name}，消耗信用: {cost}，剩余信用: {credit}").format(
                    name=item_name, cost=cost, credit=sum_credit
                )
            )
        if sum_credit <= reserve_credit:
            self.log_info(
                self.tr("信用降至保留阈值，停止优先购买，剩余信用: {credit}，阈值: {reserve}").format(
                    credit=sum_credit, reserve=reserve_credit
                )
            )
            return True, sum_credit, True
        return False, sum_credit, True

    def credit_shop(self):
        self.credit_good_search_box = self.box_of_screen(200 / 3840, 280 / 2160, 3620 / 3840, 1550 / 2160)
        self.refresh_count = 0
        self.press_key("f5")
        if not self.wait_click_ocr(
            match=self.lang.daily_shop_mixin.k_9a0004ef, time_out=7, box=self.box.top_right, recheck_time=1
        ):
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
                if sum_credit <= self.config.get(self.CFG_KEEP_CREDIT, 300):
                    return True
                else:
                    return self.buy_left(sum_credit)
        return True

    def buy_left(self, sum_credit):
        reserve_credit = self.config.get(self.CFG_KEEP_CREDIT, 300)
        self.log_info(
            self.tr("开始购买剩余可购商品，当前信用: {credit}，保留信用: {reserve}").format(
                credit=sum_credit, reserve=reserve_credit
            )
        )
        if not self.back_shop():
            return False
        results = self.find_feature(feature=fL.credit_can_buy, box=self.credit_good_search_box) or []
        for idx, item in enumerate(results, start=1):
            # 剩余商品名是运行时匹配结果，不过 tr 防污染收集池；兜底名为静态模板可过 tr
            item_name = getattr(item, "name", None) or self.tr("未知商品#{idx}").format(idx=idx)
            self.log_info(
                self.tr("尝试购买剩余商品: {name}，当前信用: {credit}").format(name=item_name, credit=sum_credit)
            )
            if not self.back_shop():
                self.info_set(self.CFG_CREDIT_SHOP_WARNING, "购买剩余商品前未能返回采购页面")
                return False
            self.click(item)
            self.wait_ui_stable(refresh_interval=0.5)
            cost = self.get_cost()
            if cost <= 0:
                self.log_info(self.tr("跳过商品: {name}，未识别到有效价格").format(name=item_name))
                continue
            self.log_info(self.tr("商品价格识别成功: {name}，价格: {cost}").format(name=item_name, cost=cost))
            result = self.wait_click_feature(
                feature=fL.skip_dialog_confirm,
                box=self.box_of_screen(0.816, 0.788, 0.855, 0.841),
                time_out=4,
                raise_if_not_found=False,
            )
            if not result:
                self.log_info(
                    self.tr("购买流程中断: {name}，未找到确认/不足弹窗，尝试返回采购页").format(name=item_name)
                )
                self.back_shop()
                return False
            self.wait_pop_up()
            sum_credit -= cost
            self.log_info(
                self.tr("购买成功: {name}，消耗信用: {cost}，剩余信用: {credit}").format(
                    name=item_name, cost=cost, credit=sum_credit
                )
            )
            if sum_credit <= reserve_credit:
                self.log_info(
                    self.tr("信用降至保留阈值，停止购买剩余商品，剩余信用: {credit}，阈值: {reserve}").format(
                        credit=sum_credit, reserve=reserve_credit
                    )
                )
                return True
        return True
