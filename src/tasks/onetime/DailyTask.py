import os
import tempfile
import threading
import webbrowser
from pathlib import Path

from qfluentwidgets import FluentIcon

from src.core.email_service import send_daily_summary_email
from src.tasks.account.account_mixin import AccountMixin
from src.tasks.daily.daily_feature import DailyFeature
from src.tasks.daily.daily_task_runner import DailyTaskRunner
from src.tasks.daily.finally_file import (
    create_task_summary_report,
)
from src.tasks.mixin.common import Common
from src.tasks.mixin.end_command_mixin import EndCommandMixin
from src.tasks.onetime.ActivityRewardTask import ActivityRewardTask
from src.tasks.onetime.BoatHarvestTask import BoatHarvestTask
from src.tasks.onetime.BoatOrganizeTask import BoatOrganizeTask
from src.tasks.onetime.CraftWeaponTask import CraftWeaponTask
from src.tasks.onetime.CreditCollectTask import CreditCollectTask
from src.tasks.onetime.CreditShopTask import CreditShopTask
from src.tasks.onetime.DailyRewardTask import DailyRewardTask
from src.tasks.onetime.DeliverySendTask import DeliverySendTask
from src.tasks.onetime.DailyBattleTask import DailyBattleTask
from src.tasks.onetime.DailyDeliveryTask import DailyDeliveryTask
from src.tasks.onetime.DemoBattleTask import DemoBattleTask
from src.tasks.onetime.HomePointTask import HomePointTask
from src.tasks.onetime.LiaisonGiftTask import LiaisonGiftTask
from src.tasks.onetime.MailTask import MailTask
from src.tasks.onetime.RegionalBuildTask import RegionalBuildTask


class DailyTask(Common, EndCommandMixin, AccountMixin):
    """日常任务聚合执行器。

    子任务均为独立任务类（可单独运行），经 DailyFeature 包装组合接入，
    日常不继承子任务业务逻辑；子任务参数存在各自任务的配置文件里，
    经「账号配置」页可按账号覆盖。
    """

    BOAT_STATE_TASK_KEYS = frozenset(
        {
            "⭐帝江号整理",
            "⭐帝江号收菜",
        }
    )

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
        self.description = "子任务开关用⭐标出，自上而下顺序执行，默认展开在最前面的『⭐⭐⭐ 默认』分组，最后执行『日常奖励』。\n子任务参数在各子任务卡片上配置（任务列表「日常任务」分组），并支持在「账号配置」页按账号覆盖。\n如果出现反复按ESC的情形，请调高『设置/主界面单次动作后延迟』（建议1.5以上）。"

        self.support_schedule_task = True
        self.support_multi_account = True
        self.daily_runner: DailyTaskRunner | None = None

        # 子任务包装器：执行时从 executor 解析已注册实例（机制见 daily_feature.py）。
        # 参数已迁到子任务配置的三项（帝江号收菜/地区建设/活动奖励）与多开关 OR 的
        # 帝江号整理，开关判定改用谓词读取子任务配置。
        self.gift_feature = DailyFeature(self, LiaisonGiftTask, switch_key="⭐送礼", run_method="execute_gift_task")
        self.organize_feature = DailyFeature(
            self,
            BoatOrganizeTask,
            switch_key="⭐帝江号整理",
            run_method="boat_organize",
            predicate=lambda: (
                bool(self.organize_feature.impl_config("⭐帝江号一键存放"))
                or bool(self.organize_feature.impl_config("⭐简易制作"))
            ),
        )
        self.harvest_feature = DailyFeature(
            self,
            BoatHarvestTask,
            switch_key="⭐帝江号收菜",
            run_method="boat_claim_rewards",
            predicate=lambda: bool(self.harvest_feature.impl_config("⭐帝江号收菜")),
        )
        self.mail_feature = DailyFeature(self, MailTask, switch_key="⭐收邮件", run_method="run_mail")
        self.delivery_send_feature = DailyFeature(
            self, DeliverySendTask, switch_key="⭐转交运送委托", run_method="delivery_send_others"
        )
        self.delivery_feature = DailyFeature(
            self, DailyDeliveryTask, switch_key="⭐自动送货", run_method="run_daily"
        )
        self.regional_feature = DailyFeature(
            self,
            RegionalBuildTask,
            switch_key="⭐地区建设",
            run_method="run_regional",
            predicate=lambda: bool(self.regional_feature.impl_config("⭐地区建设")),
        )
        self.craft_feature = DailyFeature(self, CraftWeaponTask, switch_key="⭐造装备", run_method="make_weapon")
        self.credit_feature = DailyFeature(
            self, CreditCollectTask, switch_key="⭐收信用", run_method="run_credit_collect"
        )
        self.shop_feature = DailyFeature(self, CreditShopTask, switch_key="⭐买信用商店", run_method="credit_shop")
        self.battle_feature = DailyFeature(self, DailyBattleTask, switch_key="⭐刷体力", run_method="run_battle")
        self.activity_feature = DailyFeature(
            self,
            ActivityRewardTask,
            switch_key="⭐活动奖励",
            run_method="claim_activity_rewards",
            predicate=lambda: bool(self.activity_feature.impl_config("⭐活动奖励")),
        )
        self.daily_reward_feature = DailyFeature(
            self, DailyRewardTask, switch_key="⭐日常奖励", run_method="claim_daily_rewards"
        )
        self.demo_feature = DailyFeature(self, DemoBattleTask, switch_key="⭐演算", run_method="battle_demo")
        self.home_point_feature = DailyFeature(
            self, HomePointTask, switch_key="⭐传送到帝江号右侧传送点", run_method="run_home_point"
        )

        self.config_description.update(
            {
                "⭐送礼": (
                    "是否通过「帝江号/干员联络台/赠送礼物」提升员好感度。\n"
                    "如果途中偶遇干员，则直接交互完成送礼。\n"
                    "任务开始时候，角色不能位于「帝江号/剑桥」传送点附近。"
                ),
                "⭐收邮件": "是否前往「邮箱」领取邮件。",
                "⭐转交运送委托": ("是否在「地区建设/仓储结点」中转交全部运送委托，并领取一次转交委托奖励。"),
                "⭐自动送货": "是否执行自动接取并完成运输委托。",
                "⭐造装备": (
                    "是否前往「装备制造/套组装备制造」并制作一件列表首位的装备。\n请确保有足够的装备原件和调度券。"
                ),
                "⭐收信用": (
                    "是否前往好友的「帝江号」并在「访客终端」上进行助力获得信用。\n"
                    "助力结束后，前往「采购中心/信用交易所」收取全部助力。"
                ),
                "⭐买信用商店": ("是否在「采购中心/信用交易所」采购。\n自动刷新 且 仅购买「武库配额」「嵌晶玉」。"),
                "⭐刷体力": ("是否消耗所有「理智」刷取培养材料。"),
                "⭐日常奖励": ("是否领取「行动手册/日常」和「通行证」中的奖励。"),
                "⭐演算": "是否执行演武集算任务",
                "⭐传送到帝江号右侧传送点": "是否在日常任务结束后传送到帝江号右侧传送点。",
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
        self.default_config.update(
            {
                "⭐送礼": True,
                "⭐收邮件": True,
                "⭐转交运送委托": True,
                "⭐自动送货": False,
                "⭐造装备": True,
                "⭐收信用": True,
                "⭐买信用商店": True,
                "⭐刷体力": True,
                "⭐日常奖励": True,
                "⭐演算": True,
                "⭐传送到帝江号右侧传送点": True,
                "配置选择": "⭐⭐⭐ 默认",
                "发生异常时终止游戏": False,
                "仅退出游戏": False,
                "自动打开汇总文件": True,
                "邮件发送汇总": False,
            }
        )
        task_group = {
            "⭐⭐⭐ 默认": [item[0] for item in self.build_task_plan() if item[0] in self.default_config]
            + ["⭐执行外部命令"],
        }

        # 合并两个分组字典
        all_groups = {
            **task_group,
            **self.default_config_group,
            **{"其他配置": ["发生异常时终止游戏", "仅退出游戏", "邮件发送汇总", "自动打开汇总文件"]},
        }

        self.register_config_groups(all_groups)
        self.add_exit_after_config()
        if self.debug:
            self.default_config.update({"重复测试的次数": 1})

    def build_task_plan(self):
        return [
            self.gift_feature.plan_item(),
            self.organize_feature.plan_item(),
            self.harvest_feature.plan_item(),
            self.mail_feature.plan_item(),
            self.delivery_send_feature.plan_item(),
            self.delivery_feature.plan_item(),
            self.regional_feature.plan_item(),
            self.craft_feature.plan_item(),
            self.credit_feature.plan_item(),
            self.shop_feature.plan_item(),
            self.battle_feature.plan_item(),
            self.activity_feature.plan_item(),
            self.daily_reward_feature.plan_item(),
            self.demo_feature.plan_item(),
            self.home_point_feature.plan_item(),
        ]

    def run(self):
        """日常任务主入口。"""
        self.active_and_send_mouse_delta(only_activate=True)
        repeat_times = self.config.get("重复测试的次数", 1) if self.debug else 1
        try:
            task_plan = self.build_task_plan()
            # 根据配置决定外部命令的执行时机
            end_cmd_task = ("⭐执行外部命令", self.launch_end_command_non_blocking)
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

    def _send_daily_summary_email(self, summary_path: str | Path):
        """后台发送日常汇总邮件，不阻塞任务结束；失败仅记录日志。"""

        def _run():
            try:
                summary_text = Path(summary_path).read_text(encoding="utf-8")
                status_data = self._build_summary_status_data()
                recipient = send_daily_summary_email(summary_text, status_data=status_data)
                self.log_info(f"日常汇总邮件已发送至: {recipient}", notify=True)
            except Exception as e:
                self.log_info(f"日常汇总邮件发送失败: {e}", notify=True)

        threading.Thread(target=_run, daemon=True, name="DailySummaryEmailSender").start()

    def _build_summary_status_data(self) -> dict:
        """从 daily_runner.final_summary 提取邮件状态展示数据。"""
        summary = self.daily_runner.final_summary if self.daily_runner else {}
        per_round = summary.get("per_round") or []

        success_count = sum(len(r.get("success", [])) for r in per_round)
        failed_count = sum(len(r.get("failed", [])) for r in per_round)
        skipped_count = sum(len(r.get("skipped", [])) for r in per_round)

        status = str(summary.get("status", "未开始") or "未开始")
        status_en_map = {
            "完成": "COMPLETED",
            "完成后退出": "COMPLETED",
            "部分失败": "PARTIAL",
            "运行中": "RUNNING",
            "异常结束": "FAILED",
            "未开始": "IDLE",
        }
        # 未知状态不默认当作成功，避免邮件把异常/新增状态误标为绿色完成
        status_en = status_en_map.get(status, "WARN")
        # 存在失败任务时，即使状态为完成也降级为 PARTIAL，与统计数字保持一致
        if status_en == "COMPLETED" and failed_count > 0:
            status_en = "PARTIAL"

        failed_details = self._build_failed_details()

        return {
            "status": status,
            "status_en": status_en,
            "total_rounds": summary.get("actual_repeat_total", len(per_round)),
            "success_count": success_count,
            "failed_count": failed_count,
            "skipped_count": skipped_count,
            "failed_details": failed_details,
            "system_name": "OK-EF",
            "report_type": "DAILY",
        }

    def _build_failed_details(self) -> list[dict]:
        """从 final_summary 的 failure_details 构建失败任务明细列表。"""
        summary = self.daily_runner.final_summary if self.daily_runner else {}
        failure_details = summary.get("failure_details") or {}
        per_round = summary.get("per_round") or []

        # account_id -> account_user 映射（用于显示账号名）
        id_to_user = {}
        for r in per_round:
            aid = str(r.get("account_id", "") or "")
            user = str(r.get("account_user", "") or "")
            if aid:
                id_to_user[aid] = user

        details = []
        for account_id, tasks in failure_details.items():
            if not isinstance(tasks, dict):
                continue
            account_name = id_to_user.get(str(account_id), "") or (str(account_id) if account_id else "无")
            for task_name, reason in tasks.items():
                display_task = str(task_name).lstrip("⭐").strip()
                details.append(
                    {
                        "account": account_name,
                        "task": display_task,
                        "reason": str(reason or ""),
                    }
                )
        return details

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

            # 根据开关决定是否将最终汇总通过邮件发送
            if self.config.get("邮件发送汇总", False):
                self._send_daily_summary_email(summary_path)

            return True
        except Exception as e:
            self.log_info(f"创建日常任务结尾文件失败: {e}", notify=True)
            return False
