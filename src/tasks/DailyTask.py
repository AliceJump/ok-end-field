import os
import shlex
import subprocess
from datetime import datetime

from ok import TaskDisabledException
from qfluentwidgets import FluentIcon

from src.tasks.account.account_mixin import AccountMixin
from src.tasks.daily.daily_battle_mixin import DailyBattleMixin
from src.tasks.daily.daily_buy_mixin import DailyBuyMixin
from src.tasks.daily.daily_liaison_mixin import DailyLiaisonMixin
from src.tasks.daily.daily_routine_mixin import DailyRoutineMixin
from src.tasks.daily.daily_shop_mixin import DailyShopMixin
from src.tasks.daily.daily_trade_mixin import DailyTradeMixin

class DailyTask(
    DailyBuyMixin,  # 买物资
    DailyBattleMixin,  # 刷体力
    DailyTradeMixin,  # 买卖货
    DailyShopMixin,  # 买信用商店
    DailyRoutineMixin,  # 其它
    DailyLiaisonMixin,  # 送礼
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
        self.task_status = {"success": [], "failed": []}
        self.default_config.update({"⭐传送到帝江号右侧传送点": True, "发生异常时终止游戏": False, "仅退出游戏": False})
        self.default_config.update({"⭐执行结尾外部命令": False, "结尾外部命令": ""})
        self.config_description.update(
            {
                "仅退出游戏": "是否在完成所有任务后仅退出游戏，开启后会自动关闭游戏进程,但不关闭软件\n开启发生异常时终止游戏时此选项不生效",
                "发生异常时终止游戏": "勾选这个选项：如果「完成后退出」被选定，那么抛出异常也会退出游戏和App。",
                "⭐执行结尾外部命令": "是否在日常任务末尾执行一次外部命令行程序（非阻塞）。",
                "结尾外部命令": (
                    "需要执行的命令行内容。\n"
                    "示例1（Maaend,Windows）：mxu.exe --autostart --instance 日常任务\n"
                    "示例2：python scripts/daily_extra.py\n"
                    "说明：Maaend中 --instance/-i 指定实例名，--quit-after-run/-q 表示完成后退出。\n"
                    "建议：优先绝对路径；路径或参数含空格时按系统 shell 规则加引号。"
                    "暂不兼容多账号模式"
                ),
            }
        )
        self.current_task_key = None
        self.add_exit_after_config()
        if self.debug:
            self.default_config.update({"重复测试的次数": 1})

    def run(self):
        """日常任务主入口。"""
        try:
            # 在运行期覆盖帮助链接，避免在 __init__ 阶段 self.config 仍为 None。
            self.config["帮助"] = self.HELP_LINK
            self.log_info("开始执行日常任务...", notify=True)
            accounts_bool = self.config.get("多账户模式", False)
            if accounts_bool:
                accounts_list = self.get_account_list()
                repeat_times = len(accounts_list)
            else:
                repeat_times = self.config.get("重复测试的次数", 1) if self.debug else 1
            tasks = [
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
                ("⭐周常奖励", self.claim_weekly_rewards),
                ("⭐日常奖励", self.claim_daily_rewards),
                ("⭐传送到帝江号右侧传送点", lambda: self.transfer_to_home_point(box=self.box.right)),
                ("⭐执行结尾外部命令", self.launch_end_command_non_blocking),
            ]
            all_fail_tasks = []
            for repeat_idx in range(repeat_times):

                # ===== 多账号模式 =====
                if accounts_bool:
                    account = accounts_list[repeat_idx]
                    username = str(account.get("username", "")).strip()
                    password = str(account.get("password", ""))
                    account_id = str(account.get("account_id", "")).strip() or username
                    if not username:
                        self.log_info(f"第 {repeat_idx + 1}/{repeat_times} 个账号为空，已跳过")
                        continue

                    self.set_current_account(username, account_id)
                    self.log_info(f"开始第 {repeat_idx+1}/{repeat_times} 个账号({username[-4:]})任务执行")
                    self.login_flow(username, password)

                # ===== 调试模式 =====
                elif self.debug:
                    self.set_current_account("", "")
                    self.log_info(f"调试模式，第 {repeat_idx + 1}/{repeat_times} 轮")
                else:
                    self.set_current_account("", "")

                if not self._logged_in:
                    self.ensure_main(time_out=240)
                else:
                    self.ensure_main()
                # ✅ 每轮重置状态
                self.task_status = {"success": [], "failed": []}

                self.log_info(f"开始第 {repeat_idx + 1}/{repeat_times} 轮任务执行")

                for key, func in tasks:
                    self.execute_task(key, func)

                # ✅ 统计结果
                if self.task_status["failed"]:
                    all_fail_tasks.append((repeat_idx + 1, self.task_status["failed"]))
                    self.log_info(f"第 {repeat_idx + 1} 轮 | 失败任务: {self.task_status['failed']}", notify=True)
                else:
                    self.log_info(f"第 {repeat_idx + 1} 轮 | 日常完成!", notify=True)
                if hasattr(self, "task_status"):
                    if self.task_status.get("failed"):
                        self.info_set("已失败的任务列表", self.task_status["failed"])
                    if self.task_status.get("success"):
                        self.info_set("已完成的任务列表", self.task_status["success"])

            # ✅ 汇总输出
            if repeat_times > 1:
                if all_fail_tasks:
                    self.log_info(f"执行完成，失败统计: {all_fail_tasks}", notify=True)
                else:
                    self.log_info("所有任务均成功完成!", notify=True)

            if self.config.get("仅退出游戏", False):
                self.kill_game()
                raise Exception("任务完成，仅退出游戏, 终止其他过程")
        except Exception as e:
            self.screenshot(f'{datetime.now().strftime("%Y%m%d")}_DailyTask_Exception')

            if hasattr(self, "task_status"):
                if self.task_status.get("failed"):
                    self.info_set("已失败的任务列表", self.task_status["failed"])
                if self.task_status.get("success"):
                    self.info_set("已完成的任务列表", self.task_status["success"])

            if self.current_task_key:
                self.info_set("当前失败的任务", self.current_task_key)

            if not self.config.get("发生异常时终止游戏", False):
                self.log_info("发生异常，继续游戏", notify=True)
                raise e
            else:
                if isinstance(e, TaskDisabledException):
                    self.log_info("发生异常，继续游戏", notify=True)
                    raise e
                else:
                    self.log_info("发生异常，终止游戏", notify=True)

    def execute_task(self, key, func):
        """统一执行单个子任务。"""
        if isinstance(key, str):
            if not self.config.get(key, False):
                return True
        self.current_task_key = key
        self.log_info(f"开始任务: {key}")
        self.ensure_main()
        result = func()

        if result is False:
            self.task_status["failed"].append(key)
            self.screenshot(f'{datetime.now().strftime("%Y%m%d")}_DailyTask_FailTask_{key}')
            self.log_info(f"任务 {key} 执行失败", notify=True)
            return False
        self.task_status["success"].append(key)
        self.current_task_key = None
        return True

    def launch_end_command_non_blocking(self):
        command = str(self.config.get("结尾外部命令", "")).strip()
        if not command:
            self.log_info("结尾外部命令为空，跳过执行")
            return True

        kwargs = {
            "shell": False,
            "close_fds": True,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        else:
            kwargs["start_new_session"] = True

        command_args = None
        try:
            command_args = shlex.split(command, posix=(os.name != "nt"))
            if not command_args:
                self.log_info("结尾外部命令解析后为空，跳过执行")
                return True
            process = subprocess.Popen(command_args, **kwargs)
            self.log_info(f"已启动结尾外部命令（非阻塞），pid={process.pid}")
            return True
        except Exception as e:
            failed_program = command_args[0] if command_args else command
            self.log_info(f"启动结尾外部命令失败 ({failed_program}): {e}", notify=True)
            return False
