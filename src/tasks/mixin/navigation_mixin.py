import random
import re
import time

import win32gui

from src.interaction.Mouse import active_and_send_mouse_delta
from src.tasks.BaseEfTask import BaseEfTask, back_window

TOLERANCE = 50


class NavigationMixin(BaseEfTask):
    def start_tracking_and_align_target(self, target_feature_in_map, target_feature_out_map):
        """在地图中开启追踪并在地图外完成朝向对齐。"""
        result = self.find_one(
            feature_name=target_feature_in_map,
            box=self.box_of_screen(0, 0, 1, 1),
            threshold=0.7,
        )
        if not result:
            self.log_info(f"未找到{target_feature_in_map}图标")
            return False
        self.log_info(f"找到{target_feature_in_map}图标，点击进入")
        self.click(result, after_sleep=2)

        if result := self.wait_ocr(match=re.compile("追踪"), box=self.box.bottom_right, time_out=5):
            if "追踪" in result[0].name and "取" not in result[0].name and "消" not in result[0].name:
                self.log_info("点击追踪按钮")
                self.click(result, after_sleep=2)

        self.press_key("m", after_sleep=2)
        self.log_info("关闭地图界面 (按下 M)")

        self.align_ocr_or_find_target_to_center(
            ocr_match_or_feature_name_list=target_feature_out_map,
            only_x=True,
            threshold=0.7,
            ocr=False,
        )
        self.log_info("已对齐地图目标")
        return True

    def navigate_until_target(
            self,
            target_ocr_pattern,
            nav_feature_name,
            time_out: int = 60,
            pre_loop_callback=None,
            found_special_callback=None,
    ):
        """通用导航循环：识别目标前持续前进并动态对齐。"""
        start_time = time.time()
        short_distance_flag = False
        fail_count = 0
        prev = win32gui.GetForegroundWindow()
        while not self.wait_ocr(
                match=target_ocr_pattern,
                box=self.box.bottom_right,
                time_out=1,
        ):
            if time.time() - start_time > time_out:
                self.log_info("导航超时")
                back_window(prev)
                return False

            if found_special_callback:
                special_result = found_special_callback()
                if special_result is not None:
                    back_window(prev)
                    return special_result

            if pre_loop_callback:
                pre_loop_callback()

            if not short_distance_flag:
                nav = self.find_feature(
                    nav_feature_name,
                    box=self.box_of_screen(
                        (1920 - 1550) / 1920,
                        150 / 1080,
                        1550 / 1920,
                        (1080 - 150) / 1080,
                    ),
                    threshold=0.7,
                )

                if nav:
                    fail_count = 0
                    self.log_info("找到导航路径，继续对齐并前进")

                    self.align_ocr_or_find_target_to_center(
                        ocr_match_or_feature_name_list=nav_feature_name,
                        only_x=True,
                        threshold=0.7,
                        ocr=False,
                    )

                    self.move_keys("w", duration=0.75)
                else:
                    fail_count += 1
                    self.log_info(f"未找到导航路径，连续失败次数: {fail_count}")

                    if fail_count >= 3:
                        self.log_info("切换短距离移动")
                        short_distance_flag = True

                    self.move_keys("w", duration=0.25)
            else:
                self.move_keys("w", duration=0.25)

            self.sleep(0.5)
        back_window(prev)
        return True

    def align_ocr_or_find_target_to_center(
            self,
            ocr_match_or_feature_name_list,
            only_x=False,
            only_y=False,
            box=None,
            threshold=0.8,
            max_time=50,
            ocr=True,
            use_yolo=False,
            back_prev=False,
            raise_if_fail=True,
            is_num=False,
            need_scroll=False,
            max_step=100,
            min_step=20,
            slow_radius=200,
            once_time=0.5,
            tolerance=TOLERANCE,
            ocr_frame_processor_list=None
    ):
        """将OCR识别或图像特征检测的目标对准屏幕中心（自动移动视角/鼠标）

        Args:
            ocr_match_or_feature_name_list: OCR匹配模式(str/List)或特征名称(str/List)
            only_x: True时仅对齐X轴（左右），Y轴保持不变
            only_y: True时仅对齐Y轴（上下），X轴保持不变
            box: 搜索区域框(Box)，None表示全屏。用于限制OCR/特征检测范围
            threshold: 图像特征匹配阈值(0-1)，默认0.8，仅在ocr=False时使用
            max_time: 最大尝试循环次数，默认50次
            ocr: True使用OCR模式识别，False使用图像特征匹配模式
            use_yolo: 在ocr=False时，是否改用YOLO识别（True=YOLO，False=模板特征匹配）
            raise_if_fail: True时对中失败抛出异常，False时返回False
            is_num: 数字型目标Y坐标微调（用于识别数字时的位置校正）
            need_scroll: True时在对中过程中自动滚动（通常用于列表)
            max_step: 单次移动最大步长(像素)
            min_step: 单次移动最小步长(像素)
            slow_radius: 接近目标时减速的半径范围(像素)
            once_time: 每次循环最小耗时(秒)，保证操作频率
            tolerance: 目标中心与屏幕中心的容忍偏差(像素)，默认50，偏差在范围内判定成功
            ocr_frame_processor_list: OCR帧处理函数列表(可用于色彩隔离等预处理)

        Returns:
            bool: 成功对中返回True，失败返回False(当raise_if_fail=False时)

        Raises:
            Exception: 对中失败且raise_if_fail=True时抛出异常
        """
        if box:
            feature_box = box
        else:
            feature_box = self.box_of_screen(
                (1920 - 1550) / 1920,
                150 / 1080,
                1550 / 1920,
                (1080 - 150) / 1080,
            )
        last_target = None
        last_target_fail_count = 0
        success = False
        random_move_count = 0
        move_count = 0
        scroll_bool = False
        sum_dx = 0
        sum_dy = 0
        if back_prev:    
            prev = win32gui.GetForegroundWindow()
        move_bool=False
        for i in range(max_time):
            start_action_time = time.time()
            if ocr:
                # 使用OCR模式识别目标，设置超时时间为2秒，并启用日志记录
                start_time = time.time()
                result = None
                while time.time() - start_time < 1:
                    frame = self.next_frame()
                    if not isinstance(ocr_frame_processor_list, list):
                        ocr_frame_processor_list = [ocr_frame_processor_list]
                    for ocr_frame_processor in ocr_frame_processor_list:
                        result = self.ocr(
                            match=ocr_match_or_feature_name_list,
                            box=box,
                            frame=frame,
                            log=True,
                            frame_processor=ocr_frame_processor,
                        )
                        if result:
                            break
                    if result:
                        break
                    time.sleep(0.1)
            else:
                if isinstance(ocr_match_or_feature_name_list, str):
                    ocr_match_or_feature_name_list = [ocr_match_or_feature_name_list]
                start_time = time.time()
                result = None
                while True:
                    if time.time() - start_time >= 1:
                        break
                    frame = self.next_frame()
                    if use_yolo:
                        result = self.yolo_detect(
                            name=ocr_match_or_feature_name_list,
                            frame=frame,
                            box=feature_box,
                            conf=threshold,
                        )
                    else:
                        for feature_name in ocr_match_or_feature_name_list:
                            if time.time() - start_time >= 1:
                                break

                            result = self.find_feature(
                                feature_name=feature_name,
                                threshold=threshold,
                                box=feature_box,
                            )
                            if result:
                                break
                    if result:
                        break
                    self.sleep(0.1)
            if result:
                success = True
                random_move_count = 0
                move_count = 0
                # OCR 成功
                if isinstance(result, list):
                    result = result[0]
                if is_num:
                    result.y = result.y - int(self.height * ((525 - 486) / 1080))
                if only_y:
                    result.x = self.width // 2 - result.width // 2
                if only_x:
                    result.y = self.height // 2 - result.height // 2
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
                if abs(dx) <= tolerance and abs(dy) <= tolerance:
                    if move_bool and back_prev:
                        back_window(prev)
                    return True
                else:
                    move_bool=True
                    dx, dy = self.move_to_target_once(result, max_step=max_step, min_step=min_step,
                                                      slow_radius=slow_radius)
                    sum_dx += dx
                    sum_dy += dy

            else:
                # 每次 OCR 失败，直接随机移动
                move_bool=True
                max_offset = 60  # 最大随机偏移
                if last_target:
                    decay = 0.9 ** last_target_fail_count
                    # 计算目标中心到屏幕中心的偏移

                    screen_center_x, screen_center_y = self.screen_center()
                    offset_x = int((screen_center_x - last_target.x) * decay)
                    offset_y = int((screen_center_y - last_target.y) * decay)
                    offset_width = int(last_target.width / 2 * decay)
                    offset_height = int(last_target.height / 2 * decay)
                    # 直接修改 last_target 坐标
                    last_target.x = screen_center_x - offset_x
                    last_target.y = screen_center_y - offset_y
                    last_target.width = offset_width
                    last_target.height = offset_height
                    dx, dy = self.move_to_target_once(last_target)
                    sum_dx += dx
                    sum_dy += dy
                    last_target_fail_count += 1
                    random_move_count = 0
                    move_count += 1
                    if move_count >= 10:
                        last_target = None
                        move_count = 0
                else:
                    if not success:
                        max_offset = self.width // 4
                    last_target = None
                    last_target_fail_count = 0
                    dx = random.randint(-max_offset, max_offset)
                    if not success:
                        dy = 0
                    else:
                        dy = random.randint(-max_offset, max_offset)

                    # 移动鼠标
                    active_and_send_mouse_delta(
                        self.hwnd.hwnd,
                        dx,
                        dy,
                        activate=True,
                        delay=0.1,
                    )
                    sum_dx += dx
                    sum_dy += dy
                    move_count = 0
                    random_move_count += 1
                    if random_move_count >= 10:
                        success = False
                        random_move_count = 0

            if time.time() - start_action_time < once_time:
                self.sleep(once_time - (time.time() - start_action_time))  # OCR 成功后不需要处理，下一次失败仍然随机
            if not scroll_bool and need_scroll:
                scroll_bool = True
                # cx = int(self.width * 0.5)
                # cy = int(self.height * 0.5)
                for _ in range(6):
                    # self.scroll(cx, cy, 8)
                    self.scroll_relative(0.5, 0.5, 80)
                    self.sleep(1)
        if raise_if_fail:
            raise Exception("对中失败")
        else:
            return False
