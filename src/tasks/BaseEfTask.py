from ok import BaseTask
import ctypes
import math
import random
import time
import win32gui

from src.essence.essence_recognizer import EssenceInfo, read_essence_info

user32 = ctypes.windll.user32
MOUSEEVENTF_MOVE = 0x0001
TOLERANCE = 50


class BaseEfTask(BaseTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._logged_in = False

    def move_keys(self, keys, duration):
        hwnd=self.hwnd.hwnd
        try:
            current_hwnd = win32gui.GetForegroundWindow()
            # 只有不在前台才激活
            if current_hwnd != hwnd:
                win32gui.ShowWindow(hwnd, 5)  # 5表示SW_SHOW，显示并激活窗口
                win32gui.SetForegroundWindow(hwnd)  # 将窗口设置为前台窗口
                time.sleep(0.5)  # 等待窗口激活

        except Exception as e:
            print("窗口激活失败:", e)  # 捕获并打印窗口激活过程中的异常
        key_map = {
            "w": 0x57,
            "a": 0x41,
            "s": 0x53,
            "d": 0x44,
        }

        KEYEVENTF_KEYUP = 0x0002

        # 全部按下
        for k in keys:
            user32.keybd_event(key_map[k.lower()], 0, 0, 0)

        self.sleep(duration)

        # 全部抬起
        for k in keys:
            user32.keybd_event(key_map[k.lower()], 0, KEYEVENTF_KEYUP, 0)

    def calc_direction_step(
        self, from_pos, to_pos, max_step=100, min_step=40, slow_radius=120, deadzone=4
    ):
        dx_raw = to_pos[0] - from_pos[0]
        dy_raw = to_pos[1] - from_pos[1]

        dist = math.hypot(dx_raw, dy_raw)

        # ===== 死区 =====
        if dist < deadzone:
            return 0, 0

        # ===== 非线性速度 =====
        if dist > slow_radius:
            step = max_step
        else:
            step = max(min_step, int(max_step * (dist / slow_radius)))

        dx = round(dx_raw / dist * step)
        dy = round(dy_raw / dist * step)

        return dx, dy

    def active_and_send_mouse_delta(self, hwnd, dx=1, dy=1, activate=True, only_activate=False, delay=0.02, steps=3):
        """
        发送鼠标相对移动的函数，可以选择是否激活目标窗口，并支持平滑移动
        参数:
            hwnd: 目标窗口的句柄
            dx: x方向的移动距离
            dy: y方向的移动距离
            activate: 是否激活目标窗口，默认为True
            delay: 每次移动后的延迟时间，默认为0.02秒
            steps: 将移动分为多少步完成，默认为3步
        """
        if only_activate:
            activate=True
        if activate:
            try:
                current_hwnd = win32gui.GetForegroundWindow()
                # 只有不在前台才激活
                if current_hwnd != hwnd:
                    win32gui.ShowWindow(hwnd, 5)  # 5表示SW_SHOW，显示并激活窗口
                    win32gui.SetForegroundWindow(hwnd)  # 将窗口设置为前台窗口
                    time.sleep(delay)  # 等待窗口激活

            except Exception as e:
                print("窗口激活失败:", e)  # 捕获并打印窗口激活过程中的异常

        # 分 steps 次移动，平滑
        if not only_activate:
            for i in range(steps):
                step_dx = round(dx / steps)
                step_dy = round(dy / steps)
                user32.mouse_event(MOUSEEVENTF_MOVE, step_dx, step_dy, 0, 0)
                time.sleep(delay)

    def move_to_target_once(self,hwnd, ocr_obj, screen_center_func):
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

        dx, dy = self.calc_direction_step(center_pos, target_center)

        if dx != 0 or dy != 0:
            self.active_and_send_mouse_delta(hwnd, dx, dy)

    # def center_camera(self):
    #     self.click(0.5, 0.5, down_time=0.2, key="middle")
    #     self.wait_until(self.in_combat, time_out=1)

    def screen_center(self):
        return int(self.width / 2), int(self.height / 2)

    # def turn_direction(self, direction):
    #     if direction != "w":
    #         self.send_key(direction, down_time=0.05, after_sleep=0.5)
    #     self.center_camera()

    def align_ocr_or_find_target_to_center(self, match_or_name,box=None,threshold=0.8, max_time=100,ocr=True,raise_if_fail=True,is_num=False):
        """
        将OCR识别的目标或图像特征目标对准屏幕中心
        参数:
            match_or_name: 目标匹配名称或特征名称
            max_time: 最大尝试次数，默认为50次
            ocr: 是否使用OCR模式，默认为True
        异常:
            如果在最大尝试次数内无法对中目标，抛出"对中失败"异常
        """

        last_target=None
        last_target_fail_count = 0
        for i in range(max_time):
            if ocr:
                # 使用OCR模式识别目标，设置超时时间为2秒，并启用日志记录
                result = self.wait_ocr(match=match_or_name,box=box, time_out=2, log=True)
            else:
                self.sleep(2)
                # 使用图像特征识别模式查找目标
                box = self.box_of_screen(
                    (1920 - 1550) / 1920,
                    150 / 1080,
                    1550 / 1920,
                    (1080 - 150) / 1080,
                )
                result = self.find_feature(feature_name=match_or_name,threshold=threshold, box=box)
            if result:
                # OCR 成功
                if isinstance(result, list):
                    result = result[0]
                if is_num:
                    result.y = result.y - int(self.height * ((525 - 486) / 1080))

                target_center = (
                    result.x + result.width // 2,
                    result.y + result.height // 2,
                )
                screen_center_pos = self.screen_center()
                last_target = result
                last_target_fail_count = 0
                # 计算偏移量
                dx = target_center[0] - screen_center_pos[0]
                dy = target_center[1] - screen_center_pos[1]

                # 如果目标在容忍范围内
                if abs(dx) <= TOLERANCE and abs(dy) <= TOLERANCE:
                    return True
                else:
                    self.move_to_target_once(
                        self.hwnd.hwnd, result, self.screen_center
                    )

            else:
                # 每次 OCR 失败，直接随机移动
                max_offset = 50  # 最大随机偏移
                if last_target and last_target_fail_count < 5:
                    self.move_to_target_once(
                        self.hwnd.hwnd, last_target, self.screen_center
                    )
                    last_target_fail_count += 1
                else:
                    last_target = None
                    last_target_fail_count = 0
                    dx = random.randint(-max_offset//2, max_offset)
                    dy = random.randint(-max_offset//2, max_offset)

                    # 移动鼠标
                    self.active_and_send_mouse_delta(
                        self.hwnd.hwnd,
                        dx,
                        dy,
                        activate=True,
                        delay=0.1,
                    )

                # OCR 成功后不需要处理，下一次失败仍然随机

        if raise_if_fail:
            raise Exception("对中失败")
        else:
            return False
    def skip_dialog(self,end_list=None,end_box=None):
        start_time = time.time()
        while True:
            if time.time() - start_time > 60:
                self.log_info("skip_dialog 超时退出")
                break
            if self.wait_ocr(match=["工业","探索"],box="top_left", time_out=1.5):
                break
            if self.find_one("skip_dialog_esc", horizontal_variance=0.05):
                self.send_key("esc", after_sleep=0.1)
                start = time.time()
                clicked_confirm = False
                while time.time() - start < 3:
                    confirm = self.find_confirm()
                    if confirm:
                        self.click(confirm, after_sleep=0.4)
                        clicked_confirm = True
                    elif clicked_confirm:
                        self.log_debug("AutoSkipDialogTask no confirm break")
                        break
            if end_list and self.wait_ocr(match=end_list, box=end_box, time_out=0.5):
                break
    def in_bg(self):
        return not self.hwnd.is_foreground()

    def find_confirm(self):
        return self.find_one('skip_dialog_confirm', horizontal_variance=0.05, vertical_variance=0.05)

    def in_combat_world(self):
        in_combat_world = self.find_one('top_left_tab')
        if in_combat_world:
            self._logged_in = True
        return in_combat_world

    def find_f(self):
        return self.find_one('pick_f', vertical_variance=0.05)

    def ensure_main(self, esc=True, time_out=30):
        self.info_set('current task', f'wait main esc={esc}')
        if not self.wait_until(lambda: self.is_main(esc=esc), time_out=time_out, raise_if_not_found=False):
            raise Exception('Please start in game world and in team!')
        self.info_set('current task', f'in main esc={esc}')

    def in_world(self):
        in_world = self.find_one('esc') and self.find_one('b') and self.find_one('c')
        if in_world:
            self._logged_in = True
        return in_world

    def is_main(self, esc=False):
        if self.in_world():
            self._logged_in = True
            return True
        if self.wait_login():
            return True
        if esc:
            self.back(after_sleep=1.5)
    def wait_pop_up(self):
        count = 0
        while True:
            if count > 30:
                raise Exception("提交后未检测到奖励界面，提交失败")
            result = self.find_one(feature_name="reward_ok", box="bottom",threshold=0.8)
            if result:
                self.click(result)
                break
            self.sleep(1)
            count += 1
    def wait_login(self):
        if not self._logged_in:
            if self.in_world():
                self._logged_in = True
                return True
            elif self.find_one('monthly_card') or self.find_one('logout'):
                self.click(after_sleep=1)
                return False
            elif close := (self.find_one(
                    'reward_ok', horizontal_variance=0.1, vertical_variance=0.1, ) or self.find_one(
                'one_click_claim', horizontal_variance=0.1, vertical_variance=0.1) or self.find_one('check_in_close',
                                                                                                    horizontal_variance=0.1,
                                                                                                    vertical_variance=0.1,
                                                                                                    threshold=0.75)):
                self.click(close, after_sleep=1)
                return False

    def read_essence_info(self) -> EssenceInfo | None:
        return read_essence_info(self)
