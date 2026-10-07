"""Timing combat compatibility adapter without the legacy fixed startup delay."""

from src.tasks.onetime.TimedCombatLogic import TimedCombatLogic as _TimedCombatLogic


class ImmediateTimedCombatLogic(_TimedCombatLogic):
    """Run timing combat immediately once AutoCombat has confirmed combat."""

    def run(self, start_sleep=None, no_battle=False, deadline=None):
        # ``start_sleep`` remains accepted because AutoCombat/battle_mixin still
        # pass the historical argument. Enemy-presence gating now owns startup
        # readiness, so a second fixed delay only adds latency.
        return super().run(start_sleep=0, no_battle=no_battle, deadline=deadline)
