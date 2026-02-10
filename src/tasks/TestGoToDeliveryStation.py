import re

from src.tasks.BaseEfTask import BaseEfTask
from src.tasks.BaseEfTask import send_mouse_delta

class TestGoToDeliveryStation(BaseEfTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config = {"_enabled": True}
        self.name = "测试Map"


    def run(self):
        # self.send_key("j")
        self.sleep(2)

        self.move_to_target_once(self.hwnd.hwnd,self.ocr(match=re.compile(r"紧要"))[0],self.screen_center)
        self.mouse_down(int(self.hwnd.width * 0.2), int(self.hwnd.height * 0.5))
        self.scroll_relative(0.2,0.5,count=-80)
        self.mouse_up()