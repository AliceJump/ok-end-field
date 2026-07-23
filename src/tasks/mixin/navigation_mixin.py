import random
import re

import pyautogui

from src.core.BaseEfTask import BaseEfTask
from src.data.FeatureList import FeatureList as fL
TOLERANCE = 50


class NavigationMixin(BaseEfTask):
    def start_tracking_and_align_target(self, target_feature_in_map, target_feature_out_map):
        """在地图中开启追踪并在地图外完成朝向对齐。"""
        result = self.find_one(
            feature=target_feature_in_map,
            box=self.box_of_screen(0, 0, 1, 1),
            threshold=0.7,
        )
        if not result:
            self.log_info(f"未找到{target_feature_in_map}图标")
            return False
        self.log_info(f"找到{target_feature_in_map}图标，点击进入")
        self.click(result)

        if result := self.wait_feature(feature=fL.start_follow, box=self.box.bottom_right, time_out=5, raise_if_not_found=False):
            self.click(result, after_sleep=1)

        self.press_key("m", after_sleep=2)
        self.log_info("关闭地图界面 (按下 M)")
        start_time = self.active_time()
        while not self.find_feature(
            feature=target_feature_out_map, box=self.box_of_screen(0, 0, 1, 1),
            threshold=0.7
        ):
            if self.active_time() - start_time > 5:
                self.log_info("等待追踪图标超时")
                return False
        self.align_ocr_or_find_target_to_center(
            ocr_match_or_feature_name_list=target_feature_out_map,
            only_x=True,
            threshold=0.7,
            ocr=False
        )
        self.log_info("已对齐地图目标")
        return True

    def navigate_until_target(
        self,
        target,
        nav=None,
        target_is_ocr: bool = True,
        nav_is_ocr: bool = False,
        time_out: int = 60,
        pre_loop_callback=None,
        found_special_callback=None,
        target_is_yolo: bool = False,
        nav_is_yolo: bool = False,
        box=None,
        target_vertical_variance: float = 0.0,
        need_v: bool = False,
        max_run_time: float = -1,
    ):
        """
        持续导航移动直到检测到目标，支持 OCR / YOLO / 特征匹配三种方式。

        当 nav 为 None 时，进入纯前进搜索模式，不进行导航识别与对齐，仅持续移动直到目标出现。

        Args:
            target: 目标识别对象（OCR文本 / YOLO类别 / 特征名）。
            nav: 导航标识（OCR文本 / YOLO类别 / 特征名）。为 None 时禁用导航逻辑。
            target_is_ocr (bool): target 是否使用 OCR 检测。
            nav_is_ocr (bool): nav 是否使用 OCR 检测。
            time_out (int): 最大运行时间（秒）。
            pre_loop_callback (callable, optional): 每轮循环前执行的回调函数。
            found_special_callback (callable, optional): 特殊状态检测回调，返回非 None 则中断并返回。
            target_is_yolo (bool): target 是否使用 YOLO 检测。
            nav_is_yolo (bool): nav 是否使用 YOLO 检测。
            box (tuple, optional): target 检测区域（None 则使用默认区域）。
            target_vertical_variance (float): 特征匹配时允许的垂直误差。
            need_v (bool): 是否在导航丢失时周期性按 V 尝试重置视野。
            max_run_time (float): 最大累计奔跑（ctrl 状态）时间（秒），控制的是奔跑/步行切换，不影响 w 前进键。
                - -1：不限制奔跑时间，保持原有行为
                - 0：完全不奔跑，开局立即切换步行状态，全程按住 w 步行前进
                - 大于0：允许累计奔跑指定秒数，达到后在本次导航中切换步行且不再恢复奔跑，函数结束时恢复奔跑状态

        Returns:
            bool | Any:
                - True: 成功到达目标并稳定确认
                - False: 超时未完成
                - Any: found_special_callback 返回的非 None 结果

        Behavior:
            1. 持续按 W 移动
            2. 检测 target 是否出现
            3. 出现后进行稳定性确认
            4. 若丢失目标则后退搜索
            5. 若 nav 存在，则执行导航对齐逻辑
            6. 若 nav 为 None，则仅执行直线前进搜索

        """
        last_click_v_time = 0

        def check_target():
            if target_is_ocr:
                return self.ocr(
                    match=target,
                    box=self.box_of_screen(0.635, 0.563, 0.724, 0.843)
                    if not box else box
                )
            elif target_is_yolo:
                return self.yolo_detect(
                    name=target,
                    box=self.box_of_screen(0.635, 0.563, 0.724, 0.843)
                    if not box else box
                )
            else:
                return self.find_feature(
                    target,
                    threshold=0.7,
                    vertical_variance=target_vertical_variance
                )

        # ========== 奔跑状态管理 ==========
        run_bool = True  # 当前是否处于奔跑状态（ctrl 切换）
        start_time = self.active_time()

        # max_run_time 状态变量
        run_accumulated_time = 0.0
        run_state_start_time = self.active_time() if max_run_time > 0 else None
        run_allowed = True

        def enter_run_mode():
            """切换到奔跑状态（如果允许且尚未处于奔跑状态）。"""
            nonlocal run_bool, run_state_start_time
            if run_bool or not run_allowed:
                return
            run_bool = True
            run_state_start_time = self.active_time()
            self.press_key("ctrl")

        def exit_run_mode():
            """切换到步行状态，并累加本段奔跑时间。"""
            nonlocal run_bool, run_state_start_time, run_accumulated_time, run_allowed
            if not run_bool:
                return
            if run_state_start_time is not None:
                run_accumulated_time += self.active_time() - run_state_start_time
                run_state_start_time = None
            run_bool = False
            self.press_key("ctrl")
            if max_run_time > 0 and run_accumulated_time >= max_run_time:
                run_allowed = False

        def enforce_max_run_time():
            """达到最大奔跑时间后立即切换为步行。"""
            if max_run_time <= 0 or not run_bool or not run_allowed or run_state_start_time is None:
                return
            if run_accumulated_time + self.active_time() - run_state_start_time >= max_run_time:
                self.log_info("达到最大奔跑时间，切换为步行模式")
                exit_run_mode()

        # 处理 max_run_time == 0：开局切换为步行，且全程禁止奔跑
        if max_run_time == 0:
            run_allowed = False
            run_bool = False
            self.press_key("ctrl")

        # ========== 主循环 ==========
        nav_box = self.box_of_screen(
            (1920 - 1550) / 1920,
            150 / 1080,
            1550 / 1920,
            (1080 - 150) / 1080,
        )

        self.send_key_down("w")  # 确认使用send_key：w为方向移动键，不属于游戏可配置热键，用于持续移动

        try:
            while True:
                enforce_max_run_time()
                reached = check_target()

                if reached:
                    self.send_key_up("w")  # 确认使用send_key：释放方向键

                    if run_bool:
                        self.log_info("找到目标，确认稳定中...")
                        exit_run_mode()

                    settle_time = 2 if target_is_ocr else 1
                    stable = True
                    confirm_start = self.active_time()

                    while self.active_time() - confirm_start < settle_time:
                        if not check_target():
                            stable = False
                            break
                        self.sleep(0.1)

                    if stable:
                        return True

                    self.log_info("确认期间目标丢失，开始后退搜索")
                    self.send_key_down("s")  # 确认使用send_key：s为方向移动键，不属于游戏可配置热键，用于后退搜索

                    search_start = self.active_time()
                    while self.active_time() - search_start < 10:
                        if check_target():
                            self.log_info("后退过程中重新找到目标")
                            self.send_key_up("s")  # 确认使用send_key：释放方向键
                            break
                        self.sleep(0.05)

                    self.send_key_up("s")  # 确认使用send_key：释放方向键

                if self.active_time() - start_time > time_out:
                    self.log_info("导航超时")
                    return False

                if found_special_callback:
                    special_result = found_special_callback()
                    if special_result is not None:
                        self.send_key_up("w")  # 确认使用send_key：释放方向键
                        return special_result

                if pre_loop_callback:
                    pre_loop_callback()

                # ===== nav=None -> 纯前进搜索模式 =====
                if nav is None:
                    if not run_bool and run_allowed:
                        self.log_info("恢复奔跑模式")
                        enter_run_mode()

                    self.sleep(0.01)
                    continue

                if nav_is_ocr:
                    nav_result = self.ocr(match=nav, box=nav_box)
                elif nav_is_yolo:
                    nav_result = self.yolo_detect(name=nav, box=nav_box)
                else:
                    nav_result = self.find_feature(nav, box=nav_box, threshold=0.7)

                if nav_result:
                    if not run_bool and run_allowed:
                        self.log_info("重新找到导航，恢复奔跑模式")
                        enter_run_mode()

                    self.align_ocr_or_find_target_to_center(
                        ocr_match_or_feature_name_list=nav,
                        only_x=True,
                        threshold=0.7,
                        ocr=nav_is_ocr,
                        use_yolo=nav_is_yolo,
                        max_time=1,
                        raise_if_fail=False,
                        allow_random_move=False
                    )
                else:
                    if need_v and self.active_time() - last_click_v_time > 5:
                        self.log_info("未找到导航标识，点击 V 尝试")
                        self.press_key("v")
                        last_click_v_time = self.active_time()

                    if run_bool:
                        self.log_info("未找到导航标识，进入短距离搜索模式")
                        exit_run_mode()

                self.sleep(0.01)

        finally:
            if not run_bool:
                self.log_info("导航结束，恢复奔跑模式")
                self.press_key("ctrl", after_sleep=0.01)  # 确认使用send_key：ctrl为奔跑切换键，不属于游戏可配置热键
            self.send_key_up("w")  # 确认使用send_key：释放方向键

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
            max_step=120,
            min_step=20,
            slow_radius=350,
            deadzone=8,
            once_time=0.05,
            tolerance=TOLERANCE,
            ocr_frame_processor_list=None,
            allow_random_move=True,
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
            back_prev: True时对中完成后返回上一个窗口
            raise_if_fail: True时对中失败抛出异常，False时返回False
            is_num: 数字型目标Y坐标微调（用于识别数字时的位置校正）
            need_scroll: True时在对中过程中自动滚动放大视角（常用于滑索数字对中/列表滚动两类UI）
            max_step: 单次移动最大步长(像素)
            min_step: 单次移动最小步长(像素)
            slow_radius: 接近目标时减速的半径范围(像素)
            deadzone: 鼠标停止移动的死区半径(像素)
            once_time: 每次循环最小耗时(秒)，保证操作频率
            tolerance: 目标中心与屏幕中心的容忍偏差(像素)，默认50，偏差在范围内判定成功
            ocr_frame_processor_list: OCR帧处理函数列表(可用于色彩隔离等预处理)

        Returns:
            bool: 成功对中返回True，失败返回False(当raise_if_fail=False时)

        Raises:
            Exception: 对中失败且raise_if_fail=True时抛出异常
        """
        scaled_tolerance = self.scale_distance(tolerance)
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
        move_bool = False
        for i in range(max_time*2):
            start_action_time = self.active_time()
            if ocr:
                # 使用OCR模式识别目标，设置超时时间为2秒，并启用日志记录
                start_time = self.active_time()
                result = None
                while self.active_time() - start_time < 0.15:
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
                    self.sleep(0.1)
            else:
                if isinstance(ocr_match_or_feature_name_list, str):
                    ocr_match_or_feature_name_list = [ocr_match_or_feature_name_list]
                start_time = self.active_time()
                result = None
                while True:
                    if self.active_time() - start_time >= 1:
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
                            if self.active_time() - start_time >= 1:
                                break

                            result = self.find_feature(
                                feature=feature_name,
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
                if abs(dx) <= scaled_tolerance and abs(dy) <= scaled_tolerance:
                    return True
                else:
                    dx, dy = self.move_to_target_once(
                        result,
                        max_step=max_step,
                        min_step=min_step,
                        slow_radius=slow_radius,
                        deadzone=deadzone,
                    )
                    sum_dx += dx
                    sum_dy += dy

            else:
                if not allow_random_move:
                    continue
                # 每次 OCR 失败，直接随机移动
                max_offset = self.scale_distance(60)  # 最大随机偏移
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
                    self.active_and_send_mouse_delta(
                        dx,
                        dy,
                        activate=True,
                        steps=5,
                        delay=0.003,
                    )
                    sum_dx += dx
                    sum_dy += dy
                    move_count = 0
                    random_move_count += 1
                    if random_move_count >= 10:
                        success = False
                        random_move_count = 0

            if self.active_time() - start_action_time < once_time:
                self.sleep(once_time - (self.active_time() - start_action_time))  # OCR 成功后不需要处理，下一次失败仍然随机
            if need_scroll:
                # 初始放大（只执行一次）
                if not scroll_bool:
                    scroll_bool = True
                    self.do_scroll(8, 400)

                # 时间节点控制
                scroll_plan = {int(max_time * 0.250): -400, int(max_time * 0.500): -400, int(max_time * 0.750): -400}

                if i in scroll_plan:
                    self.do_scroll(2, scroll_plan[i])
        if raise_if_fail:
            raise Exception("对中失败")
        else:
            return False

    def do_scroll(self, times, delta):
        for _ in range(times):
            pyautogui.scroll(int(self.resolution_scale() * delta))
            self.sleep(0.1)
