"""Temporal visual evidence, separate from raw sensing and native inference."""

from dataclasses import dataclass


@dataclass(frozen=True)
class StableBattleHighlight:
    skill_ids: tuple[str, ...]
    condition_json: str
    visible: bool | None
    observed_at: float


@dataclass
class _Track:
    identity: tuple
    stable: bool | None = None
    candidate: bool | None = None
    count: int = 0
    since: float = 0.0


class BattleHighlightStability:
    """Two bright frames; three dark frames spanning 150ms. No enemy facts."""

    DARK_SECONDS = .15
    MAX_GAP = .6

    def __init__(self):
        self.observations = {}
        self._tracks = {}
        self._frame = None
        self._fallback_stamp = None
        self._sampled_at = None
        self._shape = None
        self._sample_token = None

    def clear(self):
        self.__init__()

    def arm_disappearance(self, slot):
        """Only post-key dark samples can acknowledge the accepted contract."""
        if slot in self._tracks:
            track = self._tracks[slot]
            track.candidate, track.count = None, 0

    def observe(self, raw, now, *, frame=None, sample_token=None):
        shape = tuple(frame.shape[:2]) if frame is not None else None
        if self._sampled_at is not None and (now < self._sampled_at or now-self._sampled_at > self.MAX_GAP
                                             or shape != self._shape):
            self.clear()
        duplicate = (sample_token == self._sample_token if sample_token is not None else
                     frame is self._frame if frame is not None else now == self._fallback_stamp)
        duplicate = duplicate and self._sampled_at is not None
        for slot in set(self._tracks)-set(raw):
            self._tracks.pop(slot, None)
            self.observations.pop(slot, None)
        for slot, observation in raw.items():
            identity = observation.skill_ids, observation.condition_json
            track = self._tracks.get(slot)
            if track is None or track.identity != identity:
                track = self._tracks[slot] = _Track(identity)
            if duplicate and observation.visible is not None:
                # A new identity cannot borrow the previous button's result.
                self.observations[slot] = StableBattleHighlight(*identity, track.stable, self._sampled_at)
                continue
            value = observation.visible
            if value is None:
                track.stable, track.candidate, track.count = None, None, 0
            else:
                if track.candidate is not value:
                    track.candidate, track.count, track.since = value, 0, now
                track.count += 1
                confirmed = (track.count >= 2 if value else
                             track.count >= 3 and now-track.since >= self.DARK_SECONDS-1e-9)
                if confirmed:
                    track.stable = value
            self.observations[slot] = StableBattleHighlight(*identity, track.stable, now)
        if not duplicate:
            self._frame, self._fallback_stamp = frame, now
            self._sampled_at, self._shape = now, shape
            self._sample_token = sample_token
        return self.observations
