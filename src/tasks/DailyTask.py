from qfluentwidgets import FluentIcon

from src.tasks.account.account_mixin import AccountMixin
from src.tasks.daily.daily_battle_mixin import DailyBattleMixin
from src.tasks.daily.daily_buy_mixin import DailyBuyMixin
from src.tasks.daily.daily_liaison_mixin import DailyLiaisonMixin
from src.tasks.daily.daily_routine_mixin import DailyRoutineMixin
from src.tasks.daily.daily_shop_mixin import DailyShopMixin
from src.tasks.daily.daily_trade_mixin import DailyTradeMixin
from src.tasks.daily.finally_file import (
    create_daily_summary_report,
)
import tempfile
import os
import webbrowser
from pathlib import Path
from src.tasks.daily.daily_task_runner import DailyTaskRunner
from src.tasks.mixin.end_command_mixin import EndCommandMixin


class DailyTask(
    DailyBuyMixin,  # 买物资
    DailyBattleMixin,  # 刷体力
    DailyTradeMixin,  # 买卖货
    DailyShopMixin,  # 买信用商店
    DailyRoutineMixin,  # 其它
    DailyLiaisonMixin,  # 送礼
    EndCommandMixin,
    AccountMixin
):
    """日常任务聚合执行器。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "日常任务"
        self.description = "子任务开关用⭐标出，自上而下顺序执行，最后执行『日常奖励』。\n如果出现反复按ESC的情形，请调高『设置/主界面单次动作后延迟』（建议1.5以上）。"
        self.icon = FluentIcon.SYNC
        self.support_schedule_task = True
        self.support_multi_account = True
        self.daily_runner: DailyTaskRunner | None = None
        self.config_description.update(
            {
                "仅退出游戏": "是否在完成所有任务后仅退出游戏，开启后会自动关闭游戏进程,但不关闭软件\n开启发生异常时终止游戏时此选项不生效",
                "发生异常时终止游戏": "勾选这个选项：如果「完成后退出」被选定，那么抛出异常也会退出游戏和App。",
            }
        )
        self.add_end_command_config(
            enable_description="是否在日常任务末尾执行一次外部命令行程序。",
            command_description=(
                "需要执行的命令行内容。\n"
                "建议：优先绝对路径；路径或参数含空格时按系统 shell 规则加引号。\n"
                "开启『结尾外部命令等待退出』可支持多账户模式。\n"
                "可选填写『结尾外部命令起始于』作为命令工作目录。"
            ),
        )
        self.default_config.update({
            "⭐传送到帝江号右侧传送点": True,
            "配置选择": "",
            "发生异常时终止游戏": False,
            "仅退出游戏": False,
        })
        task_group = {"隐藏": [], "子任务配置": [i for i, _ in self.build_task_plan()]}

        # 合并两个分组字典
        all_groups = {**task_group, **self.default_config_group, **{"其他配置": ["发生异常时终止游戏", "仅退出游戏"]}}

        self.register_config_groups(all_groups)
        self.add_exit_after_config()
        if self.debug:
            self.default_config.update({"重复测试的次数": 1})

    def build_task_plan(self):
        return [
            ("⭐送礼", self.execute_gift_task),
            ("⭐帝江号一键存放", self.boat_one_key_store),
            ("⭐收邮件", self.claim_mail),
            ("⭐据点兑换", self.exchange_outpost_goods),
            ("⭐转交运送委托", self.delivery_send_others),
            ("⭐转交委托奖励领取", self.claim_delivery_rewards),
            ("⭐造装备", self.make_weapon),
            ("⭐简易制作", self.make_simply),
            ("⭐收信用", self.collect_credit),
            ("⭐帝江号收菜", self.boat_claim_rewards),
            ("⭐买信用商店", self.credit_shop),
            ("⭐买卖货", self.buy_sell),
            ("⭐刷体力", self.battle),
            ("⭐买物资", self.buy_staple_goods),
            ("⭐活动奖励", self.claim_activity_rewards),
            ("⭐日常奖励", self.claim_daily_rewards),
            ("⭐传送到帝江号右侧传送点", lambda: self.transfer_to_home_point(box=self.box.right)),
            ("⭐执行结尾外部命令", self.launch_end_command_non_blocking),
        ]

    def run(self):
        """日常任务主入口。"""
        repeat_times = self.config.get("重复测试的次数", 1) if self.debug else 1
        try:
            self.daily_runner = DailyTaskRunner(self, self.build_task_plan())
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
            # 在任务完成或停止时自动生成一个临时的汇总文件并打开（不再依赖配置项）
            target_directory = Path(tempfile.gettempdir())

            # 仅在 runner 产生了有效汇总数据时才创建临时文件
            if not (self.daily_runner and self.daily_runner.has_summary_data()):
                # 若没有可用的汇总信息，则不创建也不打开临时文件
                self.log_info("无可用汇总信息，跳过生成临时汇总文件")
                return True

            summary_info = self.daily_runner.final_summary
            summary_path = create_daily_summary_report(target_directory, summary_info)

            # 立即用系统默认程序打开临时汇总文件
            self._open_local_path_with_default_app(summary_path)

            self.log_info(f"日常执行情况汇总已创建并打开: {summary_path}")

            return True
        except Exception as e:
            self.log_info(f"创建日常任务结尾文件失败: {e}", notify=True)
            return False
