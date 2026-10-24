"""缘缘庄单局厨房下单，使用游戏自身的缺菜标记。"""  # 识别依据见配套开发说明。

from src.core.BaseEfTask import BaseEfTask  # 使用统一任务生命周期。
from src.icons import Icons  # 复用现有配送图标。
from src.image.restaurant_detector import (  # 分离纯图像识别。
    KITCHEN_TABS,  # 厨房页签参考位置。
    TABLE_LEFTS,
    RestockCard,
    kitchen_queues,  # 识别队列圆点。
    match_menu_icon,
    reference_frame,  # 归一化游戏帧。
    restock_cards,  # 识别缺菜标记。
    table_icon_rois,
)  # 不依赖额外模板资源。
from src.tasks.mixin.restaurant_setup_mixin import RestaurantSetupMixin
from src.tasks.restaurant_inventory import RestaurantInventory


class RestaurantTask(RestaurantSetupMixin, BaseEfTask):  # 注册为一次性经营任务。
    def __init__(self, *args, **kwargs):  # 初始化任务元数据。
        super().__init__(*args, **kwargs)  # 先初始化项目基类。
        self.name = "缘缘庄传菜"  # 固定任务名称供翻译收集。
        self.icon = Icons.Deliver  # 复用配送图标。
        self.group_name = "日常任务"  # 与其他经营任务放在同组。
        self.description = (  # 明确当前可运行范围与前置条件。
            "客栈4级、三间厨房及8格出餐台后，从缘缘庄柜台或经营中的厨房界面运行。"
            "自动配置厨师和菜单，优先补菜、预备两份库存并循环结算；仅支持中文和16:9窗口，忽略老饕。"
        )  # 不增加不必要的配置项。
        self.default_config.update({"经营轮次": 5})
        self.config_description.update({"经营轮次": "最多完成的经营局数；客源不足60时提前停止。"})
        self.config_type["经营轮次"] = {"min": 1, "max": 100}

    def _roi(self, x, y, width, height):  # 将参考坐标映射到实际截图。
        return self.box_of_screen(x / 1280, y / 720, (x + width) / 1280, (y + height) / 720)  # 不使用桌面DPI坐标。

    def _panel_ready(self):  # 仅在明确的厨房界面下单。
        return bool(
            self.ocr(box=self._roi(205, 462, 245, 40), match=self.lang.RestaurantTask.panel)
        )  # 使用稳定面板文案。

    def _ended(self):  # 经营结束横幅与厨房界面互斥。
        return bool(
            self.ocr(box=self._roi(490, 320, 300, 65), match=self.lang.RestaurantTask.ended)
        )  # 限定结束提示区域。

    def _settle(self):  # 逐页确认本局结算。
        if not self.ocr(
            box=self._roi(1020, 610, 190, 65), match=self.lang.RestaurantTask.complete_button
        ):  # 兼容已在最终结算页。
            self.wait_click_ocr(  # 等待收益结算的继续按钮。
                box=self._roi(1020, 610, 190, 65),
                match=self.lang.RestaurantTask.continue_button,
                time_out=60,
                raise_if_not_found=True,  # 等待离场动画。
            )  # 不点击提前结束经营。
        self.wait_click_ocr(  # 等待最终结算的完成按钮。
            box=self._roi(1020, 610, 190, 65),
            match=self.lang.RestaurantTask.complete_button,
            time_out=30,
            raise_if_not_found=True,  # 使用同一按钮区域。
        )  # 完成一次性任务。
        self.wait_until(  # 确认最后一页确实关闭。
            lambda: (
                not self.ocr(box=self._roi(1020, 610, 190, 65), match=self.lang.RestaurantTask.complete_button)
            ),  # 重新识别按钮。
            time_out=10,  # 结算未退出时不能报告成功。
            raise_if_not_found=True,  # 不把等待超时当作结算完成。
            settle_time=0.5,  # 排除按钮短暂识别失败。
        )  # 等待器负责刷新截图。
        self.log_info("缘缘庄本局经营已完成", notify=True)  # 完成后通知。

    def _available(self, card):  # 不点击原料耗尽或OCR不确定的菜。
        texts = self.ocr(  # 搜索缺菜标记所属卡片的可烹饪数量。
            box=self._roi(card.left + 85, card.top + 40, 210, 45),
            match=self.lang.RestaurantTask.craftable,  # 只读当前卡片。
        )  # OCR缺失时保守等待。
        for text in texts or []:  # 框架返回识别框列表。
            match = self.lang.RestaurantTask.craftable.search(text.name.replace(" ", ""))  # 保留完整数量。
            if match and (match[1] == "∞" or int(match[1]) > 0):  # 试营业的无限原料同样可下单。
                return True  # 允许安排本次制作。
        return False  # 不猜测未知库存。

    def _dispatch(self, queue):  # 每次只安排一道菜并等待反馈。
        if not queue.selected:  # 先切到队列最短的厨房。
            x, y, width, height = KITCHEN_TABS[queue.index]  # 坐标来自上游界面布局。
            self.click_relative((x + width / 2) / 1280, (y + height / 2) / 720, after_sleep=0.2)  # 相对游戏帧点击。
            selected = self.wait_until(  # 确认切换，避免把菜排到旧厨房。
                lambda: next(  # 从当前帧重新识别厨房。
                    (
                        q for q in kitchen_queues(reference_frame(self.frame)) if q.index == queue.index and q.selected
                    ),  # 必须已选中。
                    None,  # 未切换时继续等待。
                ),  # wait_until负责刷新帧。
                time_out=3,  # 切换失败即停止本次尝试。
                raise_if_not_found=False,  # 动画期间可重试。
            )  # 不沿用切换前队列状态。
            if not selected or selected.used >= selected.capacity:  # 检查当前剩余队列位。
                return False  # 下一轮重新识别。
            queue = selected  # 使用切换后的已确认状态。
        frame = reference_frame(self.frame)
        cards = restock_cards(frame)
        if not cards and self._restaurant_tables_complete:
            spare = self._restaurant_inventory.spare()
            if spare:
                for top in (207, 302, 392):
                    for left in (488, 797):
                        if (
                            match_menu_icon(frame[top + 13 : top + 79, left + 31 : left + 97], self._restaurant_icons)
                            == spare
                        ):
                            cards.append(RestockCard(left, top))
                            break
                    if cards:
                        break
        for card in cards:  # 缺菜优先，空闲时才安排预备库存。
            if not self._available(card):  # 原料不足时尝试其他缺菜。
                continue  # 不阻塞其他桌的订单。
            x, y = card.click_point  # 点击菜品卡片主体。
            current_frame = reference_frame(self.frame)
            item = match_menu_icon(
                current_frame[card.top + 13 : card.top + 79, card.left + 31 : card.left + 97], self._restaurant_icons
            )
            self.click_relative(x / 1280, y / 720, after_sleep=0.3)  # 一次提交一份。
            confirmed = self.wait_until(  # 等待目标厨房队列增加。
                lambda: self._dispatch_changed(queue),  # 不按烹饪耗时推算库存。
                time_out=3,  # 给服务器响应留出时间。
                raise_if_not_found=False,  # 未确认时重新读取真实缺菜状态。
            )  # 不对同一旧快照连续点击。
            if confirmed:  # 只统计有反馈的下单。
                self.info_incr("restaurant_orders")  # 提供任务进度。
            if item:
                self._restaurant_inventory.submitted(item, confirmed=bool(confirmed))
            else:
                self._restaurant_inventory.certain = False
            return True  # 本帧至多一次下单。
        return False  # 当前可见页没有可制作的缺菜。

    def _dispatch_changed(self, queue):  # 生产完成与新下单可能同时发生。
        frame = reference_frame(self.frame)  # 使用等待器刷新的帧。
        current = next(
            (q for q in kitchen_queues(frame) if q.index == queue.index and q.selected), None
        )  # 核验目标厨房。
        if current is None:  # 过渡帧不证明下单成功。
            return False  # 继续等待。
        return current.used > queue.used  # 只有队列增加才能计入预备库存。

    def _observe_restaurant_tables(self, frame):
        if not self._restaurant_inventory.certain:
            return False
        complete = True
        for table, left in enumerate(TABLE_LEFTS):
            if table in self._restaurant_inventory.tables and self.ocr(
                box=self._roi(left, 68, 94, 26), match=self.lang.RestaurantTask.dining
            ):
                self._restaurant_inventory.table(table, dining=True)
                continue
            rois = table_icon_rois(frame, table)
            if rois is None:
                complete = False
                continue
            if not rois and table in self._restaurant_inventory.tables:
                complete = False
            items = []
            for x, y, width, height in rois:
                item = match_menu_icon(frame[y : y + height, x : x + width], self._restaurant_icons)
                if item is None:
                    complete = False
                    break
                items.append(item)
            else:
                self._restaurant_inventory.table(table, items)
        return complete

    def run(self):
        rounds = int(self.config.get("经营轮次", 5))
        if not 1 <= rounds <= 100:
            raise ValueError("经营轮次必须在1到100之间")
        for number in range(rounds):
            state = self._prepare_restaurant()
            if state == "insufficient":
                return
            self._restaurant_inventory = RestaurantInventory(self._restaurant_dishes, fresh=state == "fresh")
            self._run_restaurant_round()
            self.info_set("restaurant_rounds", number + 1)
            if number + 1 < rounds:
                self.ensure_main(time_out=60)

    def _run_restaurant_round(self):  # 完成当前一局。
        reference_frame(self.frame)  # 先检查实际游戏帧比例与尺寸。
        self.wait_until(self._panel_ready, time_out=10, raise_if_not_found=True)  # 要求从经营中的厨房界面开始。
        self.wait_until(  # 要求已扩建三间厨房，并确认当前选中状态。
            lambda: (
                len(queues := kitchen_queues(reference_frame(self.frame))) == 3 and sum(q.selected for q in queues) == 1
            ),  # 不猜测未识别页签。
            time_out=10,  # 未满足布局要求时停止。
            raise_if_not_found=True,  # 不支持的布局不能继续输入。
        )  # 避免在不支持的布局空转。
        deadline = self.active_time() + 600  # 包含5分钟营业、收摊与结算缓冲。
        missing_since = None  # 记录识别中断时长。
        scroll_down = True  # 交替扫描上下两页，兼容八道菜。
        while self.active_time() < deadline:  # 任务有明确结束边界。
            self.sleep(0.5)  # 清除缓存并允许用户暂停或停止。
            if self._ended():  # 识别经营自然结束。
                self._settle()  # 等待并点击结算按钮。
                return  # 一次性任务正常完成。
            if not self._panel_ready():  # 不在厨房时暂停输入。
                if self.ocr(  # 可能错过短暂结束横幅，直接进入结算页。
                    box=self._roi(1020, 610, 190, 65),  # 限定结算按钮区域。
                    match=[
                        self.lang.RestaurantTask.continue_button,
                        self.lang.RestaurantTask.complete_button,
                    ],  # 接受两页结算。
                ):  # 任务已从厨房进入明确的结算页面。
                    self._settle()  # 顺序完成剩余结算。
                    return  # 确认退出后结束任务。
                missing_since = missing_since or self.active_time()  # 短暂动画允许恢复。
                if self.active_time() - missing_since > 20:  # 长时间离开面板必须报错。
                    raise RuntimeError("缘缘庄厨房界面丢失，已停止下单")  # 避免点到其他页面。
                continue  # 下一帧重新检查。
            missing_since = None  # 恢复后清除丢失计时。
            if not self.ocr(
                box=self._roi(65, 5, 110, 40), match=self.lang.RestaurantTask.countdown
            ):  # 倒计时证明经营仍在进行。
                continue  # 不对没有营业状态的画面输入。
            frame = reference_frame(self.frame)  # 统一图像检测参考尺寸。
            self._restaurant_tables_complete = self._observe_restaurant_tables(frame)
            queues = kitchen_queues(frame)  # 每帧读取当前队列。
            available = sorted(
                (q for q in queues if q.used < q.capacity), key=lambda q: (q.used, q.index)
            )  # 优先空闲厨房。
            if not available:  # 满队列或颜色不确定时等待。
                continue  # 不按计时假设队列空闲。
            if self._dispatch(available[0]):  # 本轮安排一次有效需求。
                continue  # 等待下一帧重新计算需求。
            self.scroll_relative(0.76, 0.49, -3 if scroll_down else 3)  # 扫描重叠页面中的其余菜品。
            scroll_down = not scroll_down  # 返回第一页防止漏单。
        raise RuntimeError("缘缘庄经营超时，未确认本局结算")  # 不把超时报告为成功。
