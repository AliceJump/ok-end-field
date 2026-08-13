from qfluentwidgets import FluentIcon

from src.tasks.account.account_mixin import AccountMixin
from src.tasks.daily.daily_battle_mixin import DailyBattleFeature
from src.tasks.daily.daily_buy_mixin import DailyBuyFeature
from src.tasks.daily.daily_liaison_mixin import DailyLiaisonFeature
from src.tasks.daily.daily_routine_mixin import DailyRoutineFeature
from src.tasks.daily.daily_shop_mixin import DailyShopFeature
from src.tasks.daily.daily_trade_mixin import DailyTradeFeature
from src.tasks.daily.daily_demo_mixin import DailyDemoFeature
from src.tasks.daily.daily_regional_runner import DailyRegionalRunner
from src.tasks.daily.finally_file import (
    create_task_summary_report,
)
import tempfile
import os
import webbrowser
from pathlib import Path
from src.tasks.daily.daily_task_runner import DailyTaskRunner
from src.tasks.onetime.DeliveryTask import DeliveryFeature
from src.tasks.mixin.end_command_mixin import EndCommandMixin
from src.tasks.mixin.common import Common
from src.tasks.mixin.map_mixin import MapMixin
from src.tasks.mixin.zip_line_mixin import ZipLineMixin
from src.tasks.mixin.battle_mixin import BattleMixin
from src.tasks.mixin.liaison_mixin import LiaisonMixin
from src.tasks.mixin.mouse_scan_mixin import MouseScanMixin
from src.core.config_migration import legacy_bool_switch_to_list, merge_bool_options


class DailyTask(
    Common,
    MapMixin,
    ZipLineMixin,
    BattleMixin,
    LiaisonMixin,
    EndCommandMixin,
    AccountMixin,
    MouseScanMixin
):
    """日常任务聚合执行器。"""

    # 旧版日常配置键迁移（CodeRabbit 线程4/8）：
    # 纯键名复制（config_key_migrations）由 BaseEfTask.load_config 走 MRO 自动收集；
    # 值转换（config_value_migrations）处理旧布尔开关 → 多选列表，
    # 两类迁移均为类属性声明方式，逻辑集中在 src/core/config_migration.py。
    config_key_migrations = {
        "帝江号收菜操作": "⭐帝江号收菜",
        "活动奖励": "⭐活动奖励",
    }
    config_value_migrations = {
        # 旧版三个地区布尔开关 → 新的多选列表键。
        "⭐地区建设": merge_bool_options({
            "据点兑换": "⭐据点兑换",
            "买物资": "⭐买物资",
            "买卖货": "⭐买卖货",
        }),
        # 旧布尔开关 + 操作列表 → 新多选列表键。
        "⭐帝江号收菜": legacy_bool_switch_to_list(
            ops_key="帝江号收菜操作",
            defaults=DailyRoutineFeature.BOAT_STAGES,
        ),
        "⭐活动奖励": legacy_bool_switch_to_list(
            ops_key="活动奖励",
            defaults=DailyRoutineFeature.ACTIVITY_REWARDS,
        ),
    }

    BOAT_STATE_TASK_KEYS = frozenset({
        "⭐帝江号整理",
        "⭐帝江号收菜",
    })
    MULTI_SELECTION_TASK_KEYS = frozenset({
        "⭐地区建设",
        "⭐帝江号收菜",
        "⭐活动奖励",
    })

    account_config_blacklist = {
        "发生异常时终止游戏",
        "仅退出游戏",
        "自动打开汇总文件",
        "Exit After Task",
        "重复测试的次数",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "日常任务"
        self.icon = FluentIcon.CALENDAR
        self.group_name = "日常任务"
        self.group_icon = FluentIcon.CALENDAR
        self.description = "子任务开关用⭐标出，自上而下顺序执行，默认展开在最前面的『⭐⭐⭐ 默认』分组，最后执行『日常奖励』。\n如果出现反复按ESC的情形，请调高『设置/主界面单次动作后延迟』（建议1.5以上）。"

        self.support_schedule_task = True
        self.support_multi_account = True
        self.daily_runner: DailyTaskRunner | None = None

        # 组合各个功能模块
        self.daily_buy = DailyBuyFeature(self)
        self.daily_battle = DailyBattleFeature(self)
        self.daily_trade = DailyTradeFeature(self)
        self.daily_shop = DailyShopFeature(self)
        self.daily_routine = DailyRoutineFeature(self)
        self.daily_liaison = DailyLiaisonFeature(self)
        self.daily_demo = DailyDemoFeature(self)
        self.daily_regional = DailyRegionalRunner(self)
        self.delivery = DeliveryFeature(self)

        self.config_description.update(
            {
                "仅退出游戏": "是否在完成所有任务后仅退出游戏，开启后会自动关闭游戏进程,但不关闭软件\n开启发生异常时终止游戏时此选项不生效",
                "发生异常时终止游戏": "勾选这个选项：如果「完成后退出」被选定，那么抛出异常也会退出游戏和App。",
            }
        )
        self.add_end_command_config(
            enable_description="是否执行一次外部命令行程序（可在「外部命令执行时机」选择在最开始或最后执行）。",
            command_description=(
                "需要执行的命令行内容。\n"
                "建议：优先绝对路径；路径或参数含空格时按系统 shell 规则加引号。\n"
                "开启『外部命令等待退出』可支持多账户模式。\n"
                "可选填写『外部命令起始于』作为命令工作目录。"
            ),
        )
        self.default_config.update({
            "⭐地区建设": DailyRegionalRunner.DEFAULT_OPTIONS,
            "⭐传送到帝江号右侧传送点": True,
            "配置选择": "⭐⭐⭐ 默认",
            "发生异常时终止游戏": False,
            "仅退出游戏": False,
            "自动打开汇总文件": True,
        })
        self.config_description.update({
            "⭐地区建设": (
                "按地区执行所选操作：先据点兑换，再执行买卖货的买；启用买物资时，买完后切换到稳定物资需求购买，最后切回弹性需求物资执行卖。"
            ),
            "⭐传送到帝江号右侧传送点": "是否在日常任务结束后传送到帝江号右侧传送点。",
            "自动打开汇总文件": "任务完成后自动用系统默认程序打开汇总文件。关闭则仅创建文件不打开。",
        })
        self.config_type["⭐地区建设"] = {
            "type": "multi_selection",
            "options": DailyRegionalRunner.OPTIONS,
        }
        task_group = {
            "⭐⭐⭐ 默认": [
                item[0] for item in self.build_task_plan()
                if item[0] not in self.MULTI_SELECTION_TASK_KEYS
            ] + ["⭐帝江号一键存放", "⭐简易制作", "⭐地区建设", "⭐帝江号收菜", "⭐活动奖励", "⭐执行外部命令"],
        }

        # 合并两个分组字典
        all_groups = {**task_group, **self.default_config_group, **{"其他配置": ["发生异常时终止游戏", "仅退出游戏", "自动打开汇总文件"]}}

        self.register_config_groups(all_groups)
        self.add_exit_after_config()
        if self.debug:
            self.default_config.update({"重复测试的次数": 1})

    def build_task_plan(self):
        return [
            ("⭐送礼", self.daily_liaison.execute_gift_task),
            ("⭐帝江号整理", self.daily_routine.boat_organize,
             lambda: self.config.get("⭐帝江号一键存放", False) or self.config.get("⭐简易制作", False)),
            ("⭐帝江号收菜", self.daily_routine.boat_claim_rewards),
            ("⭐收邮件", self.daily_routine.claim_mail),
            ("⭐转交运送委托", self.daily_routine.delivery_send_others),
            ("⭐自动送货", self.delivery.run_daily),
            ("⭐地区建设", self.daily_regional.run),
            ("⭐造装备", self.daily_routine.make_weapon),
            ("⭐收信用", self.daily_routine.collect_credit),
            ("⭐买信用商店", self.daily_shop.credit_shop),
            ("⭐刷体力", self.daily_battle.battle),
            ("⭐活动奖励", self.daily_routine.claim_activity_rewards),
            ("⭐日常奖励", self.daily_routine.claim_daily_rewards),
            ("⭐演算", self.daily_demo.battle_demo),
            ("⭐传送到帝江号右侧传送点", lambda: self.transfer_to_home_point(box=self.box.right)),
        ]

    def run(self):
        """日常任务主入口。"""
        self.active_and_send_mouse_delta(only_activate=True)
        repeat_times = self.config.get("重复测试的次数", 1) if self.debug else 1
        try:
            task_plan = self.build_task_plan()
            # 根据配置决定外部命令的执行时机
            end_cmd_task=("⭐执行外部命令", self.launch_end_command_non_blocking)
            if self.config.get("外部命令执行时机", "任务最后") == "任务最开始":
                task_plan.insert(0, end_cmd_task)
            else:
                task_plan.append(end_cmd_task)
            self.daily_runner = DailyTaskRunner(
                self,
                task_plan,
                shared_state_task_keys=self.BOAT_STATE_TASK_KEYS,
            )
            self.daily_runner.run(repeat_times=repeat_times)
        finally:
            self.run_daily_finally()

    def _open_local_path_with_default_app(self, path: str | Path):
        normalized_path = Path(path).resolve()
        file_uri = normalized_path.as_uri()
        if os.name == "nt":
            try:
                os.startfile(str(normalized_path))
                return
            except OSError as error:
                self.log_debug(f"使用 os.startfile 打开路径失败，改用浏览器回退: {error}")
        webbrowser.open(file_uri)

    def run_daily_finally(self):
        try:
            # 在任务完成或停止时自动生成一个临时的汇总文件（不再依赖配置项）
            target_directory = Path(tempfile.gettempdir())

            # 仅在 runner 产生了有效汇总数据时才创建临时文件
            if not (self.daily_runner and self.daily_runner.has_summary_data()):
                # 若没有可用的汇总信息，则不创建也不打开临时文件
                self.log_info("无可用汇总信息，跳过生成临时汇总文件")
                return True

            summary_info = self.daily_runner.final_summary
            summary_path = create_task_summary_report(self, target_directory, summary_info)

            # 根据开关决定是否打开汇总文件
            if self.config.get("自动打开汇总文件", True):
                self._open_local_path_with_default_app(summary_path)
                self.log_info(f"日常执行情况汇总已创建并打开: {summary_path}")
            else:
                self.log_info(f"日常执行情况汇总已创建（未打开）: {summary_path}")

            return True
        except Exception as e:
            self.log_info(f"创建日常任务结尾文件失败: {e}", notify=True)
            return False
