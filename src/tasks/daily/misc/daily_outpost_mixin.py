import re

from src.data.FeatureList import FeatureList as fL
from src.data.world_map import areas_list, goods_dict, outpost_dict
from src.data.world_map_utils import get_area_by_outpost_name, get_goods_by_outpost_name, get_world_map_text


def _edit_distance(left: str, right: str) -> int:
    """Levenshtein 距离：插入、删除、替换一个字符的代价均为 1。"""
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, 1):
        current = [i]
        for j, right_char in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (left_char != right_char)))
        previous = current
    return previous[-1]


class DailyOutpostMixin:
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
        """从 10% 逐档增加数量，首次达到或超过上限即出售，不使用加减号。

        算法：
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
