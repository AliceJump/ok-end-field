"""百趣演武：从当前界面直接执行演武的抽牌与战斗步骤。

活动「百趣演武」延续了选剑演武的「抽取 + 战斗」玩法，界面与日常演算基本一致，
差别主要在奖励与风险。因此这里不做任何导航：

1. 先手动把游戏切到活动的演武界面（抽牌页，能看到等级条）；
2. 再运行本任务，它只做「读等级 → 抽牌 → 判断 → 开打 / 放弃重开」。

抽牌策略
--------
- **选剑（默认）**：沿用「演算抽牌」的判定——整轮只掉一级且最终停在第 10 级，
  这类牌面的收益最高；不满足就放弃重开。
- **快速**：沿用日常演算的判定——等级抽到 5 级以上就直接开打。

两者都支持「自动战斗」，关闭时只负责抽牌，方便手动接管。
"""


from src.data.FeatureList import FeatureList as fL
from src.icons import Icons
from src.tasks.mixin.battle_mixin import BattleMixin
from src.tasks.mixin.common import Common
from src.tasks.mixin.demo_level_mixin import DemoLevelMixin

#: 抽牌次数上限（与演算一致：一轮最多抽 5 张）
MAX_DRAWS = 5
#: 总共的等级数，0~10 级
MAX_LEVEL = 10
#: 快速策略的开打等级
FAST_BATTLE_LEVEL = 5


class DemoArenaTask(DemoLevelMixin, BattleMixin, Common):
    """百趣演武：假定已在演武界面，只跑抽牌与战斗。"""

    CFG_MODE = "抽牌策略"
    CFG_BATTLE = "自动战斗"
    CFG_MAX_RESTART = "最多重开次数"

    MODE_XUANJIAN = "选剑（只降一级且停在第10级）"
    MODE_FAST = "快速（等级过 5 就开打）"
    MODES = (MODE_XUANJIAN, MODE_FAST)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "百趣演武"
        self.icon = Icons.SwordChallenge
        self.group_name = "战斗"
        self.description = (
            "活动百趣演武用：先手动进入活动的演武界面，再运行本任务，按所选策略抽牌，"
            "满足条件后开打（或只抽牌交给你手动打）。"
        )
        self.default_config.update(
            {
                self.CFG_MODE: self.MODE_XUANJIAN,
                self.CFG_BATTLE: True,
                self.CFG_MAX_RESTART: 30,
            }
        )
        self.config_type[self.CFG_MODE] = {"type": "drop_down", "options": list(self.MODES)}
        self.config_description.update(
            {
                self.CFG_MODE: (
                    "选剑：整轮只掉一级且最终停在第 10 级才开打，收益最高但重开次数多。\n"
                    "快速：等级抽到 5 级以上就开打，省时间。"
                ),
                self.CFG_BATTLE: "开启后满足条件直接进入战斗；关闭则只抽牌，战斗由你手动接管。",
                self.CFG_MAX_RESTART: "不满足条件时最多放弃重开多少次。",
            }
        )

    def run(self):
        mode = self.config.get(self.CFG_MODE, self.MODE_XUANJIAN)
        auto_battle = bool(self.config.get(self.CFG_BATTLE, True))
        max_restart = int(self.config.get(self.CFG_MAX_RESTART, 30) or 0)
        if mode not in self.MODES:
            mode = self.MODE_XUANJIAN

        if self.read_level() < 0:
            self.log_error("没有看到演武的等级条，请先手动进入百趣演武的抽牌界面再运行")
            return

        for restart in range(max(1, max_restart)):
            if self.exit_is_set():
                return
            if self.draw_pass(mode):
                self.log_info(f"第 {restart + 1} 轮抽牌满足条件（{mode}）", notify=True)
                if not auto_battle:
                    self.log_info("已关闭自动战斗，抽牌到此为止", notify=True)
                    return
                self.fight_once()
                return
            self.log_info(f"第 {restart + 1} 轮未满足条件，放弃后重开")
            if not self.give_up_and_restart():
                return
        self.log_warning(f"已达到最多重开次数 {max_restart}，停止", notify=True)

    # ---------- 抽牌 ----------

    def draw_pass(self, mode):
        """抽满一轮并按策略判定是否值得开打。"""
        level = self.read_level()
        if level < 0:
            return False
        down_count = 0
        for _ in range(MAX_DRAWS):
            if not self.wait_click_feature(
                feature=fL.demo_random_button,
                time_out=10,
                raise_if_not_found=False,
                click_after_delay=0.5,
            ):
                self.log_warning("未找到抽牌按钮，可能不在演武抽牌界面")
                return False
            current = self.wait_level_change(level, time_out=4)
            if current is None:
                self.log_warning("抽牌后等级没有变化")
                return False
            if current < level:
                down_count += 1
            level = current
            self.log_info(f"当前等级 {level}，掉级次数 {down_count}")
            if mode == self.MODE_FAST and level > FAST_BATTLE_LEVEL:
                return True
        return self.is_good_draw(mode, down_count, level)

    @staticmethod
    def is_good_draw(mode, down_count, level):
        """选剑：只掉一级且停在第 10 级；快速：等级超过阈值。"""
        if mode == DemoArenaTask.MODE_FAST:
            return level > FAST_BATTLE_LEVEL
        return down_count == 1 and level == MAX_LEVEL

    def give_up_and_restart(self):
        if not self.wait_click_ocr(
            match=self.lang.daily_battle_mixin.k_b8a81b7a,
            box=self.box.bottom_left,
            time_out=5,
            raise_if_not_found=False,
        ):
            self.log_warning("未找到左下角『放弃』按钮")
            return False
        self.wait_ui_stable(refresh_interval=1)
        return True

    # ---------- 战斗 ----------

    def fight_once(self):
        if not self.wait_click_feature(
            feature=fL.start_demo, time_out=10, raise_if_not_found=False, click_after_delay=0.5
        ):
            self.log_warning("未找到开始演算按钮")
            return False
        if not self.wait_click_feature(
            feature=fL.give_gift,
            time_out=10,
            raise_if_not_found=False,
            settle_time=1,
            box=self.box_of_screen(0.944, 0.900, 0.969, 0.941),
        ):
            self.log_warning("未找到进入战斗按钮", images=[self.frame])
            return False
        self.ensure_main()
        self.auto_battle()
        self.log_info("战斗结束，百趣演武本次完成", notify=True)
        return True
