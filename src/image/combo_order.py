"""Battle-local portrait precedence hints; never a live queue or cast rule."""

from itertools import combinations


class ComboOrder:
    def __init__(self):
        self.before = set()
        self.revision = 0

    def clear(self):
        self.before.clear()
        self.revision += 1

    @staticmethod
    def _closure(pairs):
        result = set(pairs)
        while True:
            added = {(a, d) for a, b in result for c, d in result if b == c}
            if added <= result:
                return result
            result.update(added)

    def learn(self, slots):
        """Only full-team matches supply evidence; duplicate faces imply no order."""
        ids = [s.native_id for s in slots if s.native_id and s.match_scope == "team"]
        unique = [native_id for native_id in ids if ids.count(native_id) == 1]
        incoming = set(combinations(unique, 2))
        merged = self._closure(self.before | incoming)
        conflict = any(a == b for a, b in merged)
        if conflict:
            # New pixels win over history. Relearn this frame, not a guessed
            # total permutation, and discard transitive stale constraints too.
            self.clear()
            merged = self._closure(incoming)
        if merged != self.before:
            self.before = merged
            self.revision += 1
        return conflict

    def after(self, observed_left, candidates):
        """Exclude only known predecessors of an actually observed left face."""
        return {
            key: value
            for key, value in candidates.items()
            if not any((key, left) in self.before for left in observed_left)
        }
