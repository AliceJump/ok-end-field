import re
import time
from typing import List

import win32gui

from src.data.FeatureList import FeatureList as fL
from src.data.world_map import areas_list
from src.image.hsv_config import HSVRange as hR
from src.tasks.BaseEfTask import back_window
from src.tasks.mixin.common import GoodsInfo
from src.tasks.mixin.navigation_mixin import NavigationMixin
from src.tasks.mixin.common import Common


class DailyTradeMixin(NavigationMixin, Common):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        #
        self.default_config.update({
            "⭐买卖货": True,
            "只买不卖": False,
        })
        buy_sell_dict = dict()
        buy_sell_desc_dict = dict()
        for area in areas_list:
            buy_sell_dict[f"{area}买入价"] = 900
            buy_sell_dict[f"{area}卖出价"] = 4500
            buy_sell_dict[area] = True
            #
            buy_sell_desc_dict[f"{area}买入价"] = f"{area}物资买入价格上限，超出则拒绝买入。若必买，则设大数。"
            buy_sell_desc_dict[f"{area}卖出价"] = f"{area}物资卖出价格下限，不足则拒绝卖出。若必卖，则设1。"
            buy_sell_desc_dict[area] = f"是否启用「地区建设/{area}物资调度/弹性需求物资」交易。"
        self.default_config.update(buy_sell_dict)
        self.config_description.update({
            "⭐买卖货": "是否在「弹性需求物资」与好友交易赚取调度券。自动选择利润最高的方案，然后用价格上下限判定是否交易。（除非可购买数量即将溢出，这种情况下必定交易。）",
            "只买不卖": "启用后将只进行购买操作，不进行出售操作。",
            **buy_sell_desc_dict,
        })

    def collect_market_goods_info(self):
        def ocr_stock_quantity() -> int:
            stock_piece = self.ocr(
                match=re.compile(r"^\d+$"),
                box=self.box_of_screen(353 / 1920, 607 / 1080, 613 / 1920, 635 / 1080),
                log=True,
            )
            if stock_piece and stock_piece[0].name.isdigit():
                return int(stock_piece[0].name)
            return 0

        test_goods_re = re.compile("货组")
        market_text_y = None
        market_text = self.wait_ocr(match=re.compile("市场"), box=self.box.left)
        if not market_text:
            self.log_info("未识别到市场文字")
            return [], None

        market_text_y = market_text[0].y
        self.next_frame()

        # =========================
        # ✅ 只买模式：直接OCR扫描
        # =========================
        if self.config.get("只买不卖", False):
            results = self.ocr(
                match=re.compile(r"\d+"),
                box=self.box_of_screen(0, market_text_y / self.height, 1, 1),
                frame_processor=self.make_hsv_isolator(hR.DARK_GRAY_TEXT),
                log=True,       
            )

            candidates = []
            for res in results:
                match = re.search(r"\d+", res.name)
                if not match:
                    continue

                num = int(match.group())

                # 过滤无效价格（按你的规则）
                if num > 100:
                    candidates.append(
                        GoodsInfo(
                            good_name="",
                            stock_quantity=0,
                            good_price=num,
                            name_box=res,
                            friend_name_box=None,
                            friend_price=0,
                        )
                    )

            if not candidates:
                self.log_info("未找到有效价格")
                return None, market_text_y

            min_item = min(candidates, key=lambda x: x.good_price)

            self.log_info(f"最低价格: {min_item.good_price}")

            return min_item, market_text_y

        # =========================
        # ✅ 正常模式：逐个点击采集
        # =========================
        goods = self.ocr(match=test_goods_re, log=True, box=self.box_of_screen(0, market_text_y / self.height, 1, 1))

        sum_good_info = []
        for good in goods:
            self.click(good)
            self.wait_ui_stable(refresh_interval=1)
            self.next_frame()
            stock_quantity = ocr_stock_quantity()
            good_piece = self.ocr(
                match=re.compile(r"^\d+$"),
                box=self.box_of_screen(1527 / 1920, 324 / 1080, 1600 / 1920, 400 / 1080),
                frame_processor=self.make_hsv_isolator(hR.DARK_GRAY_TEXT),
                log=True,
            )
            if not good_piece:
                good_piece = self.ocr(
                    match=re.compile(r"^\d+$"),
                    box=self.box_of_screen(1527 / 1920, 324 / 1080, 1600 / 1920, 400 / 1080),
                    log=True,
                )

            self.wait_click_ocr(match=re.compile("查看好友价格"), box=self.box.bottom_right)

            self.wait_ui_stable(refresh_interval=1)
            self.next_frame()

            friend_name_piece = self.ocr(
                match=re.compile(r"\d+$"),
                box=self.box_of_screen(800 / 1920, 430 / 1080, 1270 / 1920, 490 / 1080),
                frame_processor=self.make_hsv_isolator(hR.DARK_GRAY_TEXT),
                log=True,
            )

            if not good_piece:
                good_piece = []
            if not friend_name_piece:
                friend_name_piece = []

            self.log_info(
                f"货物名称: {good.name}, "
                f"存货数量: {stock_quantity}, "
                f"价格: {[i.name for i in good_piece]}, "
                f"价格来源人和价格: {[i.name for i in friend_name_piece]}"
            )

            sum_good_info.append(
                {
                    "good": good,
                    "good_piece": good_piece,
                    "friend_name_piece": friend_name_piece,
                    "stock_quantity": stock_quantity,
                }
            )

            # 返回地区建设
            back_to_area_deadline = time.time() + 20
            while not self.wait_ocr(match=re.compile("地区建设"), box=self.box.top_left, time_out=1):
                if time.time() > back_to_area_deadline:
                    self.log_info("等待返回 '地区建设' 界面超时，结束当前市场采集")
                    return sum_good_info, market_text_y
                self.back(after_sleep=0.5)

        return sum_good_info, market_text_y

    def analyze_goods_info(
            self, good_infos: List[dict], buy_price: int, sell_price: int
    ):
        processed_goods: List[GoodsInfo] = []

        for good_info in good_infos:
            try:
                name_box = good_info.get("good")
                good_piece = good_info.get("good_piece", [])
                friend_name_piece = good_info.get("friend_name_piece", [])
                stock_quantity = good_info.get("stock_quantity", 0)

                if not name_box or not good_piece:
                    raise ValueError("缺少货物名称或价格信息")

                friend_name_box = friend_name_piece[0] if len(friend_name_piece) > 0 else None
                good_name = name_box.name
                good_price = int(good_piece[0].name)

                friend_price = (
                    int(friend_name_piece[1].name)
                    if len(friend_name_piece) > 1 and friend_name_piece[1].name.isdigit()
                    else None
                )

                processed_goods.append(
                    GoodsInfo(
                        good_name=good_name,
                        good_price=good_price,
                        friend_price=friend_price,
                        stock_quantity=stock_quantity,
                        name_box=name_box,
                        friend_name_box=friend_name_box,
                    )
                )

            except Exception as e:
                self.log_error(f"解析货物失败: {good_info} | 错误: {e}")

        if not processed_goods:
            self.log_info("没有有效货物数据")
            return None, [], False

        self.log_info("===== 当前货物列表 =====")
        for good in processed_goods:
            self.log_info(
                f"[货物] 名称:{good.good_name:<10} "
                f"存货:{good.stock_quantity:>3} "
                f"买价:{good.good_price:>6} "
                f"卖价:{str(good.friend_price):>6} "
            )

        buy_good = min(processed_goods, key=lambda x: x.good_price)

        self.log_info(
            f"推荐购买 | 名称:{buy_good.good_name} " f"| 价格:{buy_good.good_price}"
        )

        try:
            sell_goods = [
                good for good in processed_goods if good.friend_price > sell_price
            ]
        except TypeError:
            self.log_error("好友价格数据异常，无法进行出售分析")
            sell_goods = []

        if sell_goods:
            self.log_info("===== 推荐出售列表 =====")
            for good in sell_goods:
                self.log_info(
                    f"推荐出售 | 名称:{good.good_name} " f"| 卖价:{good.friend_price}"
                )
        else:
            self.log_info("没有符合出售条件的货物")

        if buy_good.good_price < buy_price:
            self.log_info(
                f"满足购买条件 | 实际价格:{buy_good.good_price} "
                f"< 设定上限:{buy_price}"
            )
            return buy_good, sell_goods, True
        else:
            self.log_info(
                f"不满足购买条件 | 实际价格:{buy_good.good_price} "
                f">= 设定上限:{buy_price}"
            )
            return buy_good, sell_goods, False

    def navigate_to_friend_exchange(self):
        self.log_info("前往物资调度终端")
        self.ensure_map(addtional_match=re.compile("中枢"))
        if not self.start_tracking_and_align_target(
                fL.market_dispatch_terminal, fL.market_dispatch_terminal_out
        ):
            return False
        result = self.navigate_until_target(
            target_ocr_pattern=re.compile("物资调度终端"),
            nav_feature_name=fL.market_dispatch_terminal_out,
            time_out=200,
        )

        if result:
            self.press_key('f')
            self.wait_ui_stable(refresh_interval=1)
        return result

    def buy_sell(self):
        for area in areas_list:
            if not self.config.get(area, False):
                self.log_info(f"跳过{area}，因为配置中未启用")
                continue
            self.ensure_main()
            self.log_info(f"前往{area}")
            self.to_model_area(area, "物资调度")
            self.wait_ui_stable(refresh_interval=1)
            self.wait_click_ocr(
                match=re.compile("弹性"), box=self.box.top, after_sleep=2
            )
            result = self.find_feature(fL.market_good_icon)
            if not result:
                self.log_info("未找到货物")
                continue
            self.click(result, after_sleep=1)
            good_infos, _ = self.collect_market_goods_info()
            buy_price = self.config.get(f"{area}买入价", 0)
            sell_price = self.config.get(f"{area}卖出价", 0)
            if not (buy_price and sell_price):
                self.log_info("未找到买入价或卖出价")
                continue
            if self.config.get("只买不卖", False):
                buy_good=good_infos
                sell_goods=[]
                can_buy = buy_good and buy_good.good_price < buy_price
            else:
                buy_good, sell_goods, can_buy = self.analyze_goods_info(
                    good_infos, buy_price, sell_price
                )
            puls_minus_box = self.box_of_screen(0.36, 0.6630, 0.592, 0.8019)
            if buy_good:
                if not can_buy:
                    if self.wait_ocr(
                            match=[re.compile("即将"), re.compile("溢出")],
                            box=self.box.top_left,
                            time_out=3,
                    ):
                        can_buy = True
                if can_buy:
                    back_to_area_deadline = time.time() + 20
                    while not self.wait_ocr(
                            match=re.compile("地区建设"),
                            box=self.box.top_left,
                            time_out=1,
                    ):
                        if time.time() > back_to_area_deadline:
                            self.log_info(
                                "等待返回 '地区建设' 界面超时，结束买卖货任务"
                            )
                            return False
                        self.back(after_sleep=0.5)
                    self.click(buy_good.name_box)
                    self.wait_ui_stable(refresh_interval=1)
                    if self.plus_max():
                        self.wait_click_ocr(
                            match=re.compile("购买"),
                            box=self.box.bottom_right
                        )
                        self.wait_pop_up()
                        for sg in sell_goods:
                            if sg.good_name == buy_good.good_name:
                                sg.stock_quantity += 1
                                self.log_info(
                                    f"{sg.good_name} 本次已购买，存货数量更新为 {sg.stock_quantity}"
                                )
                                break
                    else:
                        self.log_info("未找到加号按钮，无法购买")

            for sell_good in sell_goods:
                if sell_good.stock_quantity <= 0:
                    self.log_info(f"跳过出售 {sell_good.good_name}，存货数量<=0")
                    continue
                back_to_area_deadline = time.time() + 20
                while not self.wait_ocr(
                        match=re.compile("地区建设"), box=self.box.top_left, time_out=1
                ):
                    if time.time() > back_to_area_deadline:
                        self.log_info("等待返回 '地区建设' 界面超时，结束买卖货任务")
                        return False
                    self.back(after_sleep=0.5)
                if not (self.wait_click_ocr(match=re.compile(sell_good.name_box.name[-3:]), log=True) or
                        self.wait_click_ocr(match=re.compile(sell_good.good_name[:3]), log=True)):
                    self.log_info("未找到卖出货物，无法出售")
                    continue
                self.wait_ui_stable(refresh_interval=1)
                self.wait_click_ocr(
                    match=re.compile("查看好友价格"),
                    box=self.box.bottom_right,
                )
                self.wait_ui_stable(refresh_interval=1)
                try:
                    c_y = (
                            sell_good.friend_name_box.y + sell_good.friend_name_box.height // 2
                    )
                    c_x = sell_good.friend_name_box.x - int((808 - 737) / 1920 * self.width)
                except AttributeError:
                    self.log_info("未找到好友价格，无法出售")
                    continue
                self.click(c_x, c_y, after_sleep=1)
                go_friend_deadline = time.time() + 20
                while not self.wait_click_ocr(
                        match=re.compile("前往"), box=self.box.center
                ):
                    if time.time() > go_friend_deadline:
                        self.log_info("等待 '前往' 按钮超时，跳过该货物出售")
                        break
                    self.click(c_x, c_y, after_sleep=1,move_back=False)
                if time.time() > go_friend_deadline:
                    continue
                if not self.ensure_in_friend_boat():
                    self.log_info("未进入好友船")
                    return False
                prev = win32gui.GetForegroundWindow()
                self.navigate_to_friend_exchange()
                back_window(prev)
                self.wait_click_ocr(match=re.compile(area), box=self.box.top, after_sleep=2)
                if not (self.wait_click_ocr(match=re.compile(sell_good.name_box.name[-3:])) or
                        self.wait_click_ocr(match=re.compile(sell_good.good_name[:3]))):
                    self.log_info("未找到卖出货物，无法出售")
                    continue
                self.wait_ui_stable(refresh_interval=1)
                if self.plus_max():
                    self.wait_click_ocr(
                        match=re.compile("出售"),
                        box=self.box.bottom_right,
                        after_sleep=2,
                    )
                    self.wait_pop_up()
                else:
                    self.log_info("未找到加号按钮，无法出售")

        return True
