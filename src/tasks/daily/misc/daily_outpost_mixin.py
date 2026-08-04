import re

from src.data.FeatureList import FeatureList as fL
from src.data.world_map import areas_list, goods_dict, outpost_dict
from src.data.world_map_utils import get_area_by_outpost_name, get_goods_by_outpost_name, get_world_map_text


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
            match=self.lang.daily_routine_mixin.k_bb6c696b, box=self.box_of_screen(1700 / 1920, 610 / 1080, 1, 710 / 1080), time_out=5
        )
        can_exchange_goods = [get_world_map_text(self.lang, good) for good in goods_dict.get(
            get_area_by_outpost_name(outpost_name), []
        )]

        goods_patterns = [
            re.compile(get_world_map_text(self.lang, good)) for good in get_goods_by_outpost_name(outpost_name)
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
                change_button = self.wait_click_ocr(match=self.lang.daily_routine_mixin.k_bb6c696b,
                                                    box=self.box_of_screen(1700 / 1920, 610 / 1080, 1, 710 / 1080),
                                                    time_out=5)
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
                standard_name = next(
                    (
                        kw for kw in sorted(can_exchange_goods, key=len, reverse=True)
                        if (kw in good.name or good.name in kw)
                           and len(good.name) >= max(2, len(kw) - 1)
                    ),
                    None
                )

                if not standard_name:
                    self.log_info(f"未知货物: {good.name}，跳过")
                    continue

                if good.name != standard_name:
                    self.log_info(
                        f"修正 OCR 识别结果: '{good.name}' -> '{standard_name}'"
                    )
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
                normalized_goods = [
                    good for good in normalized_goods
                    if priority_score(good.name) < len(priority_list)
                ]
                if not normalized_goods:
                    self.log_info(
                        f"{outpost_name} 没有匹配优先商品序列的可兑换货物"
                    )
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
                confirm_button = self.wait_feature(
                    feature=fL.select_confirm,
                    time_out=5,
                    raise_if_not_found=False
                )
                if confirm_button:
                    self.click(confirm_button)
            else:
                self.click(confirm_button)
            self.wait_click_ocr(
                match=get_world_map_text(self.lang, outpost_name),
                box=self.box.top,
                time_out=5
            )
            if not self.plus_max():
                excluded_goods.add(exchange_good.name)
                self.log_info(f"货物不可交易，加入地区排除列表: {exchange_good.name}")
                continue

            if not self.wait_click_feature(
                feature=fL.to_max_produce_num,
                box=self.box_of_screen(0.945, 0.894, 0.973, 0.944),
                time_out=5,
                raise_if_not_found=False
            ):
                excluded_goods.add(exchange_good.name)
                self.log_info(f"货物不可交易，加入地区排除列表: {exchange_good.name}")
                continue

            self.wait_pop_up()
            num = self.read_outpost_ticket_num(outpost_name)
            if num < 1000:
                self.log_info(f"{outpost_name} 据点当前券数量不足 (<1000)，停止兑换")
                break

            excluded_goods.add(exchange_good.name)
            self.log_info(f"货物已兑换完，加入地区排除列表: {exchange_good.name}")

        self.log_info(f"{outpost_name} 兑换操作完成")

    def test_ocr_full(self):
        self.next_frame()
        self.ocr(log=True)

    def test_ocr(self):
        box1 = self.box_of_screen(1749 / 1920, 107 / 1080, 1789 / 1920, 134 / 1080)
        box2 = self.box_of_screen(
            (1749 + (1832 - 1750)) / 1920, 107 / 1080, (1789 + (1832 - 1750)) / 1920, 134 / 1080
        )
        self.wait_click_ocr(
            match=re.compile(r"^\d+/5$"),
            after_sleep=2,
            time_out=2,
            box=box1,
            log=True,
        )
        self.wait_click_ocr(
            match=re.compile(r"^\d+/5$"),
            after_sleep=2,
            time_out=2,
            box=box2,
            log=True,
        )

    def exchange_outpost_goods(self, target_areas=None, keep_area_context=False):
        self.info_set("current_task", "exchange_outpost_goods")
        self.log_info("开始据点兑换任务")

        priority_list = self.config.get("交易货品优先序列", [])
        only_priority_goods = self.config.get("据点兑换仅购买优先商品", False)
        excluded_goods_by_area = {area: set() for area in areas_list}

        for area in target_areas or areas_list:
            self.log_info(f"进入区域: {area}")
            self.to_model_area(area, "据点管理")

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
