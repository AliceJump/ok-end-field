import math
import re
import time
import traceback
from datetime import datetime
from dataclasses import dataclass

from ok import TaskDisabledException
from src.data.FeatureList import FeatureList as fL
from src.data.world_map import stages_cost, higher_order_feature_dict, STAGE_CATEGORY_ENERGY_POOLING, \
    STAGE_CATEGORY_DANGER_REHEARSAL
from src.data.world_map import stages_dict, stages_list
from src.data.world_map_utils import get_stage_category, get_world_map_matcher, is_world_map_text
from src.tasks.sequence_parser import parse_int_sequence, parse_sequence
from src.tasks.mixin.battle_mixin import BattleMixin
from src.tasks.mixin.common import Common
from src.tasks.mixin.map_mixin import MapMixin
from src.tasks.mixin.zip_line_mixin import ZipLineMixin

gather_list = stages_dict[STAGE_CATEGORY_ENERGY_POOLING]


@dataclass
class BattleContext:
    """
    战斗上下文类，用于存储和管理战斗相关的状态信息
    """
    stage_name: str = ""  # 关卡名称
    category_name: str = ""  # 战斗类别名称

    left_ticket: int = 0  # 剩余挑战券数量

    extra_run_limit: int = 0  # 额外运行次数限制
    extra_run_count: int = 0  # 已额外运行次数
    is_extra_mode: bool = False  # 是否处于额外模式

    enter_text: str = ""  # 进入文本内容
    no_battle: bool = False  # 是否不进行战斗

    stage_reward_tier_override: str | None = None  # 关卡奖励等级覆盖
    ignore_config_reward_tier: bool = False  # 是否忽略配置的奖励等级
    today_reward_tier: str = ""  # 今日奖励等级


class DailyBattleMixin(MapMixin, ZipLineMixin, BattleMixin, Common):
    """
    日常战斗混合类，用于处理日常战斗任务，包括刷取体力副本、处理奖励档位切换等功能。
    继承自多个Mixin类，提供地图导航、滑索使用和战斗相关的功能。
    """
    # 配置项常量定义
    CFG_SCROLL_ENABLE = "是否启用滚动放大视角"  # 是否启用滚动放大视角的配置项名称
    CFG_STAGE_REWARD_TIER = "体力本奖励档位"
    REWARD_TIER_KEEP = "保持当前"
    REWARD_TIER_LOW = "低阶"
    REWARD_TIER_HIGH = "高阶"
    REWARD_TIER_STAGE_SET = {"干员经验", "干员进阶", "技能提升", "武器进阶"}
    CFG_PRE_ENTER_TEAM_SWITCH = "指定的队伍编号"
    PRE_ENTER_TEAM_SWITCH_NONE = "不换队伍"
    PRE_ENTER_TEAM_SWITCH_TEAM_OPTIONS = ["1", "2", "3", "4", "5"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.gather_near_transfer_point_dict = dict()
        self.stages_list = stages_list
        self._reset_battle_state()
        # 下列代码在 AutoCombatTask.py 中有部分重复。如有更新，请两边一起修改。
        # 不要试图归并，否则会影响『日常任务』中的选项顺序。
        today_str = datetime.now().strftime("%Y-%m-%d")
        self.default_config.update({
            "⭐刷体力": True,
            "消耗限时体力药": False,
            "体力本": "干员经验",
            self.CFG_STAGE_REWARD_TIER: self.REWARD_TIER_KEEP,
            "刷体力开始日期": today_str,  # 默认当天，可自定义
            "刷本序列": [],  # 为空表示不启用自动轮换
            "仅站桩": False,
            "体力刷完后继续刷取次数": 0,
            self.CFG_SCROLL_ENABLE: False,
            **{key: "" for key in gather_list},
            "技能释放": ["1", "2", "3"],
            "启动技能点数": 2,
            "后台结束战斗通知": True,
            "无数字操作间隔": 6,
            "进入战斗后的初始等待时间": 3,
            self.CFG_PRE_ENTER_TEAM_SWITCH: self.PRE_ENTER_TEAM_SWITCH_NONE,
            "启用排轴": False,
            "排轴序列": "ult_2,1,e,ult_3,sleep_8",
        })
        self.default_config_group.update(
            {
                "⭐刷体力": [
                    "消耗限时体力药",
                    "体力本",
                    "体力本配置",
                    # 如果要把 gather_list 里的 key 也折叠
                    "战斗相关选项",
                    "淤积点相关选项",
                ],
                "体力本配置": [self.CFG_STAGE_REWARD_TIER, "刷体力开始日期", "刷本序列"],
                "淤积点相关选项": ["仅站桩", "体力刷完后继续刷取次数", self.CFG_SCROLL_ENABLE]
                                  + [key for key in gather_list],
                "战斗相关选项": [
                    "技能释放",
                    "启动技能点数",
                    "后台结束战斗通知",
                    "无数字操作间隔",
                    "进入战斗后的初始等待时间",
                    self.CFG_PRE_ENTER_TEAM_SWITCH,
                    "启用排轴",
                    "排轴序列",
                ],
            }
        )

        self.config_description.update({
            "⭐刷体力": (
                "是否消耗所有「理智」刷取培养材料。"
            ),
            "消耗限时体力药": (
                "如果勾选，将优先全部用掉限时为‘小时’的体力药，\n"
                "如有‘天’单位的体力药，则按 2*m/n（向上取整）消耗。"
            ),
            "体力本": (
                "刷取哪个副本。所选副本必须领完所有等级的首通奖励。"
            ),
            self.CFG_STAGE_REWARD_TIER: (
                "用于『干员经验/干员进阶/技能提升/武器进阶』的奖励档位选择。\n"
                "保持当前：不切换。\n"
                "低阶/高阶：点击『前往』后自动打开『自选』并切换。"
            ),
            "仅站桩": (
                "若启用，则开始挑战后角色原地不动（不输出），\n"
                "仅对「重度能量淤积点」生效。可以用于建好防御塔情形，避免角色离开副本区域。"
            ),
            "刷体力开始日期": (
                "刷体力自动切换的起始日期，格式如2026-04-05。\n"
                "用于计算今天是第几天，配合刷本序列使用。"
            ),
            "刷本序列": (
                f"会根据开始日期自动轮换，留空表示不启用自动轮换。"
            ),
            "体力刷完后继续刷取次数": (
                "结算时点击「放弃」而非「领取」，不消耗体力。\n"
                "只支持能量淤积点。\n"
                "0 表示不启用。"
            ),
            self.CFG_SCROLL_ENABLE: (
                "启用后在对齐滑索时会自动滚动放大视角\n"
                "可能会提高对齐成功率，但也可能导致对齐成功率下降较为明显\n"
                "建议启用此项时不要使用非白发或有白帽角色"
            ),
            self.CFG_PRE_ENTER_TEAM_SWITCH: (
                "选择要更换的队伍编号。"
            ),
            **{key: (
                "需要设好「预刻写属性」。默认留空表示不使用滑索前往，\n"
                "更多用法参见 ./docs/体力本.md > 能量淤积点 。"
            ) for key in gather_list},
        })
        # 刷本序列选项：包括基础副本名 + 支持档位调整的副本的高阶/低阶版本
        stage_options_with_tiers = []
        for stage in self.stages_list:
            stage_options_with_tiers.append(stage)
            if stage in self.REWARD_TIER_STAGE_SET:
                stage_options_with_tiers.append(f"{stage}{self.REWARD_TIER_LOW}")
                stage_options_with_tiers.append(f"{stage}{self.REWARD_TIER_HIGH}")
        self.config_type["刷本序列"] = {"options_available": stage_options_with_tiers, "allow_duplication": True}
        self.config_type["体力本"] = {"type": "drop_down", "options": self.stages_list}
        self.config_type[self.CFG_STAGE_REWARD_TIER] = {
            "type": "drop_down",
            "options": [self.REWARD_TIER_KEEP, self.REWARD_TIER_LOW, self.REWARD_TIER_HIGH]
        }
        self.config_type[self.CFG_PRE_ENTER_TEAM_SWITCH] = {
            "type": "drop_down",
            "options": [self.PRE_ENTER_TEAM_SWITCH_NONE] + self.PRE_ENTER_TEAM_SWITCH_TEAM_OPTIONS,
        }

    def _switch_team_before_reenter(self):
        mode = str(self.config.get(self.CFG_PRE_ENTER_TEAM_SWITCH, self.PRE_ENTER_TEAM_SWITCH_NONE) or "").strip()
        if mode == self.PRE_ENTER_TEAM_SWITCH_NONE:
            return
        if mode not in self.PRE_ENTER_TEAM_SWITCH_TEAM_OPTIONS:
            self.log_info(f"未知换队模式: {mode}，已跳过")
            return

        selected_team = mode
        expected_text = f"0{selected_team}"
        team_switch_box = self.box_of_screen(0.0, 968 / 1080, 650 / 1920, 1.0, name="team_switch_left_bottom")
        expected_pattern = re.compile(rf"\b{re.escape(expected_text)}\b")

        self.log_info(f"准备换队: 指定目标={expected_text}")
        results = self.wait_ocr(
            match=expected_pattern,
            box=team_switch_box,
            time_out=3,
            raise_if_not_found=False,
        )
        if not results:
            self.log_info(f"未识别到换队伍文本: {expected_text}")
            return

        self.click(results[0], move_back=True, after_sleep=1.5)
        self.log_info(f"已识别并点击换队伍文本: {expected_text}")

    def _switch_team_before_activate_for_gather(self):
        mode = str(self.config.get(self.CFG_PRE_ENTER_TEAM_SWITCH, self.PRE_ENTER_TEAM_SWITCH_NONE) or "").strip()
        if mode == self.PRE_ENTER_TEAM_SWITCH_NONE:
            return True

        self.ensure_main()
        self.press_key("u", after_sleep=2)

        # 先换队，再处理出战按钮。
        self._switch_team_before_reenter()

        deploy_results = self.wait_ocr(
            match=[self.lang.daily_battle_mixin.k_62b5b688, self.lang.daily_battle_mixin.k_12577cd1],
            box=self.box.bottom_right,
            time_out=3,
            raise_if_not_found=False,
            log=True,
        )
        if not deploy_results:
            self.log_info("换队后未识别到『出战/已出战』按钮")
            return False

        deploy_text = deploy_results[0].name
        if "已出战" in deploy_text:
            self.log_info("当前为『已出战』状态，跳过点击出战")
        else:
            self.click(deploy_results[0], move_back=True, after_sleep=0.8)
            self.log_info("已点击『出战』按钮")

        self.ensure_main()
        return True

    def _reset_battle_state(self):
        self.battle_ctx = BattleContext(today_reward_tier=self.REWARD_TIER_KEEP)

    @property
    def _battle_stage_cost(self):
        return stages_cost[self.battle_ctx.category_name]

    def _split_stage_name_and_reward_tier(self, raw_stage_name):
        stage_name = str(raw_stage_name or "").strip()
        if stage_name.endswith(self.REWARD_TIER_LOW):
            return stage_name[:-len(self.REWARD_TIER_LOW)].strip(), self.REWARD_TIER_LOW
        if stage_name.endswith(self.REWARD_TIER_HIGH):
            return stage_name[:-len(self.REWARD_TIER_HIGH)].strip(), self.REWARD_TIER_HIGH
        return stage_name, None

    def _format_stage_with_reward_tier(self, stage_name, reward_tier=None):
        if reward_tier in (self.REWARD_TIER_LOW, self.REWARD_TIER_HIGH):
            return f"{stage_name}{reward_tier}"
        return stage_name

    # ------------------------------------------------------------------ #
    #  公共辅助方法：消除 ⚠️ 标注的重复代码
    # ------------------------------------------------------------------ #

    def _open_index(self):
        """F8 打开索引页面。"""
        self.ensure_main()
        self.press_key("f8")
        self.wait_click_ocr(match=self.lang.daily_battle_mixin.k_79f91106, time_out=7, after_sleep=2, box=self.box.top, log=True)

    def _click_track_and_transfer(self):
        """点击『追踪』按钮，进入地图并传送至最近传送点。"""
        if result := self.wait_feature(feature=fL.start_follow, box=self.box.bottom_right, time_out=5, raise_if_not_found=False):
            self.click(result, after_sleep=1)
        self.to_near_transfer_point(self.gather_near_transfer_point_dict[self.battle_ctx.stage_name])
        self.ensure_main()

    def _navigate_via_zip_line(self):
        """若配置了滑索路线，则通过滑索移动至目标。"""
        zip_line_str = self.config.get(self.battle_ctx.stage_name)
        if zip_line_str:
            self.wait_click_ocr(match=self.lang.daily_battle_mixin.k_b0e3a2da, time_out=10, after_sleep=2, recheck_time=1,
                                box=self.box.bottom_right, log=True, alt=True)
            zip_line_list = parse_int_sequence(zip_line_str)
            self.zip_line_list_go(
                zip_line_list,
                need_scroll=self.config.get(self.CFG_SCROLL_ENABLE),
                target=([fL.gather_icon_out_map, fL.gather_icon_out_map2], "feature")
            )

    # ------------------------------------------------------------------ #
    #  battle() 及其子步骤
    # ------------------------------------------------------------------ #

    def _resolve_stage_from_sequence(self):
        """
        根据日期和刷本序列自动决定今日副本及奖励档位。
        """
        stage_name = self.config.get("体力本")
        stage_reward_tier_override = None
        ignore_config_reward_tier = False

        seq = self.config.get("刷本序列", [])
        self.log_info(f"检测到刷本序列配置: {seq if seq else '(空)'}")
        start_date = self.config.get("刷体力开始日期", "2026-04-05")
        auto_stage = None
        explain = ""

        try:
            if seq:
                seq_list = parse_sequence(seq)
                self.log_info(f"刷本序列解析结果: {seq_list}")

                seq_with_tier_list = [
                    self._format_stage_with_reward_tier(*self._split_stage_name_and_reward_tier(s))
                    for s in seq_list
                ]
                self.log_info(f"刷本序列标准化: {seq_with_tier_list}")

                # 如果有任何无效副本名，全部放弃自动轮换
                invalid_stages = []
                for raw_stage in seq_list:
                    parsed_stage_name, parsed_tier = self._split_stage_name_and_reward_tier(raw_stage)
                    if parsed_stage_name not in self.stages_list:
                        invalid_stages.append(raw_stage)
                    elif parsed_tier and parsed_stage_name not in self.REWARD_TIER_STAGE_SET:
                        invalid_stages.append(raw_stage)

                if invalid_stages:
                    explain = f"刷体力自动选择失败：刷本序列包含无效副本名 {invalid_stages}，已使用原配置体力本"
                else:
                    today = datetime.now().date()
                    try:
                        start = datetime.strptime(start_date, "%Y-%m-%d").date()
                    except Exception as e:
                        self.log_info(f"刷体力开始日期解析失败: {e}，已使用默认配置体力本")
                        self.battle_ctx.stage_name = stage_name
                        self.battle_ctx.stage_reward_tier_override = None
                        self.battle_ctx.ignore_config_reward_tier = False
                        return
                    if start_date and today < start:
                        explain = (
                            f"刷体力自动选择失败：开始日期 {start_date} 在未来"
                            f"（今天 {today}），已使用原配置体力本"
                        )
                    elif not seq_list:
                        explain = "刷体力自动选择失败：刷本序列为空，已使用原配置体力本"
                    else:
                        days = (today - start).days
                        idx = days % len(seq_list)
                        raw_auto_stage = seq_list[idx]
                        auto_stage, stage_reward_tier_override = self._split_stage_name_and_reward_tier(raw_auto_stage)
                        ignore_config_reward_tier = True
                        self.log_info(
                            f"今日刷本序列命中: 原始项={raw_auto_stage}, "
                            f"关卡={auto_stage}, 奖励档位={stage_reward_tier_override or self.REWARD_TIER_KEEP}"
                        )
                        tier_hint = f"，奖励档位：{stage_reward_tier_override}" if stage_reward_tier_override else ""
                        explain = (
                            f"刷体力自动选择: 今天是第{days + 1}天，"
                            f"命中序列项：{raw_auto_stage}，"
                            f"今日副本：{auto_stage}{tier_hint}。"
                        )
        except Exception as e:
            if isinstance(e, TaskDisabledException):
                raise
            self.log_info(f"刷体力自动选择异常: {e}\n{traceback.format_exc()}")

        if auto_stage:
            stage_name = auto_stage
            self.log_info(explain)
        else:
            self.log_info(explain or "刷体力自动选择失败，使用原配置体力本")
            if seq:
                fallback_tier = self.config.get(self.CFG_STAGE_REWARD_TIER, self.REWARD_TIER_KEEP)
                self.log_info(
                    f"刷本序列未生效，回退体力本配置: 关卡={stage_name}, "
                    f"奖励档位={fallback_tier if stage_name in self.REWARD_TIER_STAGE_SET else self.REWARD_TIER_KEEP}"
                )

        self.battle_ctx.stage_name = stage_name
        self.battle_ctx.stage_reward_tier_override = stage_reward_tier_override
        self.battle_ctx.ignore_config_reward_tier = ignore_config_reward_tier

    def _consume_stamina_potions(self):
        """
        消耗限时体力药。

        返回:
            bool: 成功（或不需要消耗）时返回 True，失败返回 False。
        """
        if not self.config.get("消耗限时体力药", False):
            return True

        self.wait_click_feature(
            feature=fL.stamina_plus_icon, vertical_variance=0.01, horizontal_variance=0.01,
            time_out=5,
            box=self.box_of_screen((3530 - 40) / 3840, 0, (3600) / 3840, (80 + 40) / 2160),
            raise_if_not_found=False,
        )  # 右上角加号
        self.wait_ocr(match=self.lang.daily_battle_mixin.k_6c4d77af, time_out=5, box=self.box.top_left)
        # 支持天和小时单位，按剩余时效升序消耗
        box_list = self.wait_ocr(x=0.20, y=0.45, to_x=0.88, to_y=0.66, match=self.lang.daily_battle_mixin.k_4e1f3d8b, log=True)
        if not box_list:
            self.log_warning("未找到应急理智加强剂，剩余时效未识别")
        else:
            # 解析所有药品的时效，排序后依次消耗
            parsed_boxes = []
            for box in box_list:
                match = re.match(r"(\d+)(天|小时)", box.name)
                if not match:
                    self.log_warning(f"应急理智加强剂时效格式无法识别: {box.name}")
                    continue
                validity_num = int(match.group(1))
                validity_unit = match.group(2)
                # 小时优先，小时药排序在前
                sort_key = (0 if validity_unit == "小时" else 1, validity_num)
                parsed_boxes.append((sort_key, box, validity_num, validity_unit))
            if not parsed_boxes:
                self.log_warning("所有应急理智加强剂时效解析失败，无法消耗")
                self.safe_back(self.lang.daily_battle_mixin.k_a0d434d4, box=self.box.top_left, time_out=10, ocr_time_out=2)
                return True
            parsed_boxes.sort(key=lambda x: x[0])
            for _, box, validity_num, validity_unit in parsed_boxes:
                count_box_list = self.ocr(
                    x=box.x / self.width + 0.04,
                    y=box.y / self.height + 0.14,
                    to_x=box.x / self.width + 0.08,
                    to_y=box.y / self.height + 0.18,
                    match=re.compile(r"(\d+)"),
                )
                if not count_box_list:
                    self.log_info("数量未识别，按照1个处理")
                    count = 1
                else:
                    count = int(re.findall(r'(\d+)', count_box_list[0].name)[0])
                if validity_unit == "小时":
                    consume = count
                    self.log_info(f"找到 {count} 个限时 {validity_num} 小时的 应急理智加强剂，本次全部用掉")
                else:
                    consume = min(max(1, math.ceil(2 * count / validity_num)), count)
                    self.log_info(f"找到 {count} 个限时 {validity_num} 天的 应急理智加强剂，本次预计使用 {consume} 个")
                for _ in range(consume):
                    self.click(box, after_sleep=0.1)
                if not self.wait_click_ocr(match=self.lang.daily_battle_mixin.k_b56d9ac6, box=self.box.bottom_right, after_sleep=2):
                    self.log_error("无法使用 应急理智加强剂")
                else:
                    self.log_info(f"已使用 {consume} 个 应急理智加强剂")
                    self.wait_pop_up()
                # 只消耗一种类型后退出（如需全部消耗可去掉break）
                break
        # 统一出口，保证异常时也能返回主界面
        if not self.safe_back(self.lang.daily_battle_mixin.k_a0d434d4, box=self.box.top_left, time_out=10, ocr_time_out=2):
            return False
        return True

    def battle(self):
        self._reset_battle_state()
        try:
            # 自动根据日期和刷本序列决定刷哪个本
            self._resolve_stage_from_sequence()

            self.battle_ctx.today_reward_tier = (
                self.battle_ctx.stage_reward_tier_override
                if self.battle_ctx.ignore_config_reward_tier
                else self.config.get(self.CFG_STAGE_REWARD_TIER, self.REWARD_TIER_KEEP)
            )
            if self.battle_ctx.stage_name not in self.REWARD_TIER_STAGE_SET:
                self.battle_ctx.today_reward_tier = self.REWARD_TIER_KEEP
            self.log_info(
                f"今日最终刷本: {self._format_stage_with_reward_tier(self.battle_ctx.stage_name, self.battle_ctx.today_reward_tier)} "
                f"(奖励档位={self.battle_ctx.today_reward_tier})"
            )

            # F8 索引
            self._open_index()

            # 体力相关
            if not self._consume_stamina_potions():
                return False

            self.battle_ctx.left_ticket = self.detect_ticket_number()
            self.log_info(f"当前体力: {self.battle_ctx.left_ticket}")
            self.battle_ctx.category_name = get_stage_category(self.battle_ctx.stage_name)
            self.battle_ctx.no_battle = self.config.get("仅站桩", False)

            self.battle_ctx.extra_run_limit = max(0, int(self.config.get("体力刷完后继续刷取次数", 0) or 0))

            # 检查是否支持体力刷完后继续刷取
            if self.battle_ctx.extra_run_limit > 0:
                if not is_world_map_text(self.lang, self.battle_ctx.category_name, STAGE_CATEGORY_ENERGY_POOLING):
                    self.log_warning(
                        f"体力刷完后继续刷功能仅支持{STAGE_CATEGORY_ENERGY_POOLING}，当前副本为『{self.battle_ctx.category_name}』，已禁用此功能")
                    self.battle_ctx.extra_run_limit = 0
                else:
                    self.log_info(f"体力刷完后继续刷取次数: {self.battle_ctx.extra_run_limit}")

            if self.battle_ctx.left_ticket < self._battle_stage_cost:
                if self.battle_ctx.extra_run_limit <= 0:
                    self.log_info("体力不足")
                    return True
                else:
                    if not is_world_map_text(self.lang, self.battle_ctx.category_name, STAGE_CATEGORY_ENERGY_POOLING):
                        self.log_info(f"不支持无体力刷取的副本: {self.battle_ctx.category_name}")
                        return True
                    # 体力不足，执行额外刷取
                    self.log_info(f"体力不足，将执行 {self.battle_ctx.extra_run_limit} 次额外刷取（放弃领奖）")
                    if not self.to_stage():
                        return False
                    try:
                        return self.battle_gather()
                    except Exception as e:
                        if isinstance(e, TaskDisabledException):
                            raise
                        self.log_info(f"battle_gather_extra 异常: {e}\n{traceback.format_exc()}")
                        self.screenshot(
                            f'{datetime.now().strftime("%Y%m%d")}_DailyBattleMixin_battleGather_Extra_Exception_{self.battle_ctx.category_name}_{self.battle_ctx.stage_name}')
                        return False

            # 进入副本详情页
            if not self.to_stage():
                return False

            if is_world_map_text(self.lang, self.battle_ctx.category_name, STAGE_CATEGORY_ENERGY_POOLING):
                try:
                    return self.battle_gather()
                except Exception as e:
                    if isinstance(e, TaskDisabledException):
                        raise
                    self.log_info(f"battle_gather 异常: {e}\n{traceback.format_exc()}")
                    self.screenshot(
                        f'{datetime.now().strftime("%Y%m%d")}_DailyBattleMixin_battleGather_Exception_{self.battle_ctx.category_name}_{self.battle_ctx.stage_name}')
                    return False
            else:  # 协议空间 or 危境预演
                try:
                    return self.battle_space()
                except Exception as e:
                    if isinstance(e, TaskDisabledException):
                        raise
                    self.log_info(f"battle_space 异常: {e}\n{traceback.format_exc()}")
                    self.screenshot(
                        f'{datetime.now().strftime("%Y%m%d")}_DailyBattleMixin_battleSpace_Exception_{self.battle_ctx.category_name}_{self.battle_ctx.stage_name}')
                    return False
        finally:
            self.release_yolo_detector()

    def _init_gather_transfer_points(self):
        """设置传送点特征搜索区。"""
        self.gather_near_transfer_point_dict.update({
            "枢纽区": self.box.top,
            "源石研究园": self.box.top,
            "试验园区": self.box_of_screen(0.5, 0.25, 1, 0.75),
            "矿脉源区": self.box.right,
            "供能高地": self.box.bottom_right,
            "武陵城": self.box.top_left,
            "清波寨": self.box.top,
            "首墩": self.box.top,
        })

    def battle_gather(self):
        self.battle_ctx.enter_text = "挑战"
        self._init_gather_transfer_points()
        # 点击追踪按钮，进入地图并传送
        self._click_track_and_transfer()
        # 滑索移动
        self._navigate_via_zip_line()
        #
        self.navigate_until_target(target_ocr_pattern=self.lang.daily_battle_mixin.k_bfe73e18, nav_feature_name=fL.gather_icon_out_map,
                                   time_out=60)
        #
        if self.wait_ocr(match=self.lang.daily_battle_mixin.k_b8a81b7a, box=self.box.bottom_right, time_out=1):
            self.log_info("放弃未领取的奖励")
            self.wait_click_ocr(match=self.lang.daily_battle_mixin.k_b8a81b7a, box=self.box.bottom_right, time_out=5, recheck_time=1,
                                alt=True)
            self.click_confirm()
        if not self._switch_team_before_activate_for_gather():
            self.mark_task_failure("淤积点激发前换队失败")
            return False
        #
        result = self.wait_ocr(match=self.lang.daily_battle_mixin.k_cd9eabf7, box=self.box.bottom_right, time_out=5)
        if not result:
            self.log_info("没有找到『激发』按钮")
            return False
        self.sleep(1)
        if not self.wait_click_ocr(match=self.lang.daily_battle_mixin.k_cd9eabf7, box=self.box.bottom_right, time_out=5, recheck_time=1,
                                   alt=True):
            self.log_info("没有找到『激发』按钮")
            return False
        # 开战
        return self.battle_recycle()

    def battle_space(self):
        self.battle_ctx.enter_text = "进入"
        self.wait_click_ocr(match=self.lang.daily_battle_mixin.k_0e25578e, time_out=5, after_sleep=2, box=self.box.bottom_right, log=True)
        if self.wait_click_ocr(match=self.lang.daily_battle_mixin.k_4d0b4688, time_out=5, box=self.box.bottom_left, log=True):
            self.log_info("没有进入战斗，可能是因为已经没理智了")
            return True
        # 插入点：在首次『进入』之后、下一次『进入』之前执行换队。
        # 若上面已经点击到『取消』，则直接结束，不会触发该逻辑。
        self._switch_team_before_reenter()
        return self.battle_recycle()

    def _gather_retry_navigate(self):
        """
        能量淤积点的二次寻路：重新打开索引 → 进入副本详情 → 追踪传送 → 滑索 → 领取奖励。

        返回:
            bool: 成功找到并点击『领取奖励』按钮时返回 True，否则 False。
        """
        self.log_info("当前副本为『能量淤积点』，开始进行二次寻路。")
        # F8 索引
        self._open_index()
        # 进入副本详情页
        if not self.to_stage():
            self.mark_task_failure("二次寻路失败：无法进入『能量淤积点』详情页")
            return False
        # 点击追踪按钮，进入地图并传送
        self._click_track_and_transfer()
        # 滑索移动
        self._navigate_via_zip_line()
        #
        self.navigate_until_target(target_ocr_pattern=self.lang.daily_battle_mixin.k_39d12e73_1, nav_feature_name=fL.gather_icon_out_map,
                                   time_out=60)
        click_key = "放弃" if self.battle_ctx.is_extra_mode else "领取"
        result = self.wait_ocr(match=re.compile(click_key), box=self.box.bottom_right, time_out=5)
        if not result:
            self.mark_task_failure(f"二次寻路失败：没有找到『{click_key}』按钮")
            return False
        self.sleep(1)
        if not self.wait_click_ocr(match=re.compile(click_key), box=self.box.bottom_right, time_out=5, recheck_time=1,
                                   alt=True):
            self.mark_task_failure(f"二次寻路失败：没有找到『{click_key}奖励』按钮")
            return False
        # 如果是放弃领奖，那么还需要点击确认
        if self.battle_ctx.is_extra_mode:
            self.click_confirm()
            self.log_info("已放弃未领取的奖励")
        return True

    def to_restart(self):
        """
        开始下一轮刷取
        如果使用体力则点击重新挑战
        否则需要点击 激发 -> 挑战 -> 确认
        """
        if self.battle_ctx.is_extra_mode:
            # 放弃领取奖励后需要重新点击激发按钮
            result = self.wait_ocr(match=self.lang.daily_battle_mixin.k_cd9eabf7, box=self.box.bottom_right, time_out=5)
            if not result:
                self.log_info("未找到『激发』按钮，无法继续进行额外刷取")
                return False
            self.click_with_alt(result[0], after_sleep=2)
            self.wait_click_ocr(match=self.lang.daily_battle_mixin.k_9294c931, time_out=10, after_sleep=2, box=self.box.bottom_right_quarter,
                                log=True, recheck_time=1)
            self.click_confirm()
        else:
            self.wait_click_ocr(match=self.lang.daily_battle_mixin.k_55cfd979, box=self.box.bottom_left, log=True, time_out=5,
                                after_sleep=2, recheck_time=1)
        return True

    def battle_recycle(self):
        enter_bool = False
        self.battle_ctx.extra_run_count = 0
        self.battle_ctx.is_extra_mode = False

        while self.battle_ctx.left_ticket >= self._battle_stage_cost or self.battle_ctx.extra_run_count < self.battle_ctx.extra_run_limit:
            if enter_bool:
                # 开始下一轮刷取
                self.to_restart()
            else:
                # 根据 enter_str 决定点击位置, 如果是「挑战」, 并且刷取的是「能量淤积点」, 则点击右下四分之一区域, 避免误触
                enter_box = self.box.bottom_right_quarter if (
                        self.battle_ctx.enter_text == "挑战"
                        and is_world_map_text(self.lang, self.battle_ctx.category_name, STAGE_CATEGORY_ENERGY_POOLING)
                ) else self.box.bottom_right
                self.wait_click_ocr(match=re.compile(self.battle_ctx.enter_text), time_out=10, after_sleep=2,
                                    box=enter_box,
                                    log=True, recheck_time=1)
                # 如果无体力，点击放弃领奖后需要点击确认
                if self.battle_ctx.extra_run_limit > 0 and self.battle_ctx.left_ticket < self._battle_stage_cost:
                    self.click_confirm()
                    self.log_info("无体力，开始额外刷取（放弃领奖）")
                    self.battle_ctx.is_extra_mode = True
                enter_bool = True
            if not self.to_battle():
                return False
            # 移至奖励发放点，按下 F
            if not self.to_end():
                self.log_info("未发现奖励领取点")
                return False

            if self.battle_ctx.is_extra_mode:
                # 额外刷取则不领取奖励
                self.battle_ctx.extra_run_count += 1
            else:
                # 在『有可领取的奖励』页面上领取奖励
                self.battle_ctx.left_ticket = self.get_claim()
                #
                self.sleep(2)
                if self.battle_ctx.left_ticket <= 0:
                    self.wait_click_ocr(match=self.lang.daily_battle_mixin.k_0ba18905, box=self.box.bottom_right, log=True, recheck_time=1)

                    # 如果体力耗尽但设置了额外刷取次数，则开始额外刷取
                    self.battle_ctx.is_extra_mode = self.battle_ctx.extra_run_limit > 0 and self.battle_ctx.extra_run_count < self.battle_ctx.extra_run_limit
        return True

    def to_stage(self):
        """
        通用关卡进入方法：
        1. 点击左侧类别。
        2. 定位关卡位置。
        3. 点击对应按钮（“前往”或“查看”）。
        4. 自动支持普通关卡和高阶关卡（危境预演）。
        """
        # 点击左侧关卡类别
        self.wait_click_ocr(
            match=get_world_map_matcher(self.lang, self.battle_ctx.category_name),
            box=self.box.left,
            log=True,
            after_sleep=2,
            time_out=6
        )

        # 默认按钮文本
        to_text = "前往"
        if is_world_map_text(self.lang, self.battle_ctx.category_name, STAGE_CATEGORY_ENERGY_POOLING):
            to_text = "查看"
        # 判断是否是高阶关卡
        is_higher_order = is_world_map_text(self.lang, self.battle_ctx.category_name, STAGE_CATEGORY_DANGER_REHEARSAL)
        for _ in range(5):
            if is_higher_order:
                # 高阶关卡，使用 feature_dict 查找位置
                location = self.find_feature(feature_name=higher_order_feature_dict[self.battle_ctx.stage_name])
            else:
                # 普通关卡
                location = self.wait_ocr(match=re.compile(
                    self.battle_ctx.stage_name if self.battle_ctx.stage_name != '源石研究园' else '源石研究'),
                    box=self.box.left, log=True, time_out=5)
                # 「重度能量淤积点·源石研究园」会被居中指针挡住 “园”

            if location:
                enter_bool = self.wait_click_ocr(
                    match=re.compile(to_text),
                    box=self.box_of_screen(location[0].x / self.width, location[0].y / self.height, 1, 1),
                    after_sleep=2,
                    time_out=6,
                )
                if enter_bool:
                    return self._switch_stage_reward_tier()
            self.scroll_relative(650 / 1920, 0.5, count=-2)
            self.wait_ui_stable(refresh_interval=0.5)
        return False

    def _get_current_stage_reward_tier(self):
        if self.wait_ocr(match=self.lang.daily_battle_mixin.k_25e74dce, box=self.box.top, time_out=1, log=True):
            return self.REWARD_TIER_LOW
        if self.wait_ocr(match=self.lang.daily_battle_mixin.k_25e74dce, box=self.box.bottom, time_out=1, log=True):
            return self.REWARD_TIER_HIGH
        return None

    def _switch_stage_reward_tier(self):
        if self.battle_ctx.stage_name not in self.REWARD_TIER_STAGE_SET:
            if self.battle_ctx.stage_reward_tier_override:
                self.log_info(f"{self.battle_ctx.stage_name} 不支持奖励档位切换，已忽略序列后缀")
            return True

        if self.battle_ctx.stage_reward_tier_override in (self.REWARD_TIER_LOW, self.REWARD_TIER_HIGH):
            target_tier = self.battle_ctx.stage_reward_tier_override
        elif self.battle_ctx.ignore_config_reward_tier:
            # 启用刷本序列时，未写后缀则保持当前，不读取独立配置项
            return True
        else:
            target_tier = self.config.get(self.CFG_STAGE_REWARD_TIER, self.REWARD_TIER_KEEP)
        if target_tier == self.REWARD_TIER_KEEP:
            return True

        if not self.wait_click_ocr(match=self.lang.daily_battle_mixin.k_a6ee3a67, box=self.box.bottom_right, time_out=6, after_sleep=1):
            self.log_info(f"{self.battle_ctx.stage_name} 未识别到『自选』，跳过奖励档位切换")
            return True
        self.wait_ui_stable(refresh_interval=0.5)
        current_tier = self._get_current_stage_reward_tier()
        if current_tier == target_tier:
            self.log_info(f"{self.battle_ctx.stage_name} 已是{target_tier}奖励")
        else:
            target_box = self.box.top if target_tier == self.REWARD_TIER_LOW else self.box.bottom
            candidates = self.wait_ocr(
                match=[self.lang.daily_battle_mixin.k_25e74dce, self.lang.daily_battle_mixin.k_70b20820],
                box=target_box,
                time_out=4,
                log=True,
            ) or []
            valid_candidates = [c for c in candidates if "奖励选择" not in c.name]
            if valid_candidates:
                self.click(valid_candidates[0], after_sleep=1)
            else:
                self.log_info(f"{self.battle_ctx.stage_name} 未识别到{target_tier}对应按钮，保持当前档位")

        if not self.safe_back(match=self.lang.daily_battle_mixin.k_0e25578e, box=self.box.bottom_right, time_out=10, ocr_time_out=1):
            self.log_info("切换奖励档位后未返回到『进入』界面")
            return False
        return True

    def to_battle(self):
        if not is_world_map_text(self.lang, self.battle_ctx.category_name, STAGE_CATEGORY_ENERGY_POOLING):
            self.wait_pop_up(time_out=4)
            end_time = time.time()
            while not self.wait_ocr(match=self.lang.daily_battle_mixin.k_6afbae72, time_out=1, box=self.box.top_left, log=True):
                if time.time() - end_time > 300:
                    self.log_info("等待超时，进入协议空间超时")
                    return False
            self.move_keys("w", duration=0.25)
            while not self.wait_ocr(match=self.lang.daily_battle_mixin.k_4cc61900, time_out=1, box=self.box.bottom_right, log=True):
                self.move_keys('w', duration=0.25)
            self.press_key("f")
        else:
            self.wait_pop_up(time_out=4)
            end_time = time.time()
            while not self.wait_ocr(match=self.lang.daily_battle_mixin.k_9294c931, time_out=1, box=self.box.top_left, log=True):
                if time.time() - end_time > 30:
                    self.log_info("等待超时，进入挑战超时")
                    return False
        return self.auto_battle(no_battle=self.battle_ctx.no_battle)

    def to_end(self):
        if is_world_map_text(self.lang, self.battle_ctx.category_name, STAGE_CATEGORY_ENERGY_POOLING):
            end_feature_name = [fL.gather_icon_out_map2, fL.gather_icon_out_map]
            use_yolo = False
            search_box = None
            need_follow = True
            for i in range(9):
                for end_feature in end_feature_name:
                    if self.find_feature(
                            end_feature,
                            box=self.box_of_screen((1920 - 1550) / 1920, 150 / 1080, 1550 / 1920, (1080 - 150) / 1080),
                    ):
                        need_follow = False
                        break
                if not need_follow:
                    break
                self.click(key="middle")
                self.move_keys("aw", duration=0.1)
            # F8 索引
            if need_follow:
                self._open_index()
                # 进入副本详情页
                if not self.to_stage():
                    self.mark_task_failure("二次寻路失败：无法进入『能量淤积点』详情页")
                    return False
                if result := self.wait_feature(feature=fL.start_follow, box=self.box.bottom_right, time_out=5, raise_if_not_found=False):
                    self.click(result, after_sleep=1)
                    self.ensure_main()
                else:
                    raise Exception("未找到追踪按钮")
            self.click(key="middle", after_sleep=2)
        else:
            end_feature_name = "battle_end"
            use_yolo = True
            search_box = self.box_of_screen((1920 - 1550) / 1920, 0, 1550 / 1920, (1080 - 150) / 1080)
            for _ in range(9):
                if self.yolo_detect(end_feature_name, box=search_box):
                    break
                self.click(key="middle", after_sleep=2)
                self.move_keys("aw", duration=0.1)
                self.sleep(1)
        start_time = time.time()
        try:
            while self.align_ocr_or_find_target_to_center(end_feature_name, ocr=False, use_yolo=use_yolo,
                                                          box=search_box,
                                                          only_x=True, threshold=0.5, tolerance=100):
                if time.time() - start_time > 60:
                    if is_world_map_text(self.lang, self.battle_ctx.category_name, STAGE_CATEGORY_ENERGY_POOLING):
                        raise TimeoutError("等待奖励发放点超时")
                    else:
                        return False

                click_key = "放弃" if self.battle_ctx.is_extra_mode else "领取"
                if result := self.wait_ocr(match=re.compile(click_key), time_out=1, box=self.box.bottom_right):
                    self.sleep(0.5)
                    self.click_with_alt(result[0])
                    # 如果是放弃领奖，那么点击后还需要点击确认
                    if self.battle_ctx.is_extra_mode:
                        self.click_confirm()
                        self.log_info("已放弃未领取的奖励")
                    break
                else:
                    self.move_keys('w', duration=0.25)
        except Exception as e:
            if isinstance(e, TaskDisabledException):
                raise
            if is_world_map_text(self.lang, self.battle_ctx.category_name, STAGE_CATEGORY_ENERGY_POOLING):
                self.log_info(f"未找到奖励发放点，尝试二次寻路: {e}")
                if self._gather_retry_navigate():
                    return True
                else:
                    self.mark_task_failure("二次寻路失败，无法找到奖励发放点")
                    return False
            else:
                raise e
        return True

    def get_claim(self):
        """
        执行一次领奖操作，并返回剩余理智。

        逻辑：
        1. 等待界面稳定，并找到“可领取”提示。
        2. 尝试点击“获得奖励”，如果失败则本轮任务失败。
        3. 扣除本轮理智，判断剩余理智是否足够。
        4. 点击“领取”，记录领取状态。

        返回：
            int: 扣掉本轮消耗理智后的剩余理智，如果理智不足则返回 0。
        """
        self.log_info(
            "领取奖励,当前理智: {}, 本轮消耗理智: {}".format(self.battle_ctx.left_ticket, self._battle_stage_cost))
        self.wait_ui_stable(refresh_interval=1)
        start_time = time.time()

        # 等待界面出现“可领取”
        while not self.wait_ocr(match=self.lang.daily_battle_mixin.k_45ff3e5f, box=self.box.top, time_out=1):
            if time.time() - start_time > 60:
                return 0
            self.press_key("f", down_time=0.2)
            self.wait_ui_stable(refresh_interval=1)

        # 本轮默认消耗理智
        need_ticket_number = self._battle_stage_cost

        # 尝试点击“获得奖励”，失败则本轮减少消耗理智
        if not self.wait_click_ocr(
                match=self.lang.daily_battle_mixin.k_60064e16,
                box=self.box_of_screen(530 / 1920, 330 / 1080, 1400 / 1920, 570 / 1080),
                time_out=2,
                after_sleep=1,
                log=True
        ):
            self.mark_task_failure("未找到 '获得奖励' 按钮, 任务失败")
            return 0

        # 扣除本轮消耗理智
        sum_ticket_number = self.battle_ctx.left_ticket - need_ticket_number
        self.log_info("扣除本轮消耗理智: {}, 剩余理智: {}".format(need_ticket_number, sum_ticket_number))
        if sum_ticket_number < 0:
            return 0  # 理智不足，不能继续

        # 点击“领取”，失败则返回0
        self.next_frame()
        if not self.wait_click_ocr(match=self.lang.daily_battle_mixin.k_39d12e73, box=self.box.bottom_right, time_out=2, log=True):
            self.mark_task_failure("领取失败")
            return 0
        # 预测下一轮是否还能继续
        next_sum = sum_ticket_number - need_ticket_number
        self.log_info("预测下一轮消耗理智: {}, 预测下一轮剩余理智: {}".format(need_ticket_number, next_sum))

        if next_sum < 0:
            self.log_info("下一轮理智不足，无法继续")
            return 0
        else:
            # 返回本轮剩余理智，不返回next_sum，因为减耗只用于判断下一轮可否继续
            return sum_ticket_number
