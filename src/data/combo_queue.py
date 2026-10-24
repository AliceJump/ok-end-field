"""Bind fresh fixed-slot portraits to unambiguous native combo buttons.

Queue order is a constraint, not permission to select a later E action. A face
does not establish a skill variant, remaining time, hit or successful cast.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from src.image.combo_skill_detector import ComboObservation

CATALOG = Path(__file__).resolve().parents[2] / "assets/data/combo_buttons"
MAX_OBSERVATION_AGE = .5


@lru_cache(maxsize=4)
def read_combo_buttons(path=CATALOG):
    path = Path(path)
    index = json.loads((path / "index.json").read_bytes())
    raw = (path / "buttons.json").read_bytes()
    buttons = json.loads(raw)
    if (index["schema_version"] != 1 or hashlib.sha256(raw).hexdigest() != index["buttons_sha256"]
            or len(buttons) != index["buttons"] or not index["native_inputs"]
            or len({b["skill_id"] for b in buttons}) != len(buttons)):
        raise ValueError("Invalid native combo button catalog")
    for button in buttons:
        if (not button["skill_id"].startswith(button["owner_native_id"] + "_")
                or not button["character"] or not button["portrait_native_id"]
                or len(button["source_sha256"]) != 64):
            raise ValueError("Invalid combo button ownership")
    return tuple(buttons)


@dataclass(frozen=True)
class QueuedCombo:
    position: int
    slot: str
    character: str
    portrait_native_id: str
    skill_id: str
    profiles: tuple


def fresh_combo_observation(observation, team, now):
    return (isinstance(observation, ComboObservation) and observation.ready is True
            and observation.status == "observed" and observation.team == tuple(team)
            and observation.frame_size is not None and math.isfinite(now)
            and math.isfinite(observation.observed_at)
            and 0 <= now - observation.observed_at <= MAX_OBSERVATION_AGE)


def bind_combo_queue(observation, team, disabled_slots, store, now, *, catalog=CATALOG):
    """Return the known contiguous prefix. Unknown slots are never promoted.

    The catalog also checks cross-character icon reuse: Rossi's first button
    names Chen's portrait in this capture. Team membership alone cannot resolve
    that face when both potential owners are present (or a member is unknown).
    """
    if not fresh_combo_observation(observation, team, now):
        return ()
    buttons = read_combo_buttons(catalog)
    result = []
    incomplete = any(not name or name == "?" for name in team)
    for position, head in enumerate(observation.slots, 1):
        if position > 4 or head.position != position or head.character is None or head.native_id is None:
            break
        possible = [b for b in buttons if b["portrait_native_id"] == head.native_id
                    and (incomplete or b["character"] in team)]
        if len(possible) != 1:
            break  # Multiple owners or variants; a portrait cannot pick one.
        button = possible[0]
        slots = [str(i + 1) for i, name in enumerate(team) if name == button["character"]]
        if (len(slots) != 1 or slots[0] in disabled_slots
                or any(c.slot == slots[0] for c in result)
                or head.team_slot != int(slots[0]) or head.character != button["character"]):
            break
        profiles = tuple(p for p in store.profiles(button["character"], "link")
                         if p.skill_id == button["skill_id"])
        if len(profiles) != 1 or profiles[0].sp_cost != 0:
            break  # No guessed replacement profile or unsupported public-SP accounting.
        result.append(QueuedCombo(position, slots[0], button["character"], head.native_id,
                                  button["skill_id"], profiles))
    return tuple(result)


def same_combo_head(expected, observed):
    """Identity recheck only; disappearance is not a cast-success signal."""
    if (not isinstance(expected, ComboObservation) or not isinstance(observed, ComboObservation)
            or expected.team != observed.team or expected.frame_size != observed.frame_size
            or observed.ready is not True or observed.status != "observed"):
        return False
    a, b = expected.head, observed.head
    return (a is not None and b is not None
            and (a.position, a.character, a.native_id, a.team_slot)
            == (b.position, b.character, b.native_id, b.team_slot))
