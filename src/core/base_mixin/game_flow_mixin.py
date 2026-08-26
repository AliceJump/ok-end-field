import numpy as np
from pynput.keyboard import Key

from src.data.world_map import areas_list
from src.data.world_map_utils import get_world_map_matcher, get_world_map_text
from src.image.login_screenshot import capture_window_by_screen
from src.interaction.Mouse import run_at_window_pos
from src.data.FeatureList import FeatureList as fL


class GameFlowMixin:
    """登录弹窗、主界面状态与场景导航流程能力。"""

    def press_esc(self):
        """仅过剧情（对话时）使用的 ESC 触发函数。"""
        self.keyboard.press(Key.esc)
        self.keyboard.release(Key.esc)

    def login_screenshot(self, need_active=True):
        """
        截取登录或弹窗场景下的窗口截图。

        Args:
            need_active: 是否先激活窗口。

        Returns:
            np.ndarray: 截取到的窗口图像。
        """
        if need_active:
            self.active_and_send_mouse_delta(0, 0, activate=True, only_activate=True)
        self.sleep(0.1)
        return capture_window_by_screen(self.get_game_hwnd())

    def login_ocr(self, x=0, y=0, to_x=1, to_y=1, match=None, width=0, height=0, box=None, name=None, threshold=0,
                  target_height=0, use_grayscale=False, log=False, frame_processor=None, lib='default',
                  need_active=True):
        """
        基于登录截图执行 OCR 识别。

        Args:
            x: 区域左上角相对 X 坐标。
            y: 区域左上角相对 Y 坐标。
            to_x: 区域右下角相对 X 坐标。
            to_y: 区域右下角相对 Y 坐标。
            match: 需要匹配的文本或正则。
            width: 识别区域宽度。
            height: 识别区域高度。
            box: 识别框。
            name: 识别区域名称。
            threshold: OCR 置信度阈值。
            target_height: 目标缩放高度。
            use_grayscale: 是否转灰度。
            log: 是否记录日志。
            frame_processor: 额外帧处理器。
            lib: OCR 引擎名称。
            need_active: 是否先激活窗口。

        Returns:
            list: OCR 识别结果列表。
        """
        img = self.login_screenshot(need_active=need_active)
        if not isinstance(img, np.ndarray):
            img = np.array(img)
        return super().ocr(
            x=x,
            y=y,
            to_x=to_x,
            to_y=to_y,
            match=match,
            width=width,
            height=height,
            box=box,
            name=name,
            threshold=threshold,
            frame=img,
            target_height=target_height,
            use_grayscale=use_grayscale,
            log=log,
            frame_processor=frame_processor,
            lib=lib,
        )

    def skip_dialog(self, time_out=60):
        """
        跳过对话框，自动点击确认或跳过按钮。

        Returns:
            bool: 成功处理返回 True，超时返回 False。
        """
        start_time = self.active_time()
        while True:
            if self.active_time() - start_time > time_out:
                self.log_info("skip_dialog 超时退出")
                return False
            if self.find_one(fL.skip_dialog_esc, horizontal_variance=0.05):
                self.press_esc()
                self.click_confirm()
            self.sleep(0.5)

    def find_confirm(self):
        """
        寻找对话框中的确认按钮。

        Returns:
            list: 匹配到的确认按钮结果列表。
        """
        return self.find_one(
            fL.skip_dialog_confirm, horizontal_variance=0.05, vertical_variance=0.05
        )

    def click_confirm(self, after_sleep=0, time_out=5, recheck_time=0, disappear_time_out=0.8):
        """
        点击对话框中的确认按钮。

        Args:
            after_sleep: 点击后的延迟时间。
            time_out: 总超时时间。
            recheck_time: 点击后重新检测的等待时间。
            disappear_time_out: 等待确认按钮消失的最大时间。

        Returns:
            bool: 找到并点击确认按钮返回 True，超时返回 False。
        """
        start_time = self.active_time()
        while True:
            self.next_frame()
            confirm = self.find_confirm()
            if confirm:
                self.click(confirm)

                if disappear_time_out > 0:
                    self.wait_until(
                        lambda: not self.find_confirm(),
                        time_out=disappear_time_out,
                        raise_if_not_found=False,
                    )
                if after_sleep > 0:
                    self.sleep(after_sleep)

                if recheck_time > 0:
                    self.sleep(recheck_time)

                    if confirm := self.find_confirm():
                        self.click(confirm)
                        if disappear_time_out > 0:
                            self.wait_until(
                                lambda: not self.find_confirm(),
                                time_out=disappear_time_out,
                                raise_if_not_found=False,
                            )
                        if after_sleep > 0:
                            self.sleep(after_sleep)

                return True
            # 超时检测
            if self.active_time() - start_time > time_out:
                self.log_info("点击确认超时")
                return False

            self.sleep(0.01)

    def wait_pop_up(self, time_out=15, after_sleep=0):
        """
        等待奖励弹窗出现并点击 OK 按钮。
        使用 click_feature 持续点击直到弹窗消失。

        Args:
            time_out: 总超时时间。
            after_sleep: 点击后的延迟时间。

        Returns:
            bool: 找到并点击返回 True，超时返回 False。
        """
        clicked = self.click_feature(
            fL.reward_ok,
            boxes=[self.box.bottom],
            time_out=time_out,
            after_sleep=after_sleep,
        )
        if clicked:
            return True
        result = self.wait_ocr(
            match=self.lang.game_flow_mixin.k_8b2ca27a,
            time_out=1,
            box=self.box.bottom,
        )
        if result:
            self.click(result, after_sleep=after_sleep)
            return True
        return False

    def wait_login(self):
        """
        处理登录界面的各种弹窗（月卡、签到、奖励等）。

        Returns:
            bool: 已进入主界面或已成功处理登录弹窗时返回 True，否则返回 False。
        """
        close = None
        if not self._logged_in:
            if self.in_world():
                self._logged_in = True
                return True
            elif self.find_one(fL.monthly_card) or self.find_one(fL.monthly_card2) or self.find_one(fL.logout) or self.find_one(fL.login_reward_icon):
                run_at_window_pos(self.get_game_hwnd(), super().click, self.width // 2, self.height // 2, 1, 0.5, 0.5)
                return False
            elif close := (
                    self.find_one(fL.one_click_claim, horizontal_variance=0.1, vertical_variance=0.1)
                    or self.find_one(
                fL.check_in_close,
                horizontal_variance=0.1,
                vertical_variance=0.1,
                threshold=0.75,
            )
            ):
                self.click(close)
                return False
        return False

    def find_f(self):
        """
        寻找 F 键提示（拾取物品）。

        Returns:
            list: 匹配到的 F 键提示结果列表。
        """
        return self.find_one(fL.pick_f, vertical_variance=0.05)

    def in_combat_world(self):
        """
        判断是否在战斗场景中。

        Returns:
            bool: 检测到战斗场景返回 True。
        """
        in_combat_world = self.find_one(fL.top_left_tab)
        if in_combat_world:
            self._logged_in = True
        return in_combat_world

    def ensure_main(self, esc=True, time_out=90, after_sleep=0):
        """
        确保回到主界面（游戏世界）。

        Args:
            esc: 是否在失败时执行返回键处理。
            time_out: 等待主界面的总超时时间。
            after_sleep: 成功后额外等待时间。

        Returns:
            None

        Raises:
            Exception: 当无法回到主界面时抛出。
        """
        self.check_resolution()
        self.info_set("current task", self.tr("wait main esc={esc}").format(esc=esc))
        start = self.active_time()
        observe_time = min(2.0, time_out)

        # Give loading and return animations a chance to finish before recovery input.
        result = self.wait_until(
            lambda: self.is_main(esc=False),
            time_out=observe_time,
            settle_time=1.0,
            raise_if_not_found=False,
        )
        if not result and self.active_time() - start < time_out:
            self._next_main_recovery_time = self.active_time()
            result = self.wait_until(
                lambda: self.is_main(esc=esc),
                time_out=max(0.01, time_out - (self.active_time() - start)),
                settle_time=1.0,
                raise_if_not_found=False,
            )

        if not result:
            raise Exception("Please start in game world and in team!")
        if after_sleep > 0:
            self.sleep(after_sleep)
        self.info_set("current task", self.tr("in main esc={esc}").format(esc=esc))

    def in_world(self):
        """
        判断是否在游戏世界中（非菜单/对话状态）。

        Returns:
            bool: 当前处于游戏世界返回 True。
        """
        main_world_features = [fL.esc]

        in_world = all(self.find_one(f, vertical_variance=0.01, horizontal_variance=0.02) for f in main_world_features)

        if in_world:
            self._logged_in = True

        return in_world

    def is_main(self, esc=False):
        """
        判断是否处于可执行任务的主界面状态。

        Args:
            esc: 是否在处理失败时按返回键。

        Returns:
            bool: 处于主界面返回 True，否则返回 False。
        """

        self.next_frame()

        # Stability is handled by ensure_main's outer wait.
        if self.in_world():
            self._logged_in = True
            return True

        # 登录流程处理成功
        if self.wait_login():
            return True

        # # 对话/剧情中：右上角跳过图标，使用与 skip_dialog 一致的 ESC 返回键
        # if self.find_one(fL.skip_dialog_esc, horizontal_variance=0.05):
        #     self.log_info("检测到对话跳过图标，按 ESC 退出对话")
        #     self.press_esc()
        #     self._next_main_recovery_time = self.active_time() + self.once_sleep_time
        #     return False                                                                                                        

        # 某些弹窗状态不视为主界面
        if result := (
            self.find_one(
                feature=fL.skip_dialog_confirm,
                horizontal_variance=0.05,
                vertical_variance=0.05
            )
            or self.find_one(
                feature=fL.to_max_produce_num,
                box=self.box_of_screen(0.550, 0.885, 0.573, 0.950)
            )
            or self.find_one(
                feature=fL.left_battle,
                vertical_variance=0.1,
            )
            or self.find_one(
                feature=fL.log_out_confirm,
                box=self.box_of_screen(0.451, 0.572, 0.553, 0.620)
            )
            or self.find_one(
                feature=fL.reward_ok,
                horizontal_variance=0.1,
                vertical_variance=0.1,
            )
        ):
            self.log_info("检测到特定弹窗，尝试点击确认")
            self.click(result)
            self._next_main_recovery_time = self.active_time() + self.once_sleep_time
            return False

        # 命中 OCR 干扰并进行了处理，当前不视为稳定主界面
        rules = [[
            None,
            None,
            [self.lang.game_flow_mixin.k_8b2ca27a, self.lang.game_flow_mixin.k_7cd2e0c0],
            self.box.bottom
        ]]

        if self.handle_ocr_rules(rules):
            return False

        if esc and self.active_time() >= getattr(self, "_next_main_recovery_time", 0):
            self.log_info("主界面自然恢复等待结束，发送返回键")
            self.back()
            self._next_main_recovery_time = self.active_time() + self.once_sleep_time

        return False


    def handle_ocr_rules(self, rules: list[list]) -> bool:
        """
        OCR 规则处理。

        Args:
            rules: OCR 规则列表，格式为 [need, need_box, match, box]。

        Returns:
            True: 命中规则并已处理
            False: 未命中任何规则
        """

        for need, need_box, match, box in rules:

            # 前置条件检测
            if need is not None and not self.ocr(match=need, box=need_box, log=True):
                continue

            # 命中目标
            if result := self.ocr(match=match, box=box, log=True):
                self.click_with_alt(result, after_sleep=self.once_sleep_time)
                return True

        return False

    def enter_home_room_list(self, time_out=6):
        """
        进入基地房间列表页面（i 面板）。

        Args:
            time_out: OCR 等待超时时间。

        Returns:
            bool: 进入成功返回 True，否则返回 False。
        """
        self.log_info("进入基地房间列表页面")

        self.transfer_to_home_point(should_check_out_boat=True)
        self.press_key("i")
        results = self.wait_feature(feature=fL.operation_report_icon, time_out=time_out, raise_if_not_found=False)
        if results:
            self.log_info("已进入房间列表")
            return True

        self.log_info("未识别到房间列表")
        return False

    def to_model_area(self, area, model=None):
        """
        导航到指定区域的特定模块。

        Args:
            area: 目标区域。
            model: 目标模块名称。

        Returns:
            bool: 定位并点击成功返回 True，否则返回 False。
        """
        need_change = True
        success = False

        for _ in range(3):
            # safe_back 可能已经把页面留在地区建设总览，先复用现有页面，避免再次按 Y 将其关闭。
            check = self.wait_feature(
                feature=fL.transaction_overview,
                time_out=0.5,
                raise_if_not_found=False,
            )
            if not check:
                self.press_key("y")
                check = self.wait_feature(
                    feature=fL.transaction_overview,
                    time_out=5,
                    raise_if_not_found=False,
                )
            if not check:
                self.log_info("未识别到区域且未检测到建设，重新尝试打开界面")
                continue
            result = self.wait_ocr(match=[get_world_map_matcher(self.lang, area) for area in areas_list], box=self.box.left, time_out=1)
            if result:
                success = True
                break
            self.log_info("未识别到区域且未检测到建设，重新尝试打开界面")

        if not success or not result:
            self.log_error("未能识别到区域列表")
            return False
        expected_area_text = get_world_map_text(self.lang, area)
        for i in result:
            if expected_area_text in i.name or area in i.name:
                need_change = False
                break
        if need_change:
            if not self.wait_click_feature(
                    feature=fL.change_area, time_out=2, raise_if_not_found=False
            ):
                return False
            if not self.wait_click_ocr(
                    match=get_world_map_matcher(self.lang, area),
                    box=self.box_of_screen(
                        648 / 1920, 196 / 1080, 648 / 1920 + 628 / 1920, 196 / 1080 + 192 / 1080
                    ),
                    time_out=4,
            ):
                return False
            if not self.wait_click_feature(
                    feature=fL.to_max_produce_num,
                    box=self.box_of_screen(0.636, 0.585, 0.656, 0.622),
                    time_out=2,
                    raise_if_not_found=False
            ):
                return False

        if not model:
            self.wait_ui_stable()
            return True

        models = {
            "物资调度": fL.material_dispatch_enter,
            "仓储节点": fL.warehouse_node_enter,
            "据点管理": fL.outpost_manager_enter,
        }
        
        if box := self.wait_feature(
            feature=models.get(model),
            box=self.box.right,
            time_out=5,
            settle_time=0.5,
            raise_if_not_found=False
        ):
            self.click(box)
            self.wait_ui_stable()
            return True
        else:
            self.log_error(f"未找到‘{model}’按钮，任务中止。")
            return False

    def switch_to_area_delivery_list(self, target_area):
        """
        切换到指定区域的交付列表。

        Args:
            target_area: 目标区域。

        Returns:
            bool | None: 成功时返回 True，未命中时返回 None。
        """
        if result := self.wait_ocr(match=[get_world_map_matcher(self.lang, area) for area in areas_list],
                                   box=self.box_of_screen(0, 960 / 1080, 260 / 1920, 1), time_out=5):
            expected_target_text = get_world_map_text(self.lang, target_area)
            if expected_target_text in result[0].name or target_area in result[0].name:
                return True
            else:
                self.click(result[0])
                self.wait_click_ocr(match=get_world_map_matcher(self.lang, target_area),
                                    box=self.box_of_screen(0, (960 - 60 * len(areas_list)) / 1080, 260 / 1920, 1),
                                    time_out=5)
                return True

    def ensure_map(self, addtional_feature=None, time_out=30):
        """
        确保进入地图界面。

        Args:
            addtional_feature: 额外需要检测的特征。
            time_out: 总超时时间。

        Returns:
            None

        Raises:
            Exception: 当超时仍未进入地图时抛出。
        """
        start_time = self.active_time()
        default_features = [fL.transaction_icon, fL.main_centre_icon]
        if addtional_feature:
            features = default_features + addtional_feature if isinstance(addtional_feature, list) else default_features + [addtional_feature]
        else:
            features = default_features
        def in_map():
            if self.find_one(fL.in_map, box=self.box_of_screen(0.027, 0.531, 0.051, 0.896)):
                return True
            return any(self.find_one(feature) for feature in features)

        if self.wait_until(in_map, time_out=min(0.5, time_out), raise_if_not_found=False):
            return

        while self.active_time() - start_time < time_out:
            self.log_info("未检测到地图状态，发送地图按键")
            self.press_key("m")
            remaining = time_out - (self.active_time() - start_time)
            if self.wait_until(in_map, time_out=max(0.01, min(2.0, remaining)), raise_if_not_found=False):
                return

        raise Exception("进入地图失败")

    def in_friend_boat(self):
        """
        判断是否在好友的帝江号舰船中。

        Returns:
            bool: 检测到好友帝江号返回 True。
        """
        return self.wait_feature(feature=fL.left_button, vertical_variance=0.1, horizontal_variance=0.1, raise_if_not_found=False, time_out=1) is not None

    def ensure_in_friend_boat(self):
        """
        确保进入好友帝江号舰船。

        Returns:
            bool: 成功进入返回 True，超时返回 False。
        """
        start_time = self.active_time()
        while True:
            if self.active_time() - start_time > 60:
                self.log_info("进入好友帝江号超时")
                return False
            if self.in_friend_boat():
                return True
