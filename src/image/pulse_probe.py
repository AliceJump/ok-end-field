"""独立白色脉冲探针——不依赖任何战斗配置，观测脉冲并落盘。

目的：建立「哪些位置（按钮区域）在实战中出现过白色脉冲」的实测清单，
为后续把官方推荐时机反哺排轴提供数据依据。与
:mod:`src.image.recommend_skill_detector` 的关系：

- 复用同一套白色占比算法（``white_ratio``）与确认阈值（``PULSE_ON_RATIO``）；
- 但**使用独立的去抖实例与独立节流**——不与「自动释放推荐技能」的共享
  detector 混用状态（两者采样节奏不同，且闪光过滤的 reset 语义不同）；
- **只记录不按键**：探针从不产生任何输入动作，不影响战斗行为。

独立性约束：
- 自带配置开关 :data:`KEY_PULSE_PROBE`（默认开），不受「自动技能列表 /
  推荐技能」等战斗配置组合影响；
- 挂载点在 ``AutoCombatLogic.run`` 主循环（所有走 ``auto_battle`` 的战斗——
  主线/日常/演示/影拓——每帧必经），与战斗模式（普通/排轴/实时条件）无关；
- 队伍信息**尽力而为**：读得到 ``_battle_member_count`` / ``_battle_team``
  就随事件记录映射结果，读不到则监测全部 4 个区域、字段记 null，
  绝不因队伍识别缺失而停止观测。

落盘：``config_path("pulse_probe_log.jsonl")``，JSONL 追加写；单条失败
不影响战斗（静默降级为节流一次的 debug 日志）。
"""

from __future__ import annotations

import contextlib
import json
import threading
from datetime import datetime

from src.core.BattleConfig import KEY_PULSE_PROBE, RECOMMEND_SKILL_REGIONS
from src.image.recommend_skill_detector import (
    PULSE_ON_RATIO,
    RecommendSkillDetector,
)

__all__ = ["PulseProbe", "get_pulse_probe"]

# 采样节流（秒）：上升沿 OFF 判定需连续 3 帧低占比，0.25s 采样下
# 复位耗时 ≈0.75s，远短于脉冲周期；同时把圆周采样开销压到可忽略。
_SAMPLE_INTERVAL = 0.25


class PulseProbe:
    """白色脉冲观测器：上升沿 → JSONL 落盘，只记录不按键。"""

    def __init__(self, log_path=None):
        """":param log_path: 落盘路径；None 时按 ``config_path`` 惰性解析
        （测试注入临时路径用，生产走默认单例即可）。"""
        self._lock = threading.RLock()
        self._detector = RecommendSkillDetector()  # 探针私有去抖状态
        self._last_sample_t = 0.0
        self._log_error_reported = False
        self._log_path = log_path

    def reset(self) -> None:
        """清空去抖状态与节流计时（供测试或战斗边界复位）。"""
        with self._lock:
            self._detector.reset()
            self._last_sample_t = 0.0

    def observe(self, task) -> None:
        """对当前帧做一次脉冲观测（由战斗循环每帧调用）。

        :param task: battle_mixin 实例（需提供 frame / active_time /
            get_battle_config / log_debug，队伍字段可选）。
        """
        try:
            if not task.get_battle_config(KEY_PULSE_PROBE, True):
                return
            now = task.active_time()
            with self._lock:
                if now - self._last_sample_t < _SAMPLE_INTERVAL:
                    return
                self._last_sample_t = now

            frame = task.frame
            if frame is None or frame.size == 0:
                return

            # 人数信息尽力而为：读不到时监测全部 4 区域（独立探针语义）
            member_count = getattr(task, "_battle_member_count", None)
            try:
                member_count = int(member_count or 0)
            except (TypeError, ValueError):
                member_count = 0
            if 1 <= member_count <= len(RECOMMEND_SKILL_REGIONS):
                active_regions = RECOMMEND_SKILL_REGIONS[-member_count:]
            else:
                member_count = 0
                active_regions = list(RECOMMEND_SKILL_REGIONS)

            # 本帧各区域占比只算一次
            ratios = {
                str(r["label"]): self._detector.white_ratio(
                    frame, float(r["x"]), float(r["y"]), float(r["button_radius"])
                )
                for r in active_regions
            }

            # 全屏闪光过滤：≥3 区域同时全白 = 大招演出/爆炸，非单按钮脉冲；
            # 复位全部区域，允许后续真实脉冲重新产生上升沿。
            if len(active_regions) >= 3 and all(
                ratios[str(r["label"])] >= PULSE_ON_RATIO for r in active_regions
            ):
                for r in active_regions:
                    self._detector.reset_label(str(r["label"]))
                return

            # 上升沿 → 落盘（slot = 激活区域从左到右位次，与推荐技能按键编号一致）
            for slot, region in enumerate(active_regions, start=1):
                label = str(region["label"])
                if self._detector.detect_ratio(ratios[label], label):
                    self._record(task, label, slot, ratios[label], member_count)
        except Exception:  # 探针绝不干扰战斗
            self._log_observe_error(task)

    def _record(self, task, label: str, slot: int, ratio: float, member_count: int) -> None:
        """把一次脉冲上升沿写入 JSONL（追加，LF 行尾）。"""
        team = getattr(task, "_battle_team", None)
        char = None
        if isinstance(team, (list, tuple)) and 1 <= slot <= len(team):
            char = team[slot - 1] if team[slot - 1] != "?" else None

        entry = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "active_t": round(float(task.active_time()), 3),
            "label": label,
            "slot": slot,
            "key": str(slot),
            "char": char,
            "team": list(team) if isinstance(team, (list, tuple)) else None,
            "ratio": round(float(ratio), 3),
            "member_count": member_count or None,
            "task": type(task).__name__,
        }
        try:
            if self._log_path is not None:
                path = self._log_path
            else:
                from src.core.paths import config_path

                path = config_path("pulse_probe_log.jsonl")
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            task.log_debug(f"脉冲探针记录: {label} slot={slot} char={char} ratio={entry['ratio']}")
            self._log_error_reported = False
        except Exception:
            self._log_observe_error(task)

    def _log_observe_error(self, task) -> None:
        """观测/落盘异常按节流原则只报一次，避免刷屏。"""
        if self._log_error_reported:
            return
        self._log_error_reported = True
        with contextlib.suppress(Exception):
            task.log_debug("脉冲探针观测异常（本次战斗内不再重复报告）")


_shared_lock = threading.Lock()
_shared_probe: PulseProbe | None = None


def get_pulse_probe() -> PulseProbe:
    """返回进程级共享探针实例（状态随应用生命周期存活）。"""
    global _shared_probe
    if _shared_probe is None:
        with _shared_lock:
            if _shared_probe is None:
                _shared_probe = PulseProbe()
    return _shared_probe
