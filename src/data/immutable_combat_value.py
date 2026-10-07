"""Share only recursively immutable authored values across counterfactual worlds."""

from copy import deepcopy
from dataclasses import fields
from enum import Enum


def _immutable(value):
    if value is None or type(value) in {bool, int, float, str, bytes} or isinstance(value, Enum):
        return True
    if type(value) in {tuple, frozenset}:
        return all(_immutable(item) for item in value)
    return isinstance(value, ImmutableCombatValue) and value._share_on_deepcopy


class ImmutableCombatValue:
    """Frozen does not imply immutable: mutable descendants disable sharing.

    The decision is cached at construction, when the complete value graph is
    available. A shared graph contains no containers or descendants that can
    subsequently mutate; unsafe values keep ordinary deep-copy isolation.
    """

    def __post_init__(self):
        object.__setattr__(self, "_share_on_deepcopy", all(_immutable(getattr(self, f.name)) for f in fields(self)))

    def __deepcopy__(self, memo):
        if self._share_on_deepcopy:
            memo[id(self)] = self
            return self
        result = type(self).__new__(type(self))
        memo[id(self)] = result
        for name, value in self.__dict__.items():
            object.__setattr__(result, name, deepcopy(value, memo))
        return result
