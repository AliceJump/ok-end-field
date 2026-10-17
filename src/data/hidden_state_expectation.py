"""Expected-value model for hidden combat resources.

Unobservable state is represented as a probability distribution, never as an
assumed full stack.  When no evidence exists for a binary prerequisite, the
model uses a maximum-entropy prior of 0.5.  Stack resources with known bounds
start at a known battle-entry value (normally zero) and are updated by observed
actions.  Range-valued gains use a uniform distribution over the supported
range unless later evidence narrows them.
"""

from __future__ import annotations

import contextlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from src.data.character_capabilities import load_character_capabilities

_ROOT = Path(__file__).resolve().parent.parent.parent
_BASELINE_FILE = _ROOT / "assets" / "data" / "damage_baseline.json"

_ELEMENT_EFFECT = {
    "寒冷": "ATTACH_COLD",
    "灼热": "ATTACH_BURN",
    "电磁": "ATTACH_ELECTROMAGNETIC",
    "自然": "ATTACH_NATURAL",
}


@dataclass(frozen=True)
class DiscreteBelief:
    probabilities: tuple[float, ...]

    def __post_init__(self):
        if not self.probabilities:
            raise ValueError("empty belief")
        if any(value < 0 or not math.isfinite(value) for value in self.probabilities):
            raise ValueError("invalid belief probability")
        total = sum(self.probabilities)
        if total <= 0:
            raise ValueError("zero belief mass")
        normalized = tuple(value / total for value in self.probabilities)
        object.__setattr__(self, "probabilities", normalized)

    @property
    def maximum(self) -> int:
        return len(self.probabilities) - 1

    @property
    def expected(self) -> float:
        return sum(index * value for index, value in enumerate(self.probabilities))

    @property
    def full_probability(self) -> float:
        return self.probabilities[-1]

    def probability_at_least(self, level: int) -> float:
        level = max(0, min(self.maximum, int(level)))
        return sum(self.probabilities[level:])

    @classmethod
    def certain(cls, level: int, maximum: int):
        maximum = max(0, int(maximum))
        level = max(0, min(maximum, int(level)))
        values = [0.0] * (maximum + 1)
        values[level] = 1.0
        return cls(tuple(values))

    @classmethod
    def maximum_entropy(cls, maximum: int):
        maximum = max(0, int(maximum))
        value = 1.0 / (maximum + 1)
        return cls(tuple(value for _ in range(maximum + 1)))

    def add(self, amount: int, success_probability: float = 1.0):
        amount = max(0, int(amount))
        p = max(0.0, min(1.0, float(success_probability)))
        out = [0.0] * len(self.probabilities)
        for level, mass in enumerate(self.probabilities):
            out[level] += mass * (1.0 - p)
            out[min(self.maximum, level + amount)] += mass * p
        return DiscreteBelief(tuple(out))

    def add_uniform(self, minimum: int, maximum: int, success_probability: float = 1.0):
        minimum = max(0, int(minimum))
        maximum = max(minimum, int(maximum))
        p = max(0.0, min(1.0, float(success_probability)))
        width = maximum - minimum + 1
        out = [0.0] * len(self.probabilities)
        for level, mass in enumerate(self.probabilities):
            out[level] += mass * (1.0 - p)
            for gain in range(minimum, maximum + 1):
                out[min(self.maximum, level + gain)] += mass * p / width
        return DiscreteBelief(tuple(out))

    def consume_all(self, probability: float = 1.0):
        p = max(0.0, min(1.0, float(probability)))
        out = [value * (1.0 - p) for value in self.probabilities]
        out[0] += p
        return DiscreteBelief(tuple(out))


@dataclass(frozen=True)
class DamageEnvelope:
    actor: str
    kind: str
    low: float
    high: float
    source: str
    stack_resource: str | None = None
    stack_maximum: int | None = None

    def expected(self, full_probability: float) -> float:
        p = max(0.0, min(1.0, float(full_probability)))
        return self.low + (self.high - self.low) * p


@dataclass(frozen=True)
class ExpectedDamage:
    low: float
    expected: float
    high: float
    full_probability: float
    expected_fraction: float
    basis: str


def load_damage_envelopes(path: Path | None = None) -> dict[tuple[str, str], DamageEnvelope]:
    p = path or _BASELINE_FILE
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(rows, list):
        return {}

    result = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        actor = str(row.get("character") or "")
        for skill in row.get("skills") or ():
            if not isinstance(skill, dict):
                continue
            kind = skill.get("type")
            if kind not in {"战技", "连携技", "终结技"}:
                continue
            try:
                low = float(skill.get("crit_expect", skill.get("non_crit", 0)) or 0)
                high = float(skill.get("full_expect", low) or low)
            except (TypeError, ValueError):
                continue
            if not (math.isfinite(low) and math.isfinite(high)):
                continue
            high = max(low, high)
            stack_resource = None
            stack_maximum = None

            # Some skills encode a linear per-stack damage term without a
            # precomputed full_expect.  Spell attachment has a documented
            # global cap of 4 in the project's reviewed combat-system data.
            # Derive the full endpoint from the actual multiplier row instead
            # of silently treating the base snapshot as "full".
            conditional_rows = tuple(skill.get("conditional_rows") or ())
            for item in conditional_rows:
                match = re.search(r"消耗每层附着\s*/\s*额外伤害倍率:\s*([0-9.]+)%", str(item))
                if not match:
                    continue
                try:
                    base_multiplier = float(skill.get("multiplier_pct") or 0)
                    per_stack = float(match.group(1))
                except (TypeError, ValueError):
                    continue
                if base_multiplier > 0 and per_stack > 0:
                    stack_resource = "spell_attach"
                    stack_maximum = 4
                    high = max(high, low * (base_multiplier + per_stack * stack_maximum) / base_multiplier)
                    break

            result[(actor, kind)] = DamageEnvelope(
                actor,
                kind,
                max(0.0, low),
                max(0.0, high),
                "damage_baseline",
                stack_resource,
                stack_maximum,
            )
    return result


class HiddenStateExpectation:
    """Belief state for hidden stacks and binary enemy/team prerequisites."""

    UNKNOWN_BINARY = 0.5

    _RESOURCE_EFFECTS: ClassVar[dict[str, str]] = {
        "STACK_SIGN": "insight",
        "STACK_HUNTING_ARROW": "hunting_arrow",
        "STACK_QINGTING_SWORD": "qingting_sword",
    }
    _STATUS_RESOURCES: ClassVar[dict[str, str]] = {
        "STATUS_CONDUCTING": "conducting",
    }

    def __init__(self, team: list[str], mechanics: dict, capabilities: dict | None = None):
        self.team = tuple(team)
        self.mechanics = mechanics
        self.resources: dict[tuple[str, str], DiscreteBelief] = {}
        self.conditions: dict[str, float] = {}
        self.attachments: dict[str, DiscreteBelief] = {}
        self.spell_attach = DiscreteBelief.certain(0, 4)

        # Team composition is evidence too. If nobody in the detected team can
        # produce an element, its attachment probability starts at zero rather
        # than the generic 0.5 unknown prior. If at least one provider exists
        # but the current stack is not observable, keep the maximum-entropy 0.5.
        capabilities = capabilities if capabilities is not None else load_character_capabilities()
        any_attachment_provider = False
        for element, effect in _ELEMENT_EFFECT.items():
            providers = [
                actor
                for actor in team
                if actor != "?"
                and capabilities.get(actor) is not None
                and element in capabilities[actor].attach_elements
            ]
            if providers:
                any_attachment_provider = True
                self.attachments[effect] = DiscreteBelief.maximum_entropy(4)
            else:
                self.attachments[effect] = DiscreteBelief.certain(0, 4)
            self.conditions[effect] = self.attachments[effect].probability_at_least(1)

        if any_attachment_provider:
            self.spell_attach = DiscreteBelief.maximum_entropy(4)

        for index, actor in enumerate(team, 1):
            mechanic = mechanics.get(actor)
            if mechanic is None:
                continue
            slot = str(index)
            for resource in mechanic.resources:
                if resource.maximum is not None:
                    self.resources[(slot, resource.key)] = DiscreteBelief.certain(0, resource.maximum)

    def condition_probability(self, condition: str) -> float:
        condition = str(condition or "")
        if condition in self.attachments:
            return self.attachments[condition].probability_at_least(1)
        resource_key = self._STATUS_RESOURCES.get(condition)
        if resource_key is not None:
            matches = [belief for (_slot, key), belief in self.resources.items() if key == resource_key]
            if matches:
                return max(belief.probability_at_least(1) for belief in matches)
        if "|" in condition:
            parts = [self.condition_probability(part) for part in condition.split("|") if part]
            probability_none = 1.0
            for value in parts:
                probability_none *= 1.0 - value
            return 1.0 - probability_none if parts else self.UNKNOWN_BINARY

        if ">=" in condition:
            key, raw = condition.split(">=", 1)
            try:
                threshold = int(float(raw))
            except ValueError:
                return self.UNKNOWN_BINARY
            for (_slot, resource_key), belief in self.resources.items():
                if resource_key == key:
                    return belief.probability_at_least(threshold)
            return self.UNKNOWN_BINARY

        if ":" in condition and condition.startswith("STACK_"):
            effect, raw = condition.split(":", 1)
            resource_key = self._RESOURCE_EFFECTS.get(effect)
            if resource_key is not None:
                try:
                    threshold = int(float(raw))
                except ValueError:
                    return self.UNKNOWN_BINARY
                matches = [belief for (slot, key), belief in self.resources.items() if key == resource_key]
                if matches:
                    return max(belief.probability_at_least(threshold) for belief in matches)

        return self.conditions.get(condition, self.UNKNOWN_BINARY)

    def requirements_probability(self, requirements: tuple[str, ...]) -> float:
        if not requirements:
            return 1.0
        probability = 1.0
        for requirement in requirements:
            if requirement.startswith("prefer:"):
                continue
            probability *= self.condition_probability(requirement)
        return probability

    def set_condition(self, condition: str, probability: float):
        self.conditions[condition] = max(0.0, min(1.0, float(probability)))

    def _resource(self, slot: str, effect: str):
        key = self._RESOURCE_EFFECTS.get(effect)
        if key is None:
            return None, None
        return key, self.resources.get((slot, key))

    def apply_tokens(
        self,
        slot: str,
        *,
        requires: tuple[str, ...] = (),
        consumes: tuple[str, ...] = (),
        produces: tuple[str, ...] = (),
    ):
        success = self.requirements_probability(requires)

        for token in consumes:
            if token.endswith(":all") or token.endswith(":all_if_present") or token.endswith(":all_on_attack"):
                effect = token.split(":", 1)[0]
                key, belief = self._resource(slot, effect)
                if key is not None and belief is not None:
                    self.resources[(slot, key)] = belief.consume_all(success)
                elif effect.startswith("ATTACH_") or effect.startswith("STATUS_"):
                    self.set_condition(effect, self.condition_probability(effect) * (1.0 - success))
                continue
            if token.startswith("spell_attach:all"):
                # Element is intentionally unknown; consume the union of spell attachments.
                self.spell_attach = self.spell_attach.consume_all(success)
                for effect, belief in tuple(self.attachments.items()):
                    self.attachments[effect] = belief.consume_all(success)
                    self.conditions[effect] = self.attachments[effect].probability_at_least(1)

        for token in produces:
            if token.startswith("STACK_") and ":" in token:
                effect, raw = token.split(":", 1)
                key, belief = self._resource(slot, effect)
                if key is None or belief is None:
                    continue
                if ".." in raw:
                    low, high = raw.split("..", 1)
                    with contextlib.suppress(ValueError):
                        self.resources[(slot, key)] = belief.add_uniform(int(float(low)), int(float(high)), success)
                else:
                    with contextlib.suppress(ValueError):
                        self.resources[(slot, key)] = belief.add(int(float(raw)), success)
                continue
            if token.startswith("STATUS_CONDUCTING:"):
                _effect, raw = token.split(":", 1)
                matches = [
                    (resource_key, belief)
                    for (resource_slot, resource_key), belief in self.resources.items()
                    if resource_slot == slot and resource_key == "conducting"
                ]
                if matches:
                    resource_key, belief = matches[0]
                    if raw == "+1_or_apply":
                        self.resources[(slot, resource_key)] = belief.add(1, success)
                    else:
                        with contextlib.suppress(ValueError):
                            self.resources[(slot, resource_key)] = belief.add(int(float(raw)), success)
                    self.conditions["STATUS_CONDUCTING"] = self.resources[(slot, resource_key)].probability_at_least(1)
                continue
            if token.startswith("ATTACH_"):
                belief = self.attachments.get(token, DiscreteBelief.certain(0, 4))
                self.attachments[token] = belief.add(1, success)
                self.spell_attach = self.spell_attach.add(1, success)
                self.conditions[token] = self.attachments[token].probability_at_least(1)
                continue
            if token.startswith(("STATUS_", "VULN_")):
                self.set_condition(token, max(self.condition_probability(token), success))
                continue
            if token == "ult_state":
                self.set_condition("ult_state", max(self.condition_probability("ult_state"), success))
                self.set_condition(
                    "first_battle_in_ult",
                    max(
                        self.condition_probability("first_battle_in_ult"),
                        success,
                    ),
                )

    def apply_action(self, action):
        self.apply_tokens(
            action.slot,
            requires=tuple(action.requires),
            consumes=tuple(action.consumes),
            produces=tuple(action.produces),
        )
        if "first_battle_in_ult" in tuple(action.requires):
            self.set_condition("first_battle_in_ult", 0.0)

    def _full_probability_for(self, actor: str, kind: str, transition=None) -> tuple[float, str]:
        # Prefer explicit transition requirements when they describe the
        # enhancement condition. This automatically improves as prior actions
        # add/remove beliefs.
        if transition is not None and transition.requires:
            p = self.requirements_probability(tuple(transition.requires))
            return p, "transition_requirements"

        # Existing baseline only annotates the attachment dependency for some
        # full-caliber skills. With no runtime evidence, use maximum entropy.
        mechanic = self.mechanics.get(actor)
        if mechanic is not None:
            if mechanic.archetype == "main_control_attack_channel" and kind == "战技":
                belief = self.attachments.get("ATTACH_NATURAL")
                if belief is not None and belief.maximum > 0:
                    return belief.expected / belief.maximum, "expected_natural_attach_fraction"
                return 0.0, "no_natural_attachment"
            if mechanic.archetype == "consume_status_build_stack_burst" and kind == "战技":
                matches = [belief for (_slot, key), belief in self.resources.items() if key == "conducting"]
                if matches and matches[0].maximum > 0:
                    return matches[0].expected / matches[0].maximum, "expected_conducting_fraction"
                return self.condition_probability("STATUS_CONDUCTING"), "conducting_belief"

        return self.UNKNOWN_BINARY, "maximum_entropy_binary"

    def estimate_damage(
        self,
        actor: str,
        kind: str,
        envelope: DamageEnvelope | None,
        transition=None,
    ) -> ExpectedDamage | None:
        if envelope is None:
            return None
        if envelope.high <= envelope.low:
            return ExpectedDamage(
                envelope.low,
                envelope.low,
                envelope.high,
                1.0,
                1.0,
                "single_caliber",
            )

        if (
            transition is not None
            and getattr(transition, "phase", "") == "天理合真首次惊霆诀"
            and actor == "庄方宜"
            and kind == "战技"
        ):
            return ExpectedDamage(
                envelope.low,
                envelope.high,
                envelope.high,
                1.0,
                1.0,
                "guaranteed_3_swords_in_first_ult_battle",
            )

        if envelope.stack_resource == "spell_attach" and envelope.stack_maximum:
            expected_fraction = self.spell_attach.expected / envelope.stack_maximum
            return ExpectedDamage(
                envelope.low,
                envelope.expected(expected_fraction),
                envelope.high,
                self.spell_attach.full_probability,
                expected_fraction,
                f"spell_attach_expectation={self.spell_attach.expected:.2f}/{envelope.stack_maximum}",
            )

        fraction, basis = self._full_probability_for(actor, kind, transition)
        full_probability = fraction
        if "expected_" in basis and (mechanic := self.mechanics.get(actor)):
            if mechanic.archetype == "main_control_attack_channel" and kind == "战技":
                belief = self.attachments.get("ATTACH_NATURAL")
                if belief is not None:
                    full_probability = belief.full_probability
            elif mechanic.archetype == "consume_status_build_stack_burst" and kind == "战技":
                matches = [belief for (_slot, key), belief in self.resources.items() if key == "conducting"]
                if matches:
                    full_probability = matches[0].full_probability
        return ExpectedDamage(
            envelope.low,
            envelope.expected(fraction),
            envelope.high,
            full_probability,
            fraction,
            basis,
        )


def element_condition(element: str) -> str | None:
    return _ELEMENT_EFFECT.get(element)
