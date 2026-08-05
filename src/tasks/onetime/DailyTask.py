from qfluentwidgets import FluentIcon
from src.data.FeatureList import FeatureList as fL

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
from src.tasks.mixin.end_command_mixin import EndCommandMixin
from src.tasks.mixin.common import Common
from src.tasks.mixin.map_mixin import MapMixin
from src.tasks.mixin.zip_line_mixin import ZipLineMixin
from src.tasks.mixin.battle_mixin import BattleMixin
from src.tasks.mixin.liaison_mixin import LiaisonMixin
from src.tasks.mixin.mouse_scan_mixin import MouseScanMixin


def migrate_legacy_daily_config(config, boat_stages=None, activity_rewards=None):
    """把旧版日常任务配置键迁移为新的多选列表键（CodeRabbit 线程4/8）。

    旧版结构（键名已从 default_config 移除）：
      - ⭐据点兑换 / ⭐买物资 / ⭐买卖货: bool → 合并为 ⭐地区建设 列表
      - ⭐帝江号收菜: bool 开关 + 帝江号收菜操作: list → ⭐帝江号收菜 列表
      - ⭐活动奖励: bool 开关 + 活动奖励: list → ⭐活动奖励 列表

    Args:
        config: 从 DailyTask.json 读取的配置字典（就地修改）。
        boat_stages: ⭐帝江号收菜 的可用选项列表（默认取 DailyRoutineFeature.BOAT_STAGES）。
        activity_rewards: ⭐活动奖励 的可用选项列表（默认取 DailyRoutineFeature.ACTIVITY_REWARDS）。

    Returns:
        (config, modified): 修改后的配置与是否有改动。
    """
    boat_stages = boat_stages or DailyRoutineFeature.BOAT_STAGES
    activity_rewards = activity_rewards or DailyRoutineFeature.ACTIVITY_REWARDS
    modified = False

    # 地区建设：三个旧布尔开关 → 新的多选列表。
    # 只有在新键缺失且存在任意旧键时才生成，避免覆盖用户的新配置。
    if "⭐地区建设" not in config:
        legacy_regional = {
            "据点兑换": config.get("⭐据点兑换"),
            "买物资": config.get("⭐买物资"),
            "买卖货": config.get("⭐买卖货"),
        }
        if any(v is not None for v in legacy_regional.values()):
            config["⭐地区建设"] = [
                name for name, enabled in legacy_regional.items() if enabled
            ]
            modified = True

    # 帝江号收菜：旧布尔开关 + 操作列表 → 新列表。
    # 开关为 True 但缺少操作列表时回退到全部默认选项。
    boat = config.get("⭐帝江号收菜")
    if isinstance(boat, bool):
        boat_ops = config.get("帝江号收菜操作")
        config["⭐帝江号收菜"] = (
            list(boat_ops)
            if boat and isinstance(boat_ops, list)
            else list(boat_stages) if boat else []
        )
        modified = True

    # 活动奖励：旧布尔开关 + 列表 → 新列表。
    reward = config.get("⭐活动奖励")
    if isinstance(reward, bool):
        reward_ops = config.get("活动奖励")
        config["⭐活动奖励"] = (
            list(reward_ops)
            if reward and isinstance(reward_ops, list)
            else list(activity_rewards) if reward else []
        )
        modified = True

    return config, modified


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

    # 旧版日常配置键迁移（CodeRabbit 线程4）：
    # 纯键名复制由 BaseEfTask.load_config 的 config_key_migrations 机制处理；
    # ⭐据点兑换 / ⭐买物资 / ⭐买卖货 三个布尔键合并为 ⭐地区建设 列表，
    # 以及 ⭐帝江号收菜 / ⭐活动奖励 的布尔开关 → 列表转换，
    # 由 migrate_legacy_daily_config 在 load_config 中处理。
    config_key_migrations = {
        "帝江号收菜操作": "⭐帝江号收菜",
        "活动奖励": "⭐活动奖励",
    }

    BOAT_STATE_TASK_KEYS = frozenset({
        "⭐帝江号一键存放",
        "⭐简易制作",
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

    def load_config(self):
        # 先迁移旧版键（含布尔 → 列表的值转换），再走基类的迁移表与配置加载。
        self._migrate_legacy_daily_config()
        super().load_config()

    def _migrate_legacy_daily_config(self):
        """迁移旧版日常配置键（线程4/8），在加载配置前改写 JSON。"""
        from ok.util.file import get_relative_path, read_json_file, write_json_file

        config_file = get_relative_path("configs", f"{self.__class__.__name__}.json")
        config = read_json_file(config_file)
        if not isinstance(config, dict):
            return
        config, modified = migrate_legacy_daily_config(
            config,
            boat_stages=DailyRoutineFeature.BOAT_STAGES,
            activity_rewards=DailyRoutineFeature.ACTIVITY_REWARDS,
        )
        if modified:
            write_json_file(config_file, config)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "日常任务"
        self.group_name = "日常任务"
        self.group_icon = FluentIcon.CALENDAR
        self.description = "子任务开关用⭐标出，自上而下顺序执行，默认展开在最前面的『⭐⭐⭐ 默认』分组，最后执行『日常奖励』。\n如果出现反复按ESC的情形，请调高『设置/主界面单次动作后延迟』（建议1.5以上）。"
        self.icon = FluentIcon.SYNC
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
                i for i, _ in self.build_task_plan()
                if i not in self.MULTI_SELECTION_TASK_KEYS
            ] + ["⭐地区建设", "⭐帝江号收菜", "⭐活动奖励", "⭐执行外部命令"],
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
            ("⭐帝江号一键存放", self.daily_liaison.boat_one_key_store),
            ("⭐简易制作", self.daily_routine.make_simply),
            ("⭐帝江号收菜", self.daily_routine.boat_claim_rewards),
            ("⭐收邮件", self.daily_routine.claim_mail),
            ("⭐转交运送委托", self.daily_routine.delivery_send_others),
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
