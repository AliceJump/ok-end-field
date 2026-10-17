"""测试任务：游戏内登出后由游戏自行重新进入同一账号（不切换账号、不杀进程）。"""

from ok import TaskDisabledException
from qfluentwidgets import FluentIcon

from src.core.BaseEfTask import BaseEfTask
from src.tasks.mixin.login_mixin import LoginMixin


class TestReloginTask(LoginMixin, BaseEfTask):
    """游戏内登出后用同一账号重新进入，不切换账号、不杀游戏进程。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "测试重新进入游戏"
        self.group_name = "工具与调试"
        self.description = "游戏内登出后用同一账号重新进入，不切换账号、不杀游戏进程"
        self.icon = FluentIcon.SYNC

    def run(self):
        try:
            self.log_info("开始测试：游戏内登出并重新进入", notify=True)
            if self.relogin_same_account():
                self.log_info("重新进入游戏成功", notify=True)
            else:
                self.log_warning("重新进入游戏被中止（窗口激活或登出确认失败）", notify=True)
        except TaskDisabledException:
            raise
        except Exception as e:
            self.handle_task_exception(e, "TestReloginTask")
