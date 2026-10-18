"""Repeat the real liaison initial alignment without navigation or gift actions."""

import re

from ok import TaskDisabledException
from qfluentwidgets import FluentIcon

from src.data.FeatureList import FeatureList as fL
from src.tasks.mixin.common import Common
from src.tasks.mixin.liaison_mixin import LiaisonMixin


class LiaisonAlignmentTestTask(Common, LiaisonMixin):
    """Re-enter the bridge every round and observe the production alignment result."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "联络站重复对中测试"
        self.description = "每轮重新传送到舰桥，按实际走一步或两步分别学习联络站首次对中；不聊天、不送礼。"
        self.group_name = "工具与调试"
        self.group_icon = FluentIcon.DEVELOPER_TOOLS
        self.icon = FluentIcon.SEARCH
        self.visible = True
        self.default_config = {"循环次数": 5}
        self.config_description = {"循环次数": "每轮重新传送并完成一次对中；0 表示持续循环，直到手动停止。"}
        self.config_type = {"循环次数": {"min": 0, "max": 100000}}
        self.default_config_group = {}

    def align_ocr_or_find_target_to_center(self, *args, **kwargs):
        result = super().align_ocr_or_find_target_to_center(*args, **kwargs)
        self._test_alignment_result = bool(result)
        return result

    def _align_once(self):
        self._test_alignment_result = False
        self._liaison_walk_attempts = 0
        self._liaison_hall_confirmed = False
        self._liaison_transfer_entry = None
        self._liaison_station_entry = None
        try:
            prepared = self.prepare_liaison_station_entry()
        finally:
            entry = vars(self).pop("_liaison_station_entry", None)
            context = entry.context if entry is not None else None
            self.log_info(
                f"[liaison-align-test] walk={self._liaison_walk_attempts} "
                f"hall_confirmed={self._liaison_hall_confirmed} "
                f"fingerprint={context.fingerprint if context else 'none'} learning_entry={entry is not None}"
            )
        if not prepared:
            return False
        self.ensure_main()
        self.ensure_map()
        tracked = self.start_tracking_and_align_target(
            fL.operator_liaison_station, fL.operator_liaison_station_out_map, learning_entry=entry
        )
        return bool(tracked and self._test_alignment_result)

    def run(self):
        count = self.config.get("循环次数", 5)
        if isinstance(count, bool) or re.fullmatch(r"\d+", str(count)) is None:
            raise ValueError(self.tr("循环次数必须为非负整数，0 表示持续循环。"))
        cycles = int(count)
        success = failed = cycle = 0
        while cycles == 0 or cycle < cycles:
            self.sleep(0.01)
            cycle += 1
            self.info_set("测试进度", f"{cycle}/{cycles or '∞'}")
            started = self.active_time()
            try:
                aligned = self._align_once()
            except TaskDisabledException:
                raise
            except Exception as error:
                aligned = False
                self.log_info(f"[liaison-align-test] cycle={cycle} error={error}")
            finally:
                for key in ("w", "a", "s", "d"):
                    self.send_key_up(key)
            success += int(bool(aligned))
            failed += int(not aligned)
            self.log_info(
                f"[liaison-align-test] cycle={cycle} aligned={bool(aligned)} "
                f"elapsed={self.active_time() - started:.2f}s success={success} failed={failed}"
            )
        summary = self.tr("联络站对中测试结束：成功 {success} 次，失败 {failed} 次。").format(
            success=success, failed=failed
        )
        self.log_info(summary, notify=True)
        if failed:
            self.mark_task_failure(summary)
        return failed == 0
