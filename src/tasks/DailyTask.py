from functools import partial
import os
import tempfile
import webbrowser
from pathlib import Path

from qfluentwidgets import FluentIcon

from src.tasks.account.account_mixin import AccountMixin
from src.tasks.daily.daily_modules import (
    DailyBattleModule,
    DailyBuyModule,
    DailyLiaisonModule,
    DailyRoutineModule,
    DailyShopModule,
    DailyTradeModule,
)
from src.tasks.daily.finally_file import (
    DEFAULT_DAILY_FINALLY_CONTENT,
    create_daily_summary_report,
)
from src.tasks.daily.daily_task_runner import DailyTaskRunner
from src.tasks.mixin.end_command_mixin import EndCommandMixin

class DailyTask(
    AccountMixin,
    EndCommandMixin,
):
    """日常任务聚合执行器。"""
    LIAISON_HELP_LINK = "https://cnb.cool/ok-oldking/ok-ef-update/-/blob/main/docs/日常任务.md"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "日常任务"
        self.description = "子任务开关用⭐标出，自上而下顺序执行，最后执行『日常奖励』。\n如果出现反复按ESC的情形，请调高『设置/主界面单次动作后延迟』（建议1.5以上）。"
        self.icon = FluentIcon.SYNC
        self.support_schedule_task = True
        self.support_multi_account = True
        self.daily_runner: DailyTaskRunner | None = None
        self._bootstrap_daily_modules()
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
            "发生异常时终止游戏": False,
            "仅退出游戏": False,
        })
        self.add_exit_after_config()
        if self.debug:
            self.default_config.update({"重复测试的次数": 1})

    def _bootstrap_daily_modules(self):
        # 仅用于让模块把默认配置/描述注册到共享 context；实例本身不常驻。
        for module_cls in (
            DailyLiaisonModule,
            DailyRoutineModule,
            DailyShopModule,
            DailyTradeModule,
            DailyBattleModule,
            DailyBuyModule,
        ):
            module_cls(self)

    def _run_daily_module(self, module_cls, method_name: str):
        module = module_cls(self)
        try:
            return getattr(module, method_name)()
        finally:
            del module

    def _build_module_task(self, key: str, module_cls, method_name: str):
        return key, partial(self._run_daily_module, module_cls, method_name)

    def build_task_plan(self):
        return [
            self._build_module_task("⭐送礼", DailyLiaisonModule, "execute_gift_task"),
            self._build_module_task("⭐帝江号一键存放", DailyLiaisonModule, "boat_one_key_store"),
            self._build_module_task("⭐收邮件", DailyRoutineModule, "claim_mail"),
            self._build_module_task("⭐据点兑换", DailyRoutineModule, "exchange_outpost_goods"),
            self._build_module_task("⭐转交运送委托", DailyRoutineModule, "delivery_send_others"),
            self._build_module_task("⭐转交委托奖励领取", DailyRoutineModule, "claim_delivery_rewards"),
            self._build_module_task("⭐造装备", DailyRoutineModule, "make_weapon"),
            self._build_module_task("⭐简易制作", DailyRoutineModule, "make_simply"),
            self._build_module_task("⭐收信用", DailyRoutineModule, "collect_credit"),
            self._build_module_task("⭐帝江号收菜", DailyRoutineModule, "boat_claim_rewards"),
            self._build_module_task("⭐买信用商店", DailyShopModule, "credit_shop"),
            self._build_module_task("⭐买卖货", DailyTradeModule, "buy_sell"),
            self._build_module_task("⭐刷体力", DailyBattleModule, "battle"),
            self._build_module_task("⭐买物资", DailyBuyModule, "buy_staple_goods"),
            self._build_module_task("⭐活动奖励", DailyRoutineModule, "claim_activity_rewards"),
            self._build_module_task("⭐日常奖励", DailyRoutineModule, "claim_daily_rewards"),
            ("⭐传送到帝江号右侧传送点", lambda: self.transfer_to_home_point(box=self.box.right)),
            ("⭐执行结尾外部命令", self.launch_end_command_non_blocking),
        ]

    def open_help_link(self, *_):
        webbrowser.open(self.LIAISON_HELP_LINK)

    def open_surprise(self, *_):
        try:
            tf = tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="w", encoding="utf-8")
            tf.write(DEFAULT_DAILY_FINALLY_CONTENT)
            tf.flush()
            tf.close()
            path = tf.name
            try:
                if os.name == "nt":
                    os.startfile(path)
                else:
                    webbrowser.open(f"file://{path}")
            except Exception:
                webbrowser.open(f"file://{path}")
            self.log_info(f"已创建并打开临时惊喜文件: {path}")
            self.info_set("最后惊喜文件", str(path))
        except Exception as e:
            self.log_info(f"创建或打开临时惊喜文件失败: {e}")

    def run(self):
        """日常任务主入口。"""
        repeat_times = self.config.get("重复测试的次数", 1) if self.debug else 1
        try:
            self.daily_runner = DailyTaskRunner(self, self.build_task_plan())
            self.daily_runner.run(repeat_times=repeat_times)
        finally:
            self.run_daily_finally()

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
            try:
                if os.name == "nt":
                    os.startfile(summary_path)
                else:
                    webbrowser.open(f"file://{summary_path}")
            except Exception:
                webbrowser.open(f"file://{summary_path}")

            self.log_info(f"日常执行情况汇总已创建并打开: {summary_path}")
            
            return True
        except Exception as e:
            self.log_info(f"创建日常任务结尾文件失败: {e}", notify=True)
            return False
