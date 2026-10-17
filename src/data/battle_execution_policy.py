"""Explicit execution contracts, independent of native scene inference.

Zhuang's white-before-input/white-disappears acknowledgement was separately
approved by the user. It is not a general meaning of a highlight observation.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class BattleExecutionPolicy:
    uses_white_acknowledgement: bool = False

    def can_attempt(self, visible):
        return not self.uses_white_acknowledgement or visible is True

    def confirms_release(self, visible):
        return self.uses_white_acknowledgement and visible is False


ORDINARY_BATTLE_EXECUTION = BattleExecutionPolicy()
ZHUANG_BATTLE_EXECUTION = BattleExecutionPolicy(uses_white_acknowledgement=True)


def battle_execution_policy(mechanic_key):
    return ZHUANG_BATTLE_EXECUTION if mechanic_key == 'zhuang_fangyi' else ORDINARY_BATTLE_EXECUTION
