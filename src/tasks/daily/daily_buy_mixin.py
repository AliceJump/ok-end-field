import re

from src.data.world_map import areas_list
from src.tasks.sequence_parser import parse_sequence
from src.tasks.mixin.common import Common


class DailyBuyMixin(Common):    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config.update({
            "⭐买物资": False,
            "购物白名单": "",
            "是否买礼物": True,
        })
        self.config_description.update({
            "⭐买物资": (
                "是否在「地区建设/物资调度/稳定物资需求」中通过调度券购买物资。\n"
                "依次购买「日用消耗」「工业货品」「人文物产」首行某个物品。"
            ),
            "购物白名单": (
                "默认留空，表示购买「日用消耗」「工业货品」首行首个物资。\n"
                "更多用法参见 ./docs/日常任务.md > 买物资 。"
            ),
            "是否买礼物": (
                "是否购买「人文物产」。"
            ),
        })


    def buy_staple_goods(self):
        self.info_set("current_task", "buy_staple_goods")
        self.log_info("开始买物资任务")
        #
        pl = [re.compile(i) for i in parse_sequence(self.config.get("购物白名单", ""))]
        #
        for area in areas_list:
            self.ensure_main()
            self.log_info(f"进入区域: {area}")
            self.to_model_area(area, "物资调度")
            #
            self.click_relative(100/3840, 464/2160, after_sleep=2)
            self.log_info("购买「日用消耗」")
            self.buy(pattern_list=pl)
            #
            self.click_relative(100/3840, 718/2160, after_sleep=2)
            self.log_info("购买「工业货品」")
            self.buy(pattern_list=pl)
            #
            if self.config.get("是否买礼物", True):
                self.click_relative(100/3840, 972/2160, after_sleep=2)
                self.log_info("购买「人文物产」")
                self.buy()


    def buy(self, pattern_list=[]):
        if len(pattern_list) <= 0:
            self.click_relative(0.1, 0.4, after_sleep=2)
            self.log_info("未指定白名单，选择首行首个")
        else:
            box_list = self.ocr(x=200/3840, y=520/2160, to_x=3680/3840, to_y=1140/2160, match=pattern_list);
            if len(box_list) <= 0:
                self.log_info("未找到白名单货品，跳过")
                return
            self.click(box_list[0], after_sleep=2)
            self.log_info(f"已选定货品：{box_list[0].name}")
        if not self.plus_max():
            self.send_key("esc", after_sleep=2)  # 确认使用send_key：esc为系统通用退出键，非游戏可配置热键
            self.log_info("调度券不足，跳过")
            return
        self.click_relative(0.8, 0.8, after_sleep=2)
        self.wait_pop_up(after_sleep=2)
        self.log_info("已购买")
