"""Repeat the real gather navigation path without challenge or combat actions."""

import re

from ok import TaskDisabledException
from qfluentwidgets import FluentIcon

from src.core.sequence_parser import parse_sequence
from src.data.world_map import STAGE_CATEGORY_ENERGY_POOLING, stages_dict
from src.tasks.onetime.BattleTask import BattleContext, BattleTask


class GatherNavigationTestTask(BattleTask):
    """A manually started navigation-only runner with an ordered, repeatable plan."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "淤积点重复导航测试"
        self.description = "按编排顺序循环导航到淤积点，使用正式学习流程，不激发、不挑战、不战斗。"
        self.group_name = "工具与调试"
        self.group_icon = FluentIcon.DEVELOPER_TOOLS
        self.icon = FluentIcon.SEARCH
        self.visible = True
        self.default_config = {"淤积点序列": ["武陵城"], "循环次数": 5}
        self.config_description = {
            "淤积点序列": "按顺序执行，可重复添加同一淤积点；滑索路线沿用全局滑索配置。",
            "循环次数": "完整执行序列的次数，0 表示持续循环，直到手动停止。",
        }
        self.config_type = {
            "淤积点序列": {
                "options_available": list(stages_dict[STAGE_CATEGORY_ENERGY_POOLING]),
                "allow_duplication": True,
            },
            "循环次数": {"min": 0, "max": 100000},
        }
        self.default_config_group = {}

    def _navigation_plan(self):
        stages = parse_sequence(self.config.get("淤积点序列"))
        allowed = stages_dict[STAGE_CATEGORY_ENERGY_POOLING]
        if not stages or any(stage not in allowed for stage in stages):
            raise ValueError(self.tr("请编排有效的淤积点序列。"))
        count = self.config.get("循环次数", 5)
        if isinstance(count, bool) or re.fullmatch(r"\d+", str(count)) is None:
            raise ValueError(self.tr("循环次数必须为非负整数，0 表示持续循环。"))
        return stages, int(count)

    def _navigate_once(self, stage):
        self.battle_ctx = BattleContext(
            stage_name=stage,
            category_name=STAGE_CATEGORY_ENERGY_POOLING,
            enter_text="挑战",
        )
        self._open_index()
        if not self.to_stage():
            return False
        if not self.navigate_to_gather():
            return False
        # Only observe the challenge prompt. Never click it or the activation UI.
        return bool(
            self.wait_ocr(
                match=self.lang.daily_battle_mixin.k_bfe73e18,
                box=self.box_of_screen(0.679, 0.620, 0.714, 0.769),
                time_out=3,
                settle_time=0.5,
                raise_if_not_found=False,
            )
        )

    def run(self):
        stages, cycles = self._navigation_plan()
        success = failed = cycle = 0
        try:
            while cycles == 0 or cycle < cycles:
                cycle += 1
                for stage in stages:
                    self.sleep(0.01)
                    self.info_set("测试进度", f"{cycle}/{cycles or '∞'} · {stage}")
                    started = self.active_time()
                    try:
                        arrived = self._navigate_once(stage)
                    except TaskDisabledException:
                        raise
                    except Exception as error:
                        arrived = False
                        self.log_info(f"[gather-nav-test] cycle={cycle} stage={stage} error={error}")
                    finally:
                        for key in ("w", "a", "s", "d"):
                            self.send_key_up(key)
                    success += int(bool(arrived))
                    failed += int(not arrived)
                    self.log_info(
                        f"[gather-nav-test] cycle={cycle} stage={stage} arrived={bool(arrived)} "
                        f"elapsed={self.active_time() - started:.2f}s success={success} failed={failed}"
                    )
        finally:
            self.release_yolo_detector()
        summary = self.tr("淤积点导航测试结束：成功 {success} 次，失败 {failed} 次。").format(
            success=success,
            failed=failed,
        )
        self.log_info(summary, notify=True)
        if failed:
            self.mark_task_failure(summary)
        return failed == 0
