"""缘缘庄单局厨房下单，使用游戏自身的缺菜标记。"""  # 识别依据见配套开发说明。

from src.core.BaseEfTask import BaseEfTask  # 使用统一任务生命周期。
from src.icons import Icons  # 复用现有配送图标。
from src.image.restaurant_detector import (  # 分离纯图像识别。
    KITCHEN_TABS,  # 厨房页签参考位置。
    kitchen_queues,  # 识别队列圆点。
    reference_frame,  # 归一化游戏帧。
    restock_cards,  # 识别缺菜标记。
)  # 不依赖额外模板资源。


class RestaurantTask(BaseEfTask):  # 注册为一次性经营任务。
    def __init__(self, *args, **kwargs):  # 初始化任务元数据。
        super().__init__(*args, **kwargs)  # 先初始化项目基类。
        self.name = "缘缘庄传菜"  # 固定任务名称供翻译收集。
        self.icon = Icons.Deliver  # 复用配送图标。
        self.group_name = "日常任务"  # 与其他经营任务放在同组。
        self.description = (  # 明确当前可运行范围与前置条件。
            "客栈4级并启用祀的上菜、上饮品、结账职责后，先开始经营并打开厨房界面。"  # 由祀负责配送。
            "自动按缺菜标记下单并完成本局结算；需开启三间厨房，仅支持中文和16:9窗口，忽略老饕。"  # 当前布局边界。
        )  # 不增加不必要的配置项。

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
        for card in restock_cards(reference_frame(self.frame)):  # 标记已扣除成品与在制菜品。
            if not self._available(card):  # 原料不足时尝试其他缺菜。
                continue  # 不阻塞其他桌的订单。
            x, y = card.click_point  # 点击菜品卡片主体。
            self.click_relative(x / 1280, y / 720, after_sleep=0.3)  # 一次提交一份。
            confirmed = self.wait_until(  # 等待队列增加或当前缺菜标记消失。
                lambda card=card: self._dispatch_changed(queue, card),  # 不按烹饪耗时推算库存。
                time_out=3,  # 给服务器响应留出时间。
                raise_if_not_found=False,  # 未确认时重新读取真实缺菜状态。
            )  # 不对同一旧快照连续点击。
            if confirmed:  # 只统计有反馈的下单。
                self.info_incr("restaurant_orders")  # 提供任务进度。
            return True  # 本帧至多一次下单。
        return False  # 当前可见页没有可制作的缺菜。

    def _dispatch_changed(self, queue, card):  # 生产完成与新下单可能同时发生。
        frame = reference_frame(self.frame)  # 使用等待器刷新的帧。
        current = next(
            (q for q in kitchen_queues(frame) if q.index == queue.index and q.selected), None
        )  # 核验目标厨房。
        if current is None:  # 过渡帧不证明下单成功。
            return False  # 继续等待。
        if current.used > queue.used:  # 队列增加可以确认提交。
            return True  # 无需计算制作秒数。
        return not any(
            c.left == card.left and abs(c.top - card.top) < 12 for c in restock_cards(frame)
        )  # 重新读取游戏需求。

    def run(self):  # 完成当前一局，不消耗客源重开。
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
