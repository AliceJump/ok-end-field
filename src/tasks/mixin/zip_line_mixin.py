import re
import time

from src.image.hsv_config import HSVRange as hR
from src.tasks.sequence_parser import parse_int_sequence
from src.tasks.mixin.navigation_mixin import NavigationMixin

on_zip_line_tip = ["向目标移动", "离开滑索架"]
on_zip_line_stop = [re.compile(i) for i in on_zip_line_tip]
continue_next = re.compile("下一连接点")


class ZipLineMixin(NavigationMixin):
    def on_zip_line_start(self, delivery_to, need_scroll=None, target=None, need_v=True):
        """进入滑索后，根据配置对齐并滑行至送货点

        Args:
            delivery_to: 送货目标名称（用于获取配置中的滑索距离序列）
            need_scroll: 是否需要滚动
            target: 目标信息，包含名称和类型(例如：("登上滑索架", "ocr"))
            need_v: 是否需要按V键追踪
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
        self.zip_line_list_go(zip_line_list, need_scroll, target, need_v=need_v)

    def zip_line_list_go(self, zip_line_list, need_scroll=None, target=None, need_v=False):
        """按顺序对齐滑索并执行滑行

        Args:
            zip_line_list: 滑索距离列表
            need_scroll: 是否需要滚动
            target: 目标信息，包含名称和类型(例如：("登上滑索架", "ocr"))
            need_v: 是否需要按V键追踪

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
                self.send_key("e")  # 游戏内无法修改此按键，故使用底层按键函数
                self.sleep(0.1)
                result = self.ocr(
                    match=on_zip_line_stop,
                    box="bottom",
                    log=True,
                )
                if result:
                    break
                if time.time() - start > 240:
                    raise Exception("滑索超时，强制退出")
        if need_v:
            self.sleep(1)
            self.click(key="right")
        if target:
            result_name = target[0]
            result_type = target[1]
            if result_type == "ocr":
                ocr_bool = True
                yolo_bool = False
            elif result_type == "yolo":
                ocr_bool = False
                yolo_bool = True
            else:
                ocr_bool = False
                yolo_bool = False
            if need_v:
                self.ensure_main()
            keys = ["w", "a", "s", "d"]
            for i in range(4):
                if result := (not need_v) or self.wait_ocr(
                        match="登上滑索架", box=self.box.bottom_right, settle_time=1, time_out=4, log=True
                ):
                    if need_v:
                        self.press_key("v", after_sleep=1)
                        self.click_with_alt(result[0], after_sleep=2)
                    self.align_ocr_or_find_target_to_center(
                        ocr_match_or_feature_name_list=result_name,
                        threshold=0.8,
                        ocr=ocr_bool,
                        use_yolo=yolo_bool,
                        raise_if_fail=False,
                    )
                    self.click(key="right")
                    break
                else:
                    self.move_keys(keys[i], 0.1)
        if self.wait_ocr(match=on_zip_line_stop, box="bottom", log=True, time_out=2):
            self.click(key="right", after_sleep=2)
        self.log_info("滑索结束")
        self.ensure_main()
