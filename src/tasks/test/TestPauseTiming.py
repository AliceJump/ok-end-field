import time

from qfluentwidgets import FluentIcon

from src.core.BaseEfTask import BaseEfTask


class TestPauseTiming(BaseEfTask):
    """暂停计时验证任务（对应 Test.py 历史版本：695d8c9）。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "暂停计时验证"
        self.group_name = "测试"
        self.group_icon = FluentIcon.DEVELOPER_TOOLS
        self.description = "验证暂停任务期间活动计时是否冻结"
        self.visible = self.debug
        self.default_config = {
            "测试模式": "两者",
            "测试超时(秒)": 10,
        }
        self.config_type = {
            "测试模式": {
                "type": "drop_down",
                "options": ["wait_until", "自写循环", "两者"],
            },
        }
        self.config_description = {
            "测试模式": "选择要运行的暂停计时测试",
            "测试超时(秒)": "暂停时间应超过此值，用于观察恢复后的行为",
        }

    def run(self):
        mode = self.config.get("测试模式", "两者")
        timeout = max(1.0, float(self.config.get("测试超时(秒)", 10)))

        if mode in ("wait_until", "两者"):
            self._test_wait_until(timeout)
        if mode in ("自写循环", "两者"):
            self._test_manual_wall_clock(timeout)

    def _test_wait_until(self, timeout: float):
        self.log_info(
            f"[暂停测试/wait_until] 开始，超时={timeout:.1f}s；"
            "请现在暂停任务，保持暂停超过超时时间后再恢复。"
        )
        started = self.active_time()
        result = self.wait_until(
            lambda: False,
            time_out=timeout,
            raise_if_not_found=False,
        )
        elapsed = self.active_time() - started
        self.log_info(
            f"[暂停测试/wait_until] 返回 result={result!r}，"
            f"活动耗时={elapsed:.2f}s；恢复后未立即超时即表示暂停计时已冻结。",
            notify=True,
        )

    def _test_manual_wall_clock(self, timeout: float):
        self.log_info(
            f"[暂停测试/自写循环] 开始，超时={timeout:.1f}s；"
            "请现在暂停任务，保持暂停超过超时时间后再恢复。"
        )
        started = time.time()
        while True:
            elapsed = time.time() - started
            if elapsed >= timeout:
                self.log_info(
                    f"[暂停测试/自写循环] 判定超时，墙上耗时={elapsed:.2f}s；"
                    "恢复后立即结束，说明该写法未适配暂停。",
                    notify=True,
                )
                return
            self.sleep(0.1)
