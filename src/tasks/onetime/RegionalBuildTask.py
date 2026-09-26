import re

from qfluentwidgets import FluentIcon

from src.data.FeatureList import FeatureList as fL
from src.data.world_map import areas_list, goods_dict, outpost_dict
from src.data.world_map_utils import (
    get_area_by_outpost_name,
    get_goods_by_outpost_name,
    get_world_map_matcher,
    get_world_map_text,
)
from src.icons import Icons
from src.image.hsv_config import HSVRange as hR
from src.tasks.mixin.common import Common, GoodsInfo
from src.tasks.mixin.map_mixin import MapMixin
from src.tasks.mixin.zip_line_mixin import ZipLineMixin


def _edit_distance(left: str, right: str) -> int:
    """Levenshtein 距离：插入、删除、替换一个字符的代价均为 1。"""
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, 1):
        current = [i]
        for j, right_char in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (left_char != right_char)))
        previous = current
    return previous[-1]


_DIGITS_ONLY_RE = re.compile(r"^\d+$")


class RegionalBuildTask(Common, MapMixin, ZipLineMixin):
    """地区建设子任务：据点兑换、买卖货与买物资的按地区编排，日常任务经 DailyFeature 接入。"""

    OPTIONS = ["据点兑换", "买物资", "买卖货"]
    DEFAULT_OPTIONS = ["据点兑换", "买卖货"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "地区建设"
        self.icon = Icons.Navigation
        self.group_name = "日常任务"
        self.group_icon = FluentIcon.CALENDAR
        self.description = "按地区执行据点兑换、买卖货与买物资。"
        self.support_multi_account = True
        # 标记买卖货流程中「买物资」回调是否真的被触发（buy_sell 可能跳过回调）
        self._buy_ran = False
        self.default_config.update(
            {
                "⭐地区建设": self.DEFAULT_OPTIONS,
                "交易货品优先序列": [],
                "据点兑换仅购买优先商品": False,
                "只买不卖": False,
                "购物白名单": [],
                "是否买礼物": True,
            }
        )
        buy_sell_dict = {}
        buy_sell_desc_dict = {}
        for area in areas_list:
            buy_sell_dict[f"{area}买入价"] = 900
            buy_sell_dict[f"{area}卖出价"] = 4500
            buy_sell_dict[area] = True
            buy_sell_desc_dict[f"{area}买入价"] = f"{area}物资买入价格上限，超出则拒绝买入。若必买，则设大数。"
            buy_sell_desc_dict[f"{area}卖出价"] = f"{area}物资卖出价格下限，不足则拒绝卖出。若必卖，则设1。"
            buy_sell_desc_dict[area] = f"是否启用「地区建设/{area}物资调度/弹性需求物资」交易。"
        self.default_config.update(buy_sell_dict)
        self.config_description.update(
            {
                "⭐地区建设": (
                    "按地区执行所选操作：先据点兑换，再执行买卖货的买；启用买物资时，买完后切换到稳定物资需求购买，最后切回弹性需求物资执行卖。"
                ),
                "交易货品优先序列": (
                    "默认留空，交易货品顺序随机。\n更多用法参见 ./docs/日常任务.md > 优先货品交易序列 。"
                ),
                "据点兑换仅购买优先商品": (
                    "启用后，仅当「交易货品优先序列」不为空时，据点兑换才只购买其中的商品；序列为空时按原逻辑兑换。"
                ),
                "只买不卖": ("启用后将只进行购买操作，不进行出售操作。"),
                "购物白名单": (
                    "默认留空，表示购买「日用消耗」「工业货品」「人文物产」首行首个物资。\n"
                    "更多用法参见 ./docs/日常任务.md > 买物资 。"
                ),
                "是否买礼物": ("是否购买「人文物产」（同样应用购物白名单序列）。"),
                **buy_sell_desc_dict,
            }
        )
        self.config_type["⭐地区建设"] = {
            "type": "multi_selection",
            "options": self.OPTIONS,
        }
        all_goods = []
        for goods_list in goods_dict.values():
            all_goods.extend(goods_list)
        self.config_type["交易货品优先序列"] = {
            "options_available": all_goods,
            "allow_duplication": False,
        }

    def read_outpost_ticket_num(self, outpost_name):
        num_str = self.wait_ocr(
            match=re.compile(r"\d+"),
            box=self.box_of_screen(
                1224 / 1920,
                235 / 1080,
                1551 / 1920,
                356 / 1080,
            ),
            time_out=5,
        )

        num = 0
        if num_str and hasattr(num_str[0], "name"):
            try:
                num = int(num_str[0].name)
            except ValueError:
                num = 0

        self.log_info(f"{outpost_name} 据点当前券数量: {num}")
        return num

    def perform_outpost_exchange(
        self,
        outpost_name,
        priority_list=None,
        excluded_goods=None,
        only_priority_goods=False,
    ):
        """据点内循环尝试更换货品并兑换。"""
        self.log_info(f"开始处理据点: {outpost_name}")

        priority_list = priority_list or []
        excluded_goods = excluded_goods if excluded_goods is not None else set()

        if not self.wait_click_ocr(
            match=get_world_map_text(self.lang, outpost_name),
            box=self.box.top,
            time_out=5,
        ):
            return False

        self.wait_ocr(
            match=self.lang.daily_routine_mixin.k_bb6c696b,
            box=self.box_of_screen(1700 / 1920, 610 / 1080, 1, 710 / 1080),
            time_out=5,
        )
        can_exchange_goods = [
            get_world_map_text(self.lang, good) for good in goods_dict.get(get_area_by_outpost_name(outpost_name), [])
        ]

        goods_patterns = [
            re.compile(re.escape(get_world_map_text(self.lang, good)))
            for good in get_goods_by_outpost_name(outpost_name)
        ]

        max_attempts = 7
        skip_goods = set()
        change_button = None
        confirm_button = None

        num = self.read_outpost_ticket_num(outpost_name)
        if num < 1000:
            self.log_info(f"{outpost_name} 据点当前券数量不足 (<1000)，停止兑换")
            max_attempts = 0

        for attempt in range(1, max_attempts + 1):
            self.log_info(f"尝试第 {attempt}/{max_attempts} 次更换货品")
            if not change_button:
                change_button = self.wait_click_ocr(
                    match=self.lang.daily_routine_mixin.k_bb6c696b,
                    box=self.box_of_screen(1700 / 1920, 610 / 1080, 1, 710 / 1080),
                    time_out=5,
                )
            else:
                self.click(change_button)
            self.wait_ocr(
                match=self.lang.daily_routine_mixin.k_70b20820,
                box=self.box.top_left,
                time_out=5,
            )
            goods = self.wait_ocr(
                match=goods_patterns,
                time_out=5,
            )

            if not goods:
                self.log_info(f"{outpost_name} 没有可兑换的货物")
                break

            normalized_goods = []
            for good in goods:
                good_name = good.name.strip("|｜丨")  # 清理 OCR 将卡片边框识别成的竖线。
                # 取argmin：返回编辑距离最小的货名，同距离优先长货名，无候选时返回 None。
                standard_name = min(
                    (kw for kw in can_exchange_goods if len(good_name) >= max(2, len(kw) - 1)),
                    key=lambda kw, name=good_name: (_edit_distance(name, kw), -len(kw)),
                    default=None,
                )

                if not standard_name:
                    self.log_info(f"未知货物: {good.name}，跳过")
                    continue

                if good.name != standard_name:
                    self.log_info(f"修正 OCR 识别结果: '{good.name}' -> '{standard_name}'")
                    good.name = standard_name

                normalized_goods.append(good)

            def priority_score(name):
                for i, pattern in enumerate(priority_list):
                    if pattern in can_exchange_goods:
                        if pattern == name:
                            return i
                    elif re.search(pattern, name):
                        return i
                return len(priority_list)

            if only_priority_goods and priority_list:
                normalized_goods = [good for good in normalized_goods if priority_score(good.name) < len(priority_list)]
                if not normalized_goods:
                    self.log_info(f"{outpost_name} 没有匹配优先商品序列的可兑换货物")
                    break

            normalized_goods.sort(key=lambda g: (priority_score(g.name), -len(g.name)))

            exchange_good = None
            for good in normalized_goods:
                standard_name = good.name

                if standard_name in skip_goods or standard_name in excluded_goods:
                    self.log_info(f"跳过已处理货物: {standard_name}")
                    continue

                skip_goods.add(standard_name)
                exchange_good = good
                self.log_info(f"成功锁定兑换目标: {standard_name}")
                break

            if not exchange_good:
                self.log_info(f"{outpost_name} 本轮没有可兑换目标")
                break

            self.log_info(f"选择货物进行兑换: {exchange_good.name}")
            self.click(exchange_good, after_sleep=0.1)
            if not confirm_button:
                confirm_button = self.wait_feature(feature=fL.select_confirm, time_out=5, raise_if_not_found=False)
                if confirm_button:
                    self.click(confirm_button)
            else:
                self.click(confirm_button)
            self.wait_click_ocr(match=get_world_map_text(self.lang, outpost_name), box=self.box.top, time_out=5)
            if not self.plus_max():
                excluded_goods.add(exchange_good.name)
                self.log_info(f"货物不可交易，加入地区排除列表: {exchange_good.name}")
                continue

            quantity_limit = self._get_outpost_trade_limit(exchange_good.name, num)
            if quantity_limit is not None:
                self._limit_outpost_trade_quantity(quantity_limit)

            if not self.wait_click_feature(
                feature=fL.to_max_produce_num,
                box=self.box_of_screen(0.945, 0.894, 0.973, 0.944),
                time_out=5,
                raise_if_not_found=False,
            ):
                excluded_goods.add(exchange_good.name)
                self.log_info(f"货物不可交易，加入地区排除列表: {exchange_good.name}")
                continue

            if quantity_limit is not None:
                self.click_confirm(after_sleep=2)

            self.wait_pop_up()
            num = self.read_outpost_ticket_num(outpost_name)
            if num < 1000:
                self.log_info(f"{outpost_name} 据点当前券数量不足 (<1000)，停止兑换")
                break

            excluded_goods.add(exchange_good.name)
            self.log_info(f"货物已兑换完，加入地区排除列表: {exchange_good.name}")

        self.log_info(f"{outpost_name} 兑换操作完成")

    def exchange_outpost_goods(self, target_areas=None, keep_area_context=False):
        self.info_set("current_task", "exchange_outpost_goods")
        self.log_info("开始据点兑换任务")

        priority_list = self.config.get("交易货品优先序列", [])
        only_priority_goods = self.config.get("据点兑换仅购买优先商品", False)
        excluded_goods_by_area = {area: set() for area in areas_list}

        for area in target_areas or areas_list:
            self.log_info(f"进入区域: {area}")
            if not self.to_model_area(area, "据点管理"):
                self.log_info(f"无法进入{area}据点管理，据点兑换失败")

            outposts = outpost_dict.get(area, [])
            if not outposts:
                self.log_info(f"{area} 没有据点可兑换")
                if keep_area_context:
                    self.safe_back(feature=fL.transaction_overview, once_time_out=3)
                else:
                    self.ensure_main()
                continue

            for outpost_name in outposts:
                self.log_info(f"开始兑换据点: {outpost_name}")
                self.perform_outpost_exchange(
                    outpost_name,
                    priority_list,
                    excluded_goods_by_area[area],
                    only_priority_goods,
                )
                self.log_info(f"完成兑换据点: {outpost_name}")

            self.log_info(f"{area} 区域据点兑换完成")
            if keep_area_context:
                self.safe_back(feature=fL.transaction_overview, once_time_out=3)
            else:
                self.ensure_main()

        self.log_info("据点兑换任务完成")
        return True

    def _get_outpost_trade_limit(self, good_name, tickets):
        """返回活动货品的券余额对应数量；None 表示普通货品。

        活动结束后清空价格表即可停用此策略，通用交易与调量方法仍可复用。
        券余额除以单价并向下取整作为调量参考，选择首次达到或超过上限的档位出售。
        """
        activity_prices = {
            get_world_map_text(self.lang, "息壤龙泡泡"): 100,
            get_world_map_text(self.lang, "重息壤龙泡泡"): 200,
        }
        unit_price = activity_prices.get(good_name)
        return tickets // unit_price if unit_price is not None else None

    def _limit_outpost_trade_quantity(self, limit) -> None:
        """全卖不超过当前调度券储量直接出售，否则从 10% 逐档调量。

        算法：
        0. 全卖该货物不超过当前调度券储量则直接出售全部库存。
        1. 从 10% 到 100% 逐档遍历，每次增加 10%，先点击再读数。
           相邻档位可能落在当前滑块手柄内，因此先点离目标较远的一端，再点目标档位。
           10%～50% 先点右端，60%～100% 先点左端；端点跳转只用于定位，不读数也不出售。
        2. 首次读到有效数量 Q >= limit 时直接出售当前档位；小于上限时继续增加。
           到 100% 仍未达到上限则出售全部库存，超额确认由调用方处理。
        3. 读数异常时继续向右尝试；到 100% 仍无法读数时回退到 10% 出售。
        点击使用整数像素，避免比例舍入改变落点；本方法只负责调量，不返回是否允许出售。
        """
        # 数量文字框留足上下边距，兼容数字居中时被一起识别的“份数”字样。
        quantity_box = self.box_of_screen(2180 / 2560, 1065 / 1440, 2370 / 2560, 1133 / 1440)
        quantity_pattern = re.compile(r"^\D*(\d+)$")
        # 根据 2560x1440 原图估计完整轨道 x=2024..2344；2051..2315 仅是滑块中心范围。
        slider_left, slider_right = 2024 / 2560, 2344 / 2560
        slider_y = 1150 / 1440

        def read_quantity():
            result = self.wait_ocr(match=quantity_pattern, box=quantity_box, time_out=2, raise_if_not_found=False)
            return int(quantity_pattern.search(result[0].name).group(1)) if len(result or []) == 1 else None

        # 先读取最大数量；有效且不超过上限时直接出售全部库存
        maximum = read_quantity()
        if maximum is not None and 0 < maximum <= limit:
            self.log_info(f"据点调量：全部库存 {maximum} 不超过可售 {limit}，直接出售")
            return

        pixel_y = int(slider_y * self.height)

        def click_step(step):
            reset_position = slider_right if step <= 5 else slider_left  # 先把手柄移到离目标较远的一端。
            self.click(int(reset_position * self.width), pixel_y, name="outpost_trade_quantity_reset", after_sleep=0.2)
            position = slider_left + (slider_right - slider_left) * step / 10
            self.click(int(position * self.width), pixel_y, name="outpost_trade_quantity", after_sleep=2)

        for step in range(1, 11):
            click_step(step)
            current = read_quantity()
            self.log_info(f"据点调量：{step * 10}% 档，可售 {limit}，当前数量 {current}")
            if current is not None and current > 0 and (current >= limit or step == 10):
                self.log_info(f"据点调量：出售 {step * 10}% 档")
                return
        click_step(1)
        self.log_info("据点调量：读数异常，回退到 10% 档出售")

    def collect_market_goods_info(self, buy_only=False):
        def ocr_stock_quantity() -> int:
            stock_piece = self.ocr(
                match=_DIGITS_ONLY_RE,
                box=self.box_of_screen(353 / 1920, 607 / 1080, 613 / 1920, 635 / 1080),
                log=True,
            )
            if stock_piece and stock_piece[0].name.isdigit():
                return int(stock_piece[0].name)
            return 0

        market_text_y = None
        market_text = self.wait_ocr(match=self.lang.daily_trade_mixin.k_930f2e66, box=self.box.left)
        if not market_text:
            self.log_info("未识别到市场文字")
            return [], None

        market_text_y = market_text[0].y
        self.next_frame()

        # =========================
        # ✅ 只买模式：直接OCR扫描
        # =========================
        if buy_only:
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
                if num > 400:
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

            self.log_info(self.tr("最低价格: {price}").format(price=min_item.good_price))

            return min_item, market_text_y

        # =========================
        # ✅ 正常模式：逐个点击采集
        # =========================
        goods = self.ocr(
            match=self.lang.daily_trade_mixin.k_13f2c5a1,
            log=True,
            box=self.box_of_screen(0, market_text_y / self.height, 1, 1),
        )

        sum_good_info = []
        for good in goods:
            self.click(good)
            self.wait_ui_stable(refresh_interval=1)
            self.next_frame()
            stock_quantity = ocr_stock_quantity()
            good_piece = self.ocr(
                match=_DIGITS_ONLY_RE,
                box=self.box_of_screen(1527 / 1920, 324 / 1080, 1600 / 1920, 400 / 1080),
                frame_processor=self.make_hsv_isolator(hR.DARK_GRAY_TEXT),
                log=True,
            )
            if not good_piece:
                good_piece = self.ocr(
                    match=_DIGITS_ONLY_RE,
                    box=self.box_of_screen(1527 / 1920, 324 / 1080, 1600 / 1920, 400 / 1080),
                    log=True,
                )

            self.wait_click_ocr(match=self.lang.daily_trade_mixin.k_cd3a269a, box=self.box.bottom_right)

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
            back_to_area_deadline = self.active_time() + 20
            while not self.wait_ocr(match=self.lang.daily_trade_mixin.k_d6bdcc47, box=self.box.top_left, time_out=1):
                if self.active_time() > back_to_area_deadline:
                    self.log_info("等待返回 '地区建设' 界面超时，结束当前市场采集")
                    return sum_good_info, market_text_y
                self.back()

        return sum_good_info, market_text_y

    def analyze_goods_info(self, good_infos: list[dict], buy_price: int, sell_price: int):
        processed_goods: list[GoodsInfo] = []

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
                f"卖价:{good.friend_price!s:>6} "
            )

        buy_good = min(processed_goods, key=lambda x: x.good_price)

        self.log_info(f"推荐购买 | 名称:{buy_good.good_name} | 价格:{buy_good.good_price}")

        try:
            sell_goods = [good for good in processed_goods if good.friend_price > sell_price]
        except TypeError:
            self.log_error("好友价格数据异常，无法进行出售分析")
            sell_goods = []

        if sell_goods:
            self.log_info("===== 推荐出售列表 =====")
            for good in sell_goods:
                self.log_info(f"推荐出售 | 名称:{good.good_name} | 卖价:{good.friend_price}")
        else:
            self.log_info("没有符合出售条件的货物")

        if buy_good.good_price < buy_price:
            self.log_info(f"满足购买条件 | 实际价格:{buy_good.good_price} < 设定上限:{buy_price}")
            return buy_good, sell_goods, True
        else:
            self.log_info(f"不满足购买条件 | 实际价格:{buy_good.good_price} >= 设定上限:{buy_price}")
            return buy_good, sell_goods, False

    def navigate_to_friend_exchange(self):
        self.log_info("前往物资调度终端")
        self.ensure_in_friend_boat()
        self.ensure_map()
        if not self.start_tracking_and_align_target(fL.market_dispatch_terminal, fL.market_dispatch_terminal_out):
            return False
        result = self.navigate_until_target(
            target=self.lang.daily_trade_mixin.k_fa04e4df,
            nav=fL.market_dispatch_terminal_out,
            time_out=200,
            box=self.box_of_screen(0.631, 0.565, 0.771, 0.837),
        )

        if result:
            self.press_key("f")
            self.wait_ui_stable(refresh_interval=1)
        return result

    def buy_sell(self, target_areas=None, keep_area_context=False, after_buy=None):
        """买卖货主流程。

        Returns:
            tuple[bool, bool]: (是否未发生导航失败, 是否已前往好友帝江号——
                仅当「前往」按钮真正点击成功且到达好友船后才为 True)。
        """
        navigation_failed = False
        went_friend_boat = False
        for area in target_areas or areas_list:
            if not self.config.get(area, False):
                self.log_info(self.tr("跳过{area}，因为配置中未启用").format(area=self.tr(area)))
                continue
            if not keep_area_context:
                self.ensure_main()
            if not self.to_model_area(area, "物资调度"):
                self.log_info(self.tr("无法进入{area}物资调度，买卖货失败").format(area=self.tr(area)))
                navigation_failed = True
                continue
            self.wait_click_ocr(match=self.lang.daily_trade_mixin.k_33fb3f9c, box=self.box.top)
            result = self.wait_feature(
                feature=fL.market_good_icon,
                time_out=2,
                raise_if_not_found=False,
            )
            if not result:
                self.log_info("未找到货物")
                continue
            self.click(result)
            buy_only = self.config.get("只买不卖", False)
            # 价格校验在采集前：buy_only 时不需要卖出价
            buy_price = self.config.get(f"{area}买入价", 0)
            sell_price = self.config.get(f"{area}卖出价", 0)
            if not buy_price or (not buy_only and not sell_price):
                self.log_info("未找到所需价格配置")
                continue
            good_infos, _ = self.collect_market_goods_info(buy_only=buy_only)
            if buy_only:
                buy_good = good_infos
                sell_goods = []
                can_buy = buy_good and (buy_good.good_price < buy_price)
            else:
                buy_good, sell_goods, can_buy = self.analyze_goods_info(good_infos, buy_price, sell_price)
            # 「即将溢出」状态强制购买：该商品即将溢出、必须买入，
            # 即使不满足价格上限。实际 OCR 文本为「即将溢出」四字，
            # 用一个「即将|溢出」合并正则匹配以便容错：OCR 可能把
            # 四字识别为一个文本块，也可能拆成「即将」「溢出」两个块，
            # 任一命中即视为即将溢出（见 find_boxes_by_name 的 OR 语义）。
            if (
                buy_good
                and not can_buy
                and self.wait_ocr(
                    match=self.lang.daily_trade_mixin.impending_overflow,
                    box=self.box.top_left,
                    time_out=3,
                )
            ):
                can_buy = True
            if buy_good:
                if can_buy:
                    back_to_area_deadline = self.active_time() + 20
                    while not self.wait_ocr(
                        match=self.lang.daily_trade_mixin.k_d6bdcc47,
                        box=self.box.top_left,
                        time_out=1,
                    ):
                        if self.active_time() > back_to_area_deadline:
                            self.log_info("等待返回 '地区建设' 界面超时，结束买卖货任务")
                            return False, went_friend_boat
                        self.back()
                    self.click(buy_good.name_box)
                    self.wait_ui_stable(refresh_interval=1)
                    if self.plus_max():
                        self.wait_click_ocr(match=self.lang.daily_trade_mixin.k_7cf40bbd, box=self.box.bottom_right)
                        self.wait_pop_up()
                        for sg in sell_goods:
                            if sg.good_name == buy_good.good_name:
                                sg.stock_quantity += 1
                                self.log_info(f"{sg.good_name} 本次已购买，存货数量更新为 {sg.stock_quantity}")
                                break
                    else:
                        self.log_info("未找到加号按钮，无法购买")
                        self.back()

            if after_buy is not None:
                after_buy(area)

            if sell_goods:
                if not self.wait_click_ocr(
                    match=self.lang.daily_trade_mixin.k_33fb3f9c,
                    box=self.box.top,
                    time_out=5,
                ):
                    self.log_info("未能切回弹性需求物资，跳过卖出操作")
                    continue

            for sell_good in sell_goods:
                if sell_good.stock_quantity <= 0:
                    # 物品名来自游戏数据运行时加载不过 tr
                    self.log_info(self.tr("跳过出售 {name}，存货数量<=0").format(name=sell_good.good_name))
                    continue
                back_to_area_deadline = self.active_time() + 20
                while not self.wait_ocr(
                    match=self.lang.daily_trade_mixin.k_d6bdcc47, box=self.box.top_left, time_out=1
                ):
                    if self.active_time() > back_to_area_deadline:
                        self.log_info("等待返回 '地区建设' 界面超时，结束买卖货任务")
                        return False, went_friend_boat
                    self.back()
                if not (
                    self.wait_click_ocr(match=re.compile(sell_good.name_box.name[-3:]), log=True)
                    or self.wait_click_ocr(match=re.compile(sell_good.good_name[:3]), log=True)
                ):
                    self.log_info("未找到卖出货物，无法出售")
                    continue
                self.wait_ui_stable(refresh_interval=1)
                self.wait_click_ocr(
                    match=self.lang.daily_trade_mixin.k_cd3a269a,
                    box=self.box.bottom_right,
                )
                self.wait_ui_stable(refresh_interval=1)
                try:
                    c_y = sell_good.friend_name_box.y + sell_good.friend_name_box.height // 2
                    c_x = sell_good.friend_name_box.x - int((808 - 737) / 1920 * self.width)
                except AttributeError:
                    self.log_info("未找到好友价格，无法出售")
                    continue
                self.click(c_x, c_y)
                go_friend_deadline = self.active_time() + 20
                while not self.wait_click_ocr(match=self.lang.daily_trade_mixin.k_23926d61, box=self.box.center):
                    if self.active_time() > go_friend_deadline:
                        self.log_info("等待 '前往' 按钮超时，跳过该货物出售")
                        break
                    self.click(c_x, c_y, move_back=False)
                if self.active_time() > go_friend_deadline:
                    continue
                if not self.ensure_in_friend_boat():
                    self.log_info("未进入好友船")
                    return False, went_friend_boat
                # 已真正点击「前往」并到达好友帝江号：角色在野外，之后无论卖出是否
                # 完成都只能 ensure_main 返回主界面（safe_back 逐层返回无法退回地区建设）
                went_friend_boat = True
                self.navigate_to_friend_exchange()
                self.wait_click_ocr(match=get_world_map_matcher(self.lang, area), box=self.box.top)
                if not (
                    self.wait_click_ocr(match=re.compile(sell_good.name_box.name[-3:]))
                    or self.wait_click_ocr(match=re.compile(sell_good.good_name[:3]))
                ):
                    self.log_info("未找到卖出货物，无法出售")
                    continue
                self.wait_ui_stable(refresh_interval=1)
                if self.plus_max():
                    self.wait_click_ocr(
                        match=self.lang.daily_trade_mixin.k_b84e4cb0,
                        box=self.box.bottom_right,
                    )
                    self.wait_pop_up()
                else:
                    self.log_info("未找到加号按钮，无法出售")

        return not navigation_failed, went_friend_boat

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

    def run_regional(self):
        selected = set(self.config.get("⭐地区建设", self.DEFAULT_OPTIONS) or [])
        enabled_outpost = "据点兑换" in selected
        enabled_buy = "买物资" in selected
        enabled_trade = "买卖货" in selected

        for area in areas_list:
            self.log_info(self.tr("开始处理地区建设: {area}").format(area=self.tr(area)))
            if enabled_outpost:
                if not self.exchange_outpost_goods(
                    target_areas=[area],
                    keep_area_context=True,
                ):
                    self.log_info(self.tr("据点兑换失败: {area}").format(area=self.tr(area)))

            if enabled_trade:
                # 买卖货：买入后通过 after_buy 回调执行「买物资」。
                # buy_sell 在地区未启用/未找到货物/缺少买卖价时会跳过回调，
                # 因此用 _buy_ran 标记，回调未执行时单独补一次买物资。
                # 任一步骤失败均只记录日志，继续处理下一地区。
                self._buy_ran = False
                trade_ok, went_friend_boat = self.buy_sell(
                    target_areas=[area],
                    keep_area_context=True,
                    after_buy=self._buy_staple_after_trade if enabled_buy else None,
                )
                if not trade_ok:
                    self.log_info(self.tr("买卖货失败: {area}").format(area=self.tr(area)))
                if went_friend_boat:
                    # 已前往好友帝江号：角色在野外，界面远离地区建设总览，
                    # 逐层返回难以退回。直接回主界面结束本区域建设，继续下一区域。
                    self.ensure_main()
                    self.log_info(
                        self.tr("已前往好友帝江号，直接回主界面结束{area}地区建设").format(area=self.tr(area))
                    )
                    continue
                if enabled_buy and not self._buy_ran:
                    self.log_info("买卖货未执行买物资回调，单独执行买物资")
                    self._buy_staple_in_area(area)
            elif enabled_buy:
                # 仅买物资：先进入物资调度，再执行购买。
                self._buy_staple_in_area(area)

            self.safe_back(feature=fL.transaction_overview, once_time_out=3)
            self.log_info(self.tr("完成地区建设: {area}").format(area=self.tr(area)))

        return True

    def _buy_staple_after_trade(self, current_area):
        """买卖货买入后的「买物资」回调。"""
        self._buy_ran = True
        return self.buy_staple_goods(
            target_areas=[current_area],
            keep_area_context=True,
        )

    def _buy_staple_in_area(self, area):
        """进入物资调度后执行「买物资」。"""
        if not self.to_model_area(area, "物资调度"):
            self.log_info(self.tr("无法进入{area}物资调度，跳过买物资").format(area=self.tr(area)))
            return False
        if not self.buy_staple_goods(
            target_areas=[area],
            keep_area_context=True,
        ):
            self.log_info(self.tr("买物资失败: {area}").format(area=self.tr(area)))
        return True

    def run(self):
        self.ensure_main(time_out=420)
        return self.run_regional()
