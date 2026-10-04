"""Timed combat with expected-SP guided visual sampling.

The established scheduler remains in ``TimedCombatLogicBase``. This thin layer
only separates predicted SP from visual truth and feeds the prediction into the
same-frame colour probe.
"""

from __future__ import annotations

import time

from src.image.skill_bar_expected_probe import read_expected_skill_bar_sp
from src.tasks.onetime.TimedCombatLogicBase import TimedCombatLogic as _TimedCombatLogicBase


class TimedCombatLogic(_TimedCombatLogicBase):
    def __init__(self, *args, wall_clock=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Scheduler time may be injected/paused in tests. SP regeneration must
        # follow real elapsed wall time, so it has an independent monotonic clock.
        self._sp_wall_clock = wall_clock or time.monotonic
        self.last_observed_sp = -1.0
        self.expected_sp = -1.0
        self.last_visual_sp_wall_time = None

    def _cache_visual_sp(self, sp, now=None, wall_now=None):
        """Commit a successful visual observation as the new prediction anchor."""
        now = self._clock() if now is None else now
        wall_now = self._sp_wall_clock() if wall_now is None else wall_now
        value = float(sp)
        self.cached_sp = value
        self.expected_sp = value
        self.last_observed_sp = value
        self.last_sp_probe_at = now
        self.last_visual_sp_wall_time = wall_now
        self.next_sp_probe_at = now + self._sp_probe_interval(value)
        return value

    def _project_probe_expected(self, wall_now=None):
        """Add natural regen once for the full visual-to-visual wall-clock window."""
        base = self.expected_sp
        anchor = self.last_visual_sp_wall_time
        if base < 0 or anchor is None:
            return None
        wall_now = self._sp_wall_clock() if wall_now is None else wall_now
        elapsed = max(0.0, float(wall_now) - float(anchor))
        return min(300.0, max(0.0, base + elapsed * self._NATURAL_SP_PER_SECOND))

    def _sample_sp(self, force=False):
        """Sample SP, using the current prediction only to choose the first ROI."""
        now = self._clock()
        wall_now = self._sp_wall_clock()

        # Keep the established cheap pressure-line full probe. A positive result
        # is real visual evidence, so it resets both truth and prediction anchors.
        if (
            not force
            and self.cached_sp >= self.sp_pressure_threshold
            and hasattr(self.task, "is_skill_bar_full_fast")
            and self.task.is_skill_bar_full_fast()
        ):
            return self._cache_visual_sp(300.0, now, wall_now)

        if not force and now < self.next_sp_probe_at:
            return self.cached_sp

        probe_expected = self._project_probe_expected(wall_now)
        observed = read_expected_skill_bar_sp(self.task, probe_expected, frame=getattr(self.task, "frame", None))
        if observed >= 0:
            return self._cache_visual_sp(observed, now, wall_now)

        # Failed visual reads do not overwrite the prediction, the last visual
        # truth, or the wall-clock anchor. Retry soon using the same window.
        self.cached_sp = -1.0
        self.next_sp_probe_at = now + self._SP_UNKNOWN_PROBE_INTERVAL
        return -1.0

    def _note_assumed_sp_spend(self, before_sp, expected_cost):
        """Apply unverified low-cost spending to prediction only.

        Natural regeneration is intentionally not materialized here. It is added
        once, immediately before the next visual probe, for the whole interval
        since the previous successful visual observation.
        """
        if before_sp < 0:
            self.next_sp_probe_at = self._clock()
            return

        base = self.expected_sp if self.expected_sp >= 0 else float(before_sp)
        predicted = max(0.0, base - max(0.0, float(expected_cost)))
        self.expected_sp = predicted
        self.cached_sp = predicted
        now = self._clock()
        self.next_sp_probe_at = now + self._sp_probe_interval(predicted)
