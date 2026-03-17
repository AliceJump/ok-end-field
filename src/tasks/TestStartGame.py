from src.tasks.BaseEfTask import BaseEfTask
from ok import TaskDisabledException
import time
class TestStartGame(BaseEfTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "启动一次游戏,120s后自动关闭"
        self.description = "配合任务计划程序提前启动游戏,防止游戏更新/弹公告导致的后续问题"
        self.support_schedule_task = True
    def run(self):
        kill = True
        try:
            self.ensure_main(time_out=120)
            self.log_info("成功启动游戏,等待15s后自动关闭(可禁用)", notify=True)
            time.sleep(15)
        except TaskDisabledException:
            kill = False
            raise
        finally:
            if kill:
                self.kill_game()
