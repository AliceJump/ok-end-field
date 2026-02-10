import re
import time

from src.tasks.BaseEfTask import BaseEfTask

on_zip_line_stop = re.compile("向目标移动")
continue_next = re.compile("下一连接点")


class DeliveryTask(BaseEfTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config = {"_enabled": True}
        self.name = "半自动送货"
        self.description = '选择后上滑索时启动'
        self.ends=["常沄","资源回收站","彦宁","齐纶"]
        self.default_config.update({
            '说明':'需填写滑索分叉序列(例如"108,64,109",是指上滑索后找108m的滑索并试图滑向它,后面依次为第一个分叉点,第二个分叉点...)',
            '常沄': "108,64,109,60",
            '资源回收站': "108,64,109",
            '彦宁': "108,64,108,59",
            '齐纶': "108,106",
            '选择送货对象': "常沄",
        })
        self.config_type["选择送货对象"] = {'type': "drop_down",
                                            'options': self.ends}
        self.lv_regex = re.compile(r"(?i)lv|\d{2}")
        self.last_target = None

    def zip_line_list_go(self, zip_line_list):
        for zip_line in zip_line_list:
            self.align_ocr_target_to_center(re.compile(str(zip_line)))
            self.log_info(f"成功将滑索调整到{zip_line}的中心")
            self.click(after_sleep=0.5)
            start = time.time()
            while not self.ocr(match=on_zip_line_stop, box="bottom", log=True):
                self.send_key("e")
                if time.time() - start > 60:
                    raise Exception("滑索超时，强制退出")
        self.sleep(1)
        self.click(key="right")

    def run(self):
        while not self.ocr(match=on_zip_line_stop, box="bottom", log=True):
            self.sleep(2)
        zip_line_list_str=self.config.get(self.config.get("选择送货对象"))
        zip_line_list = [int(i) for i in zip_line_list_str.split(",")]
        self.zip_line_list_go(zip_line_list)
