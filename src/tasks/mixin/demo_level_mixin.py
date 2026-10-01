from src.data.FeatureList import FeatureList as fL


class DemoLevelMixin:
    """演武集算共用的等级识别与变化等待。"""

    def read_level(self):
        result = self.wait_feature(
            feature=fL.level_tip,
            time_out=10,
            raise_if_not_found=False,
            box=self._level_tip_box(),
            settle_time=1,
        )
        if not result:
            self.mark_task_failure("未找到等级信息标志，可能没有进入演武集算关卡界面")
            return -1
        return self._level_from_tip(result)

    def wait_level_change(self, previous_level, time_out=4):
        changed = {"level": None}

        def level_changed():
            result = self.find_one(fL.level_tip, box=self._level_tip_box())
            if not result:
                return False
            current = self._level_from_tip(result)
            if current == previous_level:
                return False
            changed["level"] = current
            return True

        if self.wait_until(level_changed, time_out=time_out, settle_time=0.2, raise_if_not_found=False):
            return changed["level"]
        return None

    def _level_tip_box(self):
        return self.box_of_screen(0.120, 0.724, 0.803, 0.750)

    def _level_from_tip(self, result):
        start_x = 0.125  # 等级信息区域左边界占屏幕宽度的比例
        end_x = 0.802  # 等级信息区域右边界占屏幕宽度的比例
        level_all = 11  # 总共的等级数，从0级到10级
        level_x = result.x
        one_level_width = (end_x - start_x) / level_all  # 每个等级占的宽度占屏幕宽度的比例
        level = int(
            (level_x - self.screen_width * start_x) / (self.screen_width * one_level_width)
        )  # 根据等级信息标志的x坐标计算当前等级
        self.log_info(self.tr("当前等级: {level}").format(level=level))
        self.log_info(
            self.tr("x={x}, ratio={ratio:.2f}, level={level}").format(
                x=level_x,
                ratio=(level_x - self.screen_width * start_x) / (self.screen_width * one_level_width),
                level=level,
            )
        )
        return level
