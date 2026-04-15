import math
import re
import time
from datetime import datetime

from src.data.FeatureList import FeatureList as fL
from src.data.world_map import stages_cost, higher_order_feature_dict
from src.data.world_map import stages_dict, stages_list
from src.data.world_map_utils import get_stage_category
from src.tasks.sequence_parser import parse_int_sequence, parse_sequence
from src.tasks.mixin.battle_mixin import BattleMixin
from src.tasks.mixin.common import Common
from src.tasks.mixin.map_mixin import MapMixin
from src.tasks.mixin.zip_line_mixin import ZipLineMixin

gather_list = stages_dict["能量淤积点"]


class DailyBattleMixin(MapMixin, ZipLineMixin, BattleMixin, Common):
    CFG_SCROLL_ENABLE = "是否启用滚动放大视角"
    CFG_STAGE_REWARD_TIER = "体力本奖励档位"
    REWARD_TIER_KEEP = "保持当前"
    REWARD_TIER_LOW = "低阶"
    REWARD_TIER_HIGH = "高阶"
    REWARD_TIER_STAGE_SET = {"干员经验", "干员进阶", "技能提升", "武器进阶"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.gather_near_transfer_point_dict = dict()
        self.stages_list = stages_list
        # 下列代码在 AutoCombatTask.py 中有部分重复。如有更新，请两边一起修改。
        # 不要试图归并，否则会影响『日常任务』中的选项顺序。
        today_str = datetime.now().strftime("%Y-%m-%d")
        self.default_config.update({
            "⭐刷体力": True,
            "消耗限时体力药": False,
            "体力本": "干员经验",
            self.CFG_STAGE_REWARD_TIER: self.REWARD_TIER_KEEP,
            "刷体力开始日期": today_str,  # 默认当天，可自定义
            "刷本序列": "",  # 为空表示不启用自动轮换
            "仅站桩": False,
            self.CFG_SCROLL_ENABLE: False,
            **{key: "" for key in gather_list},
            "技能释放": "123",
            "启动技能点数": 2,
            "后台结束战斗通知": True,
            "无数字操作间隔": 6,
            "进入战斗后的初始等待时间": 3,
            "启用排轴": False,
            "排轴序列": "ult_2,1,e,ult_3,sleep_8",
        })
        self.config_description.update({
            "⭐刷体力": (
                "是否消耗所有「理智」刷取培养材料。"
            ),
            "消耗限时体力药": (
                "如果勾选，那么对于随机某项 m 个限时 n 天的体力药，\n"
                "使用其中的 2*m/n 个（向上取整）。"
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
                f"多个副本名用逗号分隔，如：干员经验,干员进阶,钱币收集。\n"
                f"会根据开始日期自动轮换。\n"
                "支持在以下副本后追加『低阶』或『高阶』：\n"
                "干员经验/干员进阶/技能提升/武器进阶。\n"
                "示例：干员经验低阶,技能提升高阶。\n"
                "必须为以下之一：\n"
                + '\n'.join([', '.join(self.stages_list[i:i+4]) for i in range(0, len(self.stages_list), 4)]) + "。\n"
                f"留空表示不启用自动轮换。"
            ),
            self.CFG_SCROLL_ENABLE: (
                "启用后在对齐滑索时会自动滚动放大视角\n"
                "可能会提高对齐成功率，但也可能导致对齐成功率下降较为明显\n"
                "建议启用此项时不要使用非白发或有白帽角色"
            ),
            **{key: (
                "需要设好「预刻写属性」。默认留空表示不使用滑索前往，\n"
                "更多用法参见 ./docs/体力本.md > 能量淤积点 。"
            ) for key in gather_list},
        })
        self.config_type["体力本"] = {"type": "drop_down", "options": self.stages_list}
        self.config_type[self.CFG_STAGE_REWARD_TIER] = {
            "type": "drop_down",
            "options": [self.REWARD_TIER_KEEP, self.REWARD_TIER_LOW, self.REWARD_TIER_HIGH]
        }

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

    def battle(self):
        # 重复的代码用⚠️标注，如有更新，请两边一起修改。
        #
        # 自动根据日期和刷本序列决定刷哪个本
        stage_name = self.config.get("体力本")
        stage_reward_tier_override = None
        ignore_config_reward_tier = False
        seq = self.config.get("刷本序列", "")
        self.log_info(f"检测到刷本序列配置: {seq if seq else '(空)'}")
        start_date = self.config.get("刷体力开始日期", "2026-04-05")
        auto_stage = None
        explain = ""
        try:
            if seq:
                seq_list = parse_sequence(seq)
                self.log_info(f"刷本序列解析结果: {seq_list}")
                seq_with_tier_list = []
                for raw_stage in seq_list:
                    parsed_stage_name, parsed_tier = self._split_stage_name_and_reward_tier(raw_stage)
                    seq_with_tier_list.append(self._format_stage_with_reward_tier(parsed_stage_name, parsed_tier))
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
                        return
                    if start_date and today < start:
                        explain = f"刷体力自动选择失败：开始日期 {start_date} 在未来（今天 {today}），已使用原配置体力本"
                    elif not seq_list:
                        explain = f"刷体力自动选择失败：刷本序列为空，已使用原配置体力本"
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
                            f"刷体力自动选择: 今天是第{days+1}天，"
                            f"命中序列项：{raw_auto_stage}，"
                            f"今日副本：{auto_stage}{tier_hint}。"
                        )
        except Exception as e:
            import traceback
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

        today_reward_tier = (
            stage_reward_tier_override
            if ignore_config_reward_tier
            else self.config.get(self.CFG_STAGE_REWARD_TIER, self.REWARD_TIER_KEEP)
        )
        if stage_name not in self.REWARD_TIER_STAGE_SET:
            today_reward_tier = self.REWARD_TIER_KEEP
        self.log_info(
            f"今日最终刷本: {self._format_stage_with_reward_tier(stage_name, today_reward_tier)} "
            f"(奖励档位={today_reward_tier})"
        )
        # F8 索引 ⚠️
        self.ensure_main()
        self.press_key("f8")
        self.wait_click_ocr(match=re.compile("索引"), time_out=7, after_sleep=2, box=self.box.top, log=True)
        # 体力相关
        if self.config.get("消耗限时体力药", False):
            self.click(3530/3840, 80/2160, after_sleep=2)  # 右上角加号
            box_list = self.ocr(x=0.28, y=0.45, to_x=0.88, to_y=0.66, match=re.compile(r"(\d+)天"))
            if len(box_list) <= 0:
                self.log_error("未找到应急理智加强剂，剩余天数未识别")
            else:
                box = box_list[0]
                validity = int(re.findall(r'(\d+)', box.name)[0])
                box_list=self.ocr(x=box.x/self.width+0.04, y=box.y/self.height+0.14, to_x=box.x/self.width+0.08, to_y=box.y/self.height+0.18, match=re.compile(r"(\d+)"))
                if len(box_list) <= 0:
                    self.log_info("数量未识别，按照1个处理")
                    count = 1
                else:
                    count = int(re.findall(r'(\d+)', box_list[0].name)[0])
                consume = min(max(1, math.ceil(2 * count / validity)), count)
                self.log_error(f"找到 {count} 个限时 {validity} 天的 应急理智加强剂，本次预计使用 {consume} 个")
                for i in range(consume):
                    self.click(box)
                if not self.wait_click_ocr(match=re.compile("确认"), box=self.box.bottom_right, after_sleep=2):
                    self.log_error("无法使用 应急理智加强剂")
                else:
                    self.log_error(f"已使用 {consume} 个 应急理智加强剂")
                    self.wait_pop_up()
            if not self.safe_back(re.compile("干员"), box=self.box.top_left, time_out=10, ocr_time_out=2):
                return False
        left_ticket = self.detect_ticket_number()
        self.log_info(f"当前体力: {left_ticket}")
        category_name = get_stage_category(stage_name)
        if left_ticket < stages_cost[category_name]:
            self.log_info("体力不足")
            return True
        # 进入副本详情页 ⚠️
        if not self.to_stage(
            stage_name,
            category_name,
            reward_tier_override=stage_reward_tier_override,
            ignore_config_tier=ignore_config_reward_tier,
        ):
            return False
        # 
        if category_name == "能量淤积点":
            try:
                return self.battle_gather(left_ticket, stage_name, category_name, no_battle=self.config.get("仅站桩", False))
            except Exception as e:
                # 能量淤积点情况复杂，出现异常的概率比较大，单独截图以便分析。
                self.screenshot(f'{datetime.now().strftime("%Y%m%d")}_DailyBattleMixin_battleGather_Exception')
                return False
        else: # 协议空间 or 危境预演
            return self.battle_space(left_ticket, stage_name, category_name)

    def battle_gather(self, left_ticket, stage_name, category_name, no_battle=False):
        # 重复的代码用⚠️标注，如有更新，请两边一起修改。
        #
        # 设置传送点特征搜索区
        self.gather_near_transfer_point_dict["枢纽区"] = self.box.top
        self.gather_near_transfer_point_dict["源石研究园"] = self.box.top
        self.gather_near_transfer_point_dict["矿脉源区"] = self.box.right
        self.gather_near_transfer_point_dict["供能高地"] = self.box.bottom_right
        self.gather_near_transfer_point_dict["武陵城"] = self.box.top
        self.gather_near_transfer_point_dict["清波寨"] = self.box.top
        # 点击『追踪』按钮，进入地图并传送 ⚠️
        if result := self.wait_ocr(match=re.compile("追踪"), box=self.box.bottom_right, time_out=5):
            if "追踪" in result[0].name and "取" not in result[0].name and "消" not in result[0].name:
                self.log_info("点击追踪按钮")
                self.click(result, after_sleep=2)
        self.to_near_transfer_point(self.gather_near_transfer_point_dict[stage_name])
        self.ensure_main()
        # 滑索移动 ⚠️
        zip_line_str = self.config.get(stage_name)
        if zip_line_str:
            self.press_key("f", after_sleep=2)
            zip_line_list = parse_int_sequence(zip_line_str)
            self.zip_line_list_go(
                zip_line_list,
                need_scroll=self.config.get(self.CFG_SCROLL_ENABLE),
            )
        #
        self.navigate_until_target(target_ocr_pattern=re.compile("激发|放弃"), nav_feature_name=fL.gather_icon_out_map, time_out=60)
        #
        if self.wait_ocr(match=re.compile("放弃"), box=self.box.bottom_right, time_out=5):
            self.log_info("放弃未领取的奖励")
            self.wait_click_ocr(match=re.compile("放弃"), box=self.box.bottom_right, time_out=5, recheck_time=1, alt=True)
            self.wait_click_ocr(match=re.compile("确认"), box=self.box.bottom_right, time_out=5)
        #
        result = self.wait_ocr(match=re.compile("激发"), box=self.box.bottom_right, time_out=5)
        if not result:
            self.log_info("没有找到『激发』按钮")
            return False
        else:
            self.sleep(1)
        if not self.wait_click_ocr(match=re.compile("激发"), box=self.box.bottom_right, time_out=5, recheck_time=1, alt=True):
            self.log_info("没有找到『激发』按钮")
            return False
        # 开战
        return self.battle_recycle(left_ticket, stage_name, category_name, "挑战", no_battle=no_battle, challenge_check=True)

    def battle_space(self, left_ticket, stage_name, category_name):
        self.wait_click_ocr(match=re.compile("进入"), time_out=5, after_sleep=2, box=self.box.bottom_right, log=True)
        if self.wait_click_ocr(match=re.compile("取消"), time_out=5, box=self.box.bottom_left, log=True):
            self.log_info("没有进入战斗，可能是因为已经没理智了")
            return True
        return self.battle_recycle(left_ticket, stage_name, category_name, "进入")

    def battle_recycle(self, left_ticket, stage_name, category_name, enter_str, no_battle=False, challenge_check=False):
        # 重复的代码用⚠️标注，如有更新，请两边一起修改。
        enter_bool = False
        while left_ticket > 0:
            if enter_bool:
                self.wait_click_ocr(match=re.compile("重新挑战"), box=self.box.bottom_left, log=True, time_out=5,
                                    after_sleep=2, recheck_time=1)
            else:
                self.wait_click_ocr(match=re.compile(enter_str), time_out=10, after_sleep=2, box=self.box.bottom_right,
                                    log=True, recheck_time=1)
                enter_bool = True
            if not self.to_battle(no_battle=no_battle, challenge_check=challenge_check):
                return False
            # 移至奖励发放点，按下 F
            if not self.to_end(challenge=challenge_check):
                self.log_info("未发现奖励领取点")
                if category_name == "能量淤积点":
                    try:
                        self.log_info("当前副本为『能量淤积点』，开始进行二次寻路。")
                        # F8 索引 ⚠️
                        self.ensure_main()
                        self.press_key("f8")
                        self.wait_click_ocr(match=re.compile("索引"), time_out=7, after_sleep=2, box=self.box.top, log=True)
                        # 进入副本详情页 ⚠️
                        if not self.to_stage(
                            stage_name,
                            category_name,
                        ):
                            raise RuntimeError("无法进入『能量淤积点』详情页")
                        # 点击『追踪』按钮，进入地图并传送 ⚠️
                        if result := self.wait_ocr(match=re.compile("追踪"), box=self.box.bottom_right, time_out=5):
                            if "追踪" in result[0].name and "取" not in result[0].name and "消" not in result[0].name:
                                self.log_info("点击追踪按钮")
                                self.click(result, after_sleep=2)
                        self.to_near_transfer_point(self.gather_near_transfer_point_dict[stage_name])
                        self.ensure_main()
                        # 滑索移动 ⚠️
                        zip_line_str = self.config.get(stage_name)
                        if zip_line_str:
                            self.press_key("f", after_sleep=2)
                            zip_line_list = parse_int_sequence(zip_line_str)
                            self.zip_line_list_go(
                                zip_line_list,
                                need_scroll=self.config.get(self.CFG_SCROLL_ENABLE),
                            )
                        #
                        self.navigate_until_target(target_ocr_pattern=re.compile("领取"), nav_feature_name=fL.gather_icon_out_map, time_out=60)
                        result = self.wait_ocr(match=re.compile("领取"), box=self.box.bottom_right, time_out=5)
                        if not result:
                            raise RuntimeError("没有找到『领取奖励』按钮")
                        else:
                            self.sleep(1)
                        if not self.wait_click_ocr(match=re.compile("领取"), box=self.box.bottom_right, time_out=5, recheck_time=1, alt=True):
                            raise RuntimeError("没有找到『领取奖励』按钮")
                    except RuntimeError as e:
                        self.log_info(f"二次寻路失败：{e.msg}")
                        return False
                else:
                    return False
            # 在『有可领取的奖励』页面上领取奖励
            left_ticket = self.get_claim(stages_cost[category_name], left_ticket)
            #
            self.sleep(2)
            if left_ticket <= 0:
                self.wait_click_ocr(match=re.compile("离开"), box=self.box.bottom_right, log=True, recheck_time=1)
                break
        return True

    def to_stage(self, stage_name, category_name, reward_tier_override=None, ignore_config_tier=False):
        """
        通用关卡进入方法：
        1. 点击左侧类别。
        2. 定位关卡位置。
        3. 点击对应按钮（“前往”或“查看”）。
        4. 自动支持普通关卡和高阶关卡（危境预演）。
        """
        # 点击左侧关卡类别
        self.wait_click_ocr(
            match=re.compile(category_name),
            box=self.box.left,
            log=True,
            after_sleep=2,
            time_out=6
        )

        # 默认按钮文本
        to_text = "前往"
        if category_name == "能量淤积点":
            to_text = "查看"
        # 判断是否是高阶关卡
        is_higher_order = category_name == "危境预演"
        for _ in range(5):
            if is_higher_order:
                # 高阶关卡，使用 feature_dict 查找位置
                location = self.find_feature(feature_name=higher_order_feature_dict[stage_name])
            else:
                # 普通关卡
                location = self.wait_ocr(match=re.compile(stage_name if stage_name != '源石研究园' else '源石研究'),
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
                    return self._switch_stage_reward_tier(
                        stage_name,
                        reward_tier_override=reward_tier_override,
                        ignore_config_tier=ignore_config_tier,
                    )
            self.scroll_relative(650 / 1920, 0.5, count=-2)
            self.wait_ui_stable(refresh_interval=0.5)
        return False
        # 如果找到位置，则点击按钮

    def _get_current_stage_reward_tier(self):
        if self.wait_ocr(match=re.compile("当前"), box=self.box.top, time_out=1, log=True):
            return self.REWARD_TIER_LOW
        if self.wait_ocr(match=re.compile("当前"), box=self.box.bottom, time_out=1, log=True):
            return self.REWARD_TIER_HIGH
        return None

    def _switch_stage_reward_tier(self, stage_name, reward_tier_override=None, ignore_config_tier=False):
        if stage_name not in self.REWARD_TIER_STAGE_SET:
            if reward_tier_override:
                self.log_info(f"{stage_name} 不支持奖励档位切换，已忽略序列后缀")
            return True

        if reward_tier_override in (self.REWARD_TIER_LOW, self.REWARD_TIER_HIGH):
            target_tier = reward_tier_override
        elif ignore_config_tier:
            # 启用刷本序列时，未写后缀则保持当前，不读取独立配置项
            return True
        else:
            target_tier = self.config.get(self.CFG_STAGE_REWARD_TIER, self.REWARD_TIER_KEEP)
        if target_tier == self.REWARD_TIER_KEEP:
            return True

        if not self.wait_click_ocr(match=re.compile("自选"), box=self.box.bottom_right, time_out=6, after_sleep=1):
            self.log_info(f"{stage_name} 未识别到『自选』，跳过奖励档位切换")
            return True
        self.wait_ui_stable(refresh_interval=0.5)
        current_tier = self._get_current_stage_reward_tier()
        if current_tier == target_tier:
            self.log_info(f"{stage_name} 已是{target_tier}奖励")
        else:
            target_box = self.box.top if target_tier == self.REWARD_TIER_LOW else self.box.bottom
            candidates = self.wait_ocr(
                match=[re.compile("当前"), re.compile("选择")],
                box=target_box,
                time_out=4,
                log=True,
            ) or []
            valid_candidates = [c for c in candidates if "奖励选择" not in c.name]
            if valid_candidates:
                self.click(valid_candidates[0], after_sleep=1)
            else:
                self.log_info(f"{stage_name} 未识别到{target_tier}对应按钮，保持当前档位")

        if not self.safe_back(match=re.compile("进入"), box=self.box.bottom_right, time_out=10, ocr_time_out=1):
            self.log_info("切换奖励档位后未返回到『进入』界面")
            return False
        return True

    def to_battle(self, no_battle: bool = False, challenge_check=False):
        if not challenge_check:
            self.wait_pop_up(time_out=4)
            end_time = time.time()
            while not self.wait_ocr(match=re.compile("撤离"), time_out=1, box=self.box.top_left, log=True):
                if time.time() - end_time > 300:
                    self.log_info("等待超时，进入协议空间超时")
                    return False
            self.move_keys("w", duration=0.25)
            while not self.wait_ocr(match=re.compile("触碰"), time_out=1, box=self.box.bottom_right, log=True):
                self.move_keys('w', duration=0.25)
            self.press_key("f")
        else:
            self.wait_pop_up(time_out=4)
            end_time = time.time()
            while not self.wait_ocr(match=re.compile("挑战"), time_out=1, box=self.box.top_left, log=True):
                if time.time() - end_time > 30:
                    self.log_info("等待超时，进入挑战超时")
                    return False
        return self.auto_battle(no_battle=no_battle)

    def to_end(self, challenge=False):
        if challenge:
            end_feature_name = fL.gather_icon_out_map2
            use_yolo = False
            search_box = None
        else:
            end_feature_name = "battle_end"
            use_yolo = True
            search_box = self.box_of_screen((1920 - 1550) / 1920, 0, 1550 / 1920, (1080 - 150) / 1080)
        for _ in range(9):
            if challenge:
                if self.find_feature(
                        end_feature_name,
                        box=self.box_of_screen((1920 - 1550) / 1920, 150 / 1080, 1550 / 1920, (1080 - 150) / 1080),
                ):
                    break
            else:
                if self.yolo_detect(end_feature_name, box=search_box):
                    break
            self.click(key="middle", after_sleep=2)
            self.move_keys("aw", duration=0.1)
            self.sleep(1)
        #
        start_time = time.time()
        while time.time() - start_time < 60:
            if not self.align_ocr_or_find_target_to_center(end_feature_name, ocr=False, use_yolo=use_yolo, box=search_box, only_x=True, raise_if_fail=False, threshold=0.5, tolerance=100):
                return False
            if self.wait_ocr(match=re.compile("领取"), time_out=1, box=self.box.bottom_right):
                self.sleep(0.5)
                self.press_key("f", down_time=0.2)
                return True
            else:
                self.move_keys('w', duration=0.25)
        return False

    def get_claim(self, ticket_number, sum_ticket_number):
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
        self.log_info("领取奖励,当前理智: {}, 本轮消耗理智: {}".format(sum_ticket_number, ticket_number))
        self.wait_ui_stable(refresh_interval=1)
        start_time = time.time()

        # 等待界面出现“可领取”
        while not self.wait_ocr(match=re.compile("可领取"), box=self.box.top, time_out=1):
            if time.time() - start_time > 60:
                return 0
            self.press_key("f", down_time=0.2)
            self.wait_ui_stable(refresh_interval=1)

        # 本轮默认消耗理智
        need_ticket_number = ticket_number

        # 尝试点击“获得奖励”，失败则本轮减少消耗理智
        if not self.wait_click_ocr(
                match=re.compile("获得奖励"),
                box=self.box_of_screen(530 / 1920, 330 / 1080, 1400 / 1920, 570 / 1080),
                time_out=2,
                after_sleep=1,
                log=True
        ):
            self.log_info("未找到 '获得奖励' 按钮, 任务失败")
            return 0

        # 扣除本轮消耗理智
        sum_ticket_number -= need_ticket_number
        self.log_info("扣除本轮消耗理智: {}, 剩余理智: {}".format(need_ticket_number, sum_ticket_number))
        if sum_ticket_number < 0:
            return 0  # 理智不足，不能继续

        # 点击“领取”，失败则返回0
        self.next_frame()
        if not self.wait_click_ocr(match=re.compile("领取"), box=self.box.bottom_right, time_out=2, log=True):
            self.log_info("领取失败")
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
