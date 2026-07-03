from src.data.FeatureList import FeatureList as fL

class DailyDemoFeature:    
    def __init__(self, task):
        self._task = task
        task.default_config.update({
            "⭐演算": True,
        })
        task.config_description.update({
            "⭐演算": "是否执行演武集算任务"
        })
        self.left_time = True

    def __getattr__(self, name):
        return getattr(self._task, name)

    def battle_demo(self):
        if not self.go_to_DemoGraphic():
            return False
        if not self.left_time:
            self.log_info("没有次数了，结束任务")
            return True
        if not self._demo_click_track_and_transfer():
            return False
        if not self.enter_page():
            return False
        max_time = 3
        once_double_reward = False
        for i in range(max_time):
            level = self.read_level()
            if level < 0:
                return False
            refresh_times = 0
            this_time_double_reward = False
            while level <= 5:
                level = self.click_random_and_wait_level_change(level)
                if level < 0:
                    return False
                refresh_times += 1
                if refresh_times == 3:
                    self.click_confirm(time_out=2, after_sleep=1)
                if ((refresh_times == 2 and level >=8) or once_double_reward) and not this_time_double_reward:
                    self.log_info("已刷新2次，当前关卡较高，开启双倍奖励")
                    self.wait_click_feature(feature=fL.demo_double_open, time_out=10, raise_if_not_found=False)
                    this_time_double_reward = True
            if not this_time_double_reward:
                once_double_reward = True
            self.wait_click_feature(feature=fL.start_demo, time_out=10, raise_if_not_found=False, click_after_delay=0.5)
            if not self.wait_click_feature(feature=fL.give_gift, time_out=10, raise_if_not_found=False, click_after_delay=0.5, box=self.box_of_screen(0.944, 0.900, 0.969, 0.941), after_sleep=2):
                self.mark_task_failure("未找到进入战斗按钮")
                return False
            self.ensure_main()
            self.auto_battle()
            if not self.wait_click_feature(feature=fL.restart_battle, vertical_variance=0.1, time_out=10, raise_if_not_found=False):
                self.mark_task_failure("未找到『重新挑战』按钮，可能战斗尚未结束")
                return False
        self.click_confirm(time_out=3)
        return True

    def click_random_and_wait_level_change(self, previous_level, max_retry=3):
        for retry_index in range(max_retry):
            if not self.wait_click_feature(
                    feature=fL.demo_random_button,
                    time_out=10,
                    raise_if_not_found=False,
                    click_after_delay=0.5,
                    after_sleep=1,
            ):
                self.log_warning("未找到演算随机按钮")
                return -1

            current_level = self.read_level()
            if current_level < 0:
                return -1
            if current_level != previous_level:
                return current_level

            self.log_warning(f"第 {retry_index + 1} 次点击随机按钮后等级未变化，重试点击")

        self.mark_task_failure("点击随机按钮后等级未变化，可能未点中按钮")
        return -1

    def go_to_DemoGraphic(self):
        self.ensure_main()
        self.to_model_area("武陵")
        if not self.wait_click_feature(fL.transaction_overview, time_out=5, raise_if_not_found=False):
            self.mark_task_failure("未能进入总览界面")
            return False
        self.wait_ui_stable(refresh_interval=1)
        demo_enter = None
        for _ in range(4):
            if result := self.wait_feature(feature=[fL.daily_demo_enter, fL.demo_left_time], box=self.box_of_screen(0.146, 0.094, 0.179, 0.898), time_out=2, raise_if_not_found=False):
                demo_enter = result
                break
            else:
                self.log_info("未找到『演算』入口，尝试滑动")
                self.scroll_relative(0.5, 0.5, -2)
        if not demo_enter:
            self.mark_task_failure("未找到『演算』入口，可能界面未加载完全")
            return False
        if demo_enter.name == fL.daily_demo_enter:
            self.left_time = False
            return True
        for _ in range(2):
            if self.wait_click_feature(feature=fL.view_location, time_out=10, raise_if_not_found=False, click_after_delay=0.5, box=self.box_of_screen(0.5, demo_enter.y/self.height, 1, demo_enter.y/self.height + (0.272 - 0.109))):
                break
            self.click(demo_enter, after_sleep=2)
        return True
    
        
    def _demo_click_track_and_transfer(self):
        """点击『追踪』按钮，进入地图并传送至最近传送点。"""
        if result := self.wait_feature(feature=fL.start_follow, box=self.box.bottom_right, time_out=5, raise_if_not_found=False):
            self.click(result, after_sleep=1)
        else:
            self.log_info("未找到『追踪』按钮，继续尝试自动寻路")
        if not self.to_near_transfer_point(self.box.bottom_left):
            self.mark_task_failure("未能找到传送点，无法继续")
            return False
        self.ensure_main()
        if not self.align_ocr_or_find_target_to_center(ocr_match_or_feature_name_list=fL.demographic_follow, ocr=False, raise_if_fail=False):
            self.mark_task_failure("未找到『进入演算』目标，可能尚未找到正确路线")
            return False
        if not self.navigate_until_target(target=fL.enter_demo, nav=fL.demographic_follow, target_is_ocr=False, target_vertical_variance=0.05):
            self.mark_task_failure("未能进入『进入演算』目标，可能尚未找到正确路线")
            return False
        return True
        


    def enter_page(self):
        """进入关卡选择界面，等待UI稳定。"""
        result= self.wait_feature(feature=fL.enter_demo, time_out=10, raise_if_not_found=False, box=self.box_of_screen(0.653, 0.574, 0.679, 0.817), settle_time=1)
        if not result:
            self.mark_task_failure("未找到『进入演算』按钮，可能还没到关卡入口页")
            return False
        self.click_with_alt(result, after_sleep=1)
        self.wait_ui_stable(refresh_interval=1)
        return True

    def read_level(self):
        start_x = 0.125 #等级信息区域左边界占屏幕宽度的比例
        end_x = 0.802 #等级信息区域右边界占屏幕宽度的比例
        level_all = 11 #总共的等级数，从0级到10级
        result= self.wait_feature(feature=fL.level_tip, time_out=10, raise_if_not_found=False, box=self.box_of_screen(0.120, 0.724, 0.803, 0.750), settle_time=1)
        if not result:
            self.mark_task_failure("未找到等级信息标志，可能没有进入演武集算关卡界面")
            return -1
        level_x = result.x 
        one_level_width = (end_x - start_x) / level_all #每个等级占的宽度占屏幕宽度的比例
        level = int((level_x - self.screen_width * start_x) / (self.screen_width * one_level_width)) #根据等级信息标志的x坐标计算当前等级
        self.log_info(f"当前等级: {level}")
        self.log_info(
            f"x={level_x}, ratio={(level_x - self.screen_width * start_x) / (self.screen_width * one_level_width):.2f}, level={level}"
        )
        return level
