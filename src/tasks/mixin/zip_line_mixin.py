import re
import time

from src.image.hsv_config import HSVRange as hR
from src.tasks.sequence_parser import parse_int_sequence
from src.tasks.mixin.navigation_mixin import NavigationMixin

on_zip_line_tip = ["向目标移动", "离开滑索架"]
on_zip_line_stop = [re.compile(i) for i in on_zip_line_tip]
continue_next = re.compile("下一连接点")


class ZipLineMixin(NavigationMixin):
    def on_zip_line_start(self, delivery_to, need_scroll=None):
        """进入滑索后，根据配置对齐并滑行至送货点

        Args:
            delivery_to: 送货目标名称（用于获取配置中的滑索距离序列）
            need_scroll: 是否需要滚动

        Raises:
            Exception: 滑索超时时抛出异常
        """
        start = time.time()
        self.sleep(1)
        self.next_frame()
        while not self.ocr(match=on_zip_line_stop, frame=self.next_frame(), box="bottom", log=True):
            self.sleep(0.1)
            if time.time() - start > 60:
                raise Exception("滑索超时，强制退出")
        zip_line_list_str = self.config.get(delivery_to)
        zip_line_list = parse_int_sequence(zip_line_list_str)
        self.zip_line_list_go(zip_line_list, need_scroll)

    def zip_line_list_go(self, zip_line_list, need_scroll=None):
        """按顺序对齐滑索并执行滑行

        Args:
            zip_line_list: 滑索距离列表
            need_scroll: 是否需要滚动
        """
        for zip_line in zip_line_list:
            self.align_ocr_or_find_target_to_center(
                re.compile(str(zip_line)),
                is_num=True,
                need_scroll=need_scroll,
                ocr_frame_processor_list=[
                    self.make_hsv_isolator(hR.GOLD_TEXT),
                    self.make_hsv_isolator(hR.WHITE),
                ],
                max_time=100,
            )
            self.log_info(f"成功将滑索调整到{zip_line}的中心")
            self.click(after_sleep=0.5)
            start = time.time()
            while True:
                self.next_frame()
                self.press_key("e")
                self.sleep(0.1)
                result = self.ocr(
                    match=on_zip_line_stop,
                    box="bottom",
                    log=True,
                )
                if result:
                    break
                if time.time() - start > 60:
                    raise Exception("滑索超时，强制退出")
        self.sleep(1)
        self.click(key="right")
