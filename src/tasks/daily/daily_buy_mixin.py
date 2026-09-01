import re

from src.data.FeatureList import FeatureList as fL
from src.data.lang import LangAccessor
from src.data.world_map import areas_list


class DailyBuyFeature:
    # 类型提示：lang 等属性实际由 __getattr__ 转发到 self._task
    lang: LangAccessor
    CFG_SHOP_WHITELIST = "购物白名单"
    CFG_BUY_GIFT = "是否买礼物"

    def __init__(self, task):
        self._task = task
        task.default_config.update(
            {
                self.CFG_SHOP_WHITELIST: [],
                self.CFG_BUY_GIFT: True,
            }
        )
        task.config_description.update(
            {
                self.CFG_SHOP_WHITELIST: (
                    "默认留空，表示购买「日用消耗」「工业货品」「人文物产」首行首个物资。\n"
                    "更多用法参见 ./docs/日常任务.md > 买物资 。"
                ),
                self.CFG_BUY_GIFT: ("是否购买「人文物产」（同样应用购物白名单序列）。"),
            }
        )
        task.default_config_group.update(
            {
                "⭐买物资": [self.CFG_SHOP_WHITELIST, self.CFG_BUY_GIFT],
            }
        )

    def __getattr__(self, name):
        return getattr(self._task, name)

    def buy_staple_goods(self, target_areas=None, keep_area_context=False):
        self.info_set("current_task", "buy_staple_goods")
        self.log_info("开始买物资任务")
        #
        pl = [re.compile(i) for i in self.config.get(self.CFG_SHOP_WHITELIST, [])]
        #
        if target_areas is None:
            target_areas = areas_list
        for area in target_areas:
            if not keep_area_context:
                self.ensure_main()
            # 区域名是项目静态常量（po 已有正式 msgid），文本值内层过 tr
            self.log_info(self.tr("进入区域: {area}").format(area=self.tr(area)))
            #
            self.wait_click_ocr(
                match=self.lang.daily_buy_mixin.stable_materials_tab,
                box=self.box.left,
                time_out=5,
                after_sleep=0.5,
            )
            self.wait_ui_stable(refresh_interval=0.2)
            self.log_info("购买「日用消耗」")
            self.buy(pattern_list=pl)
            #
            self.click_relative(100 / 3840, 718 / 2160)
            self.wait_ui_stable(refresh_interval=0.2)
            self.log_info("购买「工业货品」")
            self.buy(pattern_list=pl)
            #
            if self.config.get(self.CFG_BUY_GIFT, True):
                self.click_relative(100 / 3840, 972 / 2160)
                self.wait_ui_stable(refresh_interval=0.2)
                self.log_info("购买「人文物产」")
                self.buy(pattern_list=pl)

        return True

    def buy(self, pattern_list=[]):
        good_list = [None]
        if len(pattern_list) > 0:
            good_list = self.ocr(x=200 / 3840, y=520 / 2160, to_x=3680 / 3840, to_y=1140 / 2160, match=pattern_list)
            if len(good_list) <= 0:
                self.log_info("未找到白名单货品，跳过")
                return
        for good in good_list:
            self.log_info("找到白名单货品点击购买")
            if good:
                self.click(good)
            else:
                self.click_relative(0.1, 0.4)
                self.log_info("未指定白名单，选择首行首个")
            can_buy = self.wait_feature(
                feature=fL.skip_dialog_confirm,
                box=self.box_of_screen(0.825, 0.793, 0.851, 0.843),
                time_out=2,
                raise_if_not_found=False,
            )
            if can_buy:
                self.plus_max()
                self.click(can_buy)
                self.wait_pop_up()
            else:
                self.back()
                self.log_info("调度券不足，跳过")
            self.log_info("已购买")
