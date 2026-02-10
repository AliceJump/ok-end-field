import re

from src.tasks.BaseEfTask import BaseEfTask
import math
import time
import ctypes
import win32gui
import random

user32 = ctypes.windll.user32
MOUSEEVENTF_MOVE = 0x0001
TOLERANCE = 100

on_zip_line_stop = re.compile("向目标移动")
continue_next = re.compile("下一连接点")


class DeliveryTask(BaseEfTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config = {"_enabled": True}
        self.name = "半自动送货"
        self.description = '选择后上滑索时启动'
        self.default_config.update({
            '说明':'需填写滑索分叉序列(例如"108,64,109",是指上滑索后找108m的滑索并试图滑向它,后面依次为第一个分叉点,第二个分叉点...)',
            '物资回收站左下': "108,64,109,60",
            '物资回收站': "108,64,109",
            '武陵城深处': "108,64,108,59",
            '武陵城右边': "108,106",
            '选择送货对象': "物资回收站左下",
        })
        self.config_type["选择送货对象"] = {'type': "drop_down",
                                            'options': ['物资回收站左下', '物资回收站', '武陵城深处','武陵城右边']}
        self.lv_regex = re.compile(r"(?i)lv|\d{2}")
        self.last_target = None

    # ==============================
    # 计算方向步长（闭环核心）
    # ==============================
    def calc_direction_step(
            self, from_pos, to_pos, base_step=90, min_step=60, max_step=150, scale=0.15
    ):
        dx_raw = to_pos[0] - from_pos[0]
        dy_raw = to_pos[1] - from_pos[1]

        dist = math.sqrt(dx_raw ** 2 + dy_raw ** 2)
        if dist == 0:
            return 0, 0

        # 距离越近步长越小，同时受 base_step 限制
        step = int(dist * scale)
        step = max(min_step, min(max_step, step))
        step = min(step, base_step)  # 这里让 base_step 起作用，限制最大步长

        dx = round(dx_raw / dist * step)
        dy = round(dy_raw / dist * step)

        return dx, dy

    # ==============================
    # 移动鼠标
    # ==============================
    def move_view(self, hwnd, dx, dy, activate=True, delay=0.02, steps=3):
        if activate:
            try:
                current_hwnd = win32gui.GetForegroundWindow()
                # 只有不在前台才激活
                if current_hwnd != hwnd:
                    win32gui.ShowWindow(hwnd, 5)
                    win32gui.SetForegroundWindow(hwnd)
                    time.sleep(delay)

            except Exception as e:
                print("窗口激活失败:", e)

        # 分 steps 次移动，平滑
        for i in range(steps):
            step_dx = round(dx / steps)
            step_dy = round(dy / steps)
            user32.mouse_event(MOUSEEVENTF_MOVE, step_dx, step_dy, 0, 0)
            time.sleep(delay)

    # ==============================
    # 闭环控制主函数
    # ==============================
    def move_to_target_once(self, hwnd, ocr_obj, screen_center_func, step_pixels=15):
        """
        hwnd: 游戏窗口句柄
        ocr_obj: OCR类对象，必须有 x, y, width, height 属性
        screen_center_func: 返回屏幕中心坐标
        step_pixels: 最大移动步长
        """
        if ocr_obj is None:
            return  # 没检测到目标

        # 用目标中心位置
        target_center = (
            ocr_obj.x + ocr_obj.width // 2,
            ocr_obj.y + ocr_obj.height // 2,
        )

        center_pos = screen_center_func()

        dx, dy = self.calc_direction_step(center_pos, target_center, base_step=step_pixels)

        if dx != 0 or dy != 0:
            self.move_view(hwnd, dx, dy)

    def center_camera(self):
        self.click(0.5, 0.5, down_time=0.2, key="middle")
        self.wait_until(self.in_combat, time_out=1)

    def screen_center(self):
        return int(self.width / 2), int(self.height / 2)

    def turn_direction(self, direction):
        if direction != "w":
            self.send_key(direction, down_time=0.05, after_sleep=0.5)
        self.center_camera()

    def align_ocr_target_to_center(self, match_or_name, max_time=50,ocr=True):
        for i in range(max_time):
            if ocr:
                result = self.wait_ocr(match=match_or_name, time_out=2, log=True)
            else:
                result = self.find_one(match=match_or_name, log=True)
            if result:
                # OCR 成功
                result = result[0]
                result.y = result.y - int(self.height * ((525 - 486) / 1080))

                target_center = (
                    result.x + result.width // 2,
                    result.y + result.height // 2,
                )
                screen_center_pos = self.screen_center()
                self.last_target = result
                # 计算偏移量
                dx = target_center[0] - screen_center_pos[0]
                dy = target_center[1] - screen_center_pos[1]

                # 如果目标在容忍范围内
                if abs(dx) <= TOLERANCE and abs(dy) <= TOLERANCE:
                    return
                else:
                    self.move_to_target_once(
                        self.hwnd.hwnd, result, self.screen_center, step_pixels=100
                    )

            else:
                # 每次 OCR 失败，直接随机移动
                max_offset = 50  # 最大随机偏移
                if self.last_target:
                    self.move_to_target_once(
                        self.hwnd.hwnd, self.last_target, self.screen_center, step_pixels=100
                    )
                else:
                    dx = random.randint(-max_offset, max_offset)
                    dy = random.randint(-max_offset, max_offset)

                    # 移动鼠标
                    self.move_view(
                        self.hwnd.hwnd,
                        dx,
                        dy,
                        activate=True,
                        delay=0.1,
                    )

                # OCR 成功后不需要处理，下一次失败仍然随机
        raise Exception("对中失败")

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
            self.click(key="right")

    def run(self):
        # zip_line_list = [108,64,109]
        # zip_line_list=[108,64,109,60]
        # zip_line_list = [108, 64, 108,59]
        # zip_line_list = [108, 106]
        while not self.ocr(match=on_zip_line_stop, box="bottom", log=True):
            self.sleep(2)
        zip_line_list_str=self.config.get(self.config.get("选择送货对象"))
        zip_line_list = [int(i) for i in zip_line_list_str.split(",")]
        self.zip_line_list_go(zip_line_list)
