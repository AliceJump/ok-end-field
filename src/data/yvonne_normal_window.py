"""Nominal held-normal window, separate from native execution and observed hits.

One stationary target, the reviewed primary continuation and uninterrupted held
input are forecast assumptions. Projectile times project emission frames; they
are not measured impacts. No buttons, hit recognition or engine tick replay.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass, replace
from functools import lru_cache

from src.data.damage_resolution import DamageHit
from src.data.native_action_program import _nodes
from src.data.native_gameplay import native_record

PREFIX = "chr_0017_yvonne_"
LAYER = "buff_" + PREFIX + "ultimate_skill_layer"
PRIMARY = ("1_1", "2_1", "2_2", "3_1", "3_2")


def _projectile_counts(sequence):
    """Keep alternatives exclusive. Only equal one-shot arms can be collapsed."""
    counts = {0}
    for node in sequence.get("actionData", ()):
        body = node["$value"]
        if not body.get("isEnable", True):
            continue
        kind = node["$type"].rsplit(".", 1)[-1]
        values = {0}
        if kind == "LaunchProjectile+Data":
            if body["projectileId"] not in {"projectile_" + PREFIX + "ult_attack1", "projectile_" + PREFIX + "ult_attack2"}:
                raise ValueError("Unreviewed Yvonne window projectile")
            if (not body["castSkillOnHit"] or not body["assignBlackboard"] or body["assignPairs"]
                    or body["projectileSkillId"] not in {PREFIX + "ult_attack1_projhit", PREFIX + "ult_attack2_projhit"}):
                raise ValueError("Changed Yvonne projectile damage inheritance")
            values = {1}
        elif kind == "IfElseAction+IfElseActionData":
            values = set()
            for arm in (body["succeedActions"], body["failActions"]):
                arm_counts = {0}
                for block in (arm,) if isinstance(arm, dict) else arm:
                    arm_counts = {a + b for a in arm_counts for b in _projectile_counts(block)}
                values.update(arm_counts)
        counts = {a + b for a in counts for b in values}
    return counts


@dataclass(frozen=True)
class WindowAttack:
    projected_at: float
    multiplier: float
    layers: int
    kind: str
    layer_expires_at: float | None = None


@dataclass(frozen=True)
class NormalWindowQuote:
    damage: float | None
    conditional_frozen_damage: float | None
    attacks: tuple[WindowAttack, ...]
    ends_at: float
    unknown: tuple[str, ...] = ()


@dataclass(frozen=True)
class YvonneNormalWindow:
    activation: float
    first_input: float
    duration: float
    final_delay: float
    shot_scale: float
    final_scale: float
    frozen_scale: float
    crit_per_layer: float
    full_crit_damage: float
    maximum: int
    stages: tuple[tuple[float, tuple[float, ...], tuple[float, ...]], ...]
    final_recovery: float
    frozen_talent_crit_damage: float
    layer_lifetime: float

    @classmethod
    def from_store(cls, store, rank):
        from src.data.character_skills import get_character

        def ranked(key):
            return {r["key"]: r["value"] for r in store.ranked_skill(key, rank)["blackboard"]}

        ultimate = ranked(PREFIX + "ultimate_skill")
        attack = ranked(PREFIX + "ult_attack1_1")
        for suffix in ("1", "2"):
            impact = native_record(store, PREFIX + f"ult_attack{suffix}_projhit")["data"]
            units = [u for node in _nodes(impact) if node["$type"].endswith("DamageAction+DamageActionData")
                     for u in node["$value"]["damageUnits"] if u["damageAttributeType"] == 0]
            if (len(units) != 1 or units[0]["damageType"] != 4 or units[0]["damageDecorateMask"] != 128
                    or units[0]["damageProcessors"] or not units[0]["atkScale"]["useBlackboardKey"]
                    or units[0]["atkScale"]["blackboardKey"] != "atk_scale"):
                raise ValueError("Changed Yvonne nominal projectile damage")
        stages = []
        for index, suffix in enumerate(PRIMARY):
            skill = PREFIX + "ult_attack" + suffix
            data = native_record(store, skill)["data"]
            if ranked(skill)["atk_scale"] != ultimate["atk_scale1"]:
                raise ValueError("Changed Yvonne stage-specific damage scale")
            next_id = PREFIX + "ult_attack" + PRIMARY[min(index + 1, len(PRIMARY) - 1)]
            profile = store.profile(skill)
            permission = [start for start, end, allowed in profile.allow_next if next_id in allowed and start < end]
            if not permission:
                raise ValueError("Missing primary Yvonne continuation")
            # One authored frame beyond the permission boundary, not an
            # assertion about actual input cadence or a new gameplay lock.
            cadence = min(permission) + 1 / 30
            shots, layers = [], []
            for block in data["actionGroupData"]["timelineActions"]:
                at = block["_startFrame"] / 30
                counts = _projectile_counts(block["_sequenceActionData"])
                if counts != {0}:
                    if counts != {1}:
                        raise ValueError("Yvonne projectile alternatives change shot count")
                    shots.append(at)
                for node in _nodes(block["_sequenceActionData"]):
                    if node["$type"].endswith("CreateBuffAction+Data") and node["$value"].get("isEnable", True):
                        if any(v["buffId"] == LAYER for v in node["$value"]["buffs"]):
                            layers.append(at)
            if not shots or max((*shots, *layers)) >= cadence:
                raise ValueError("Yvonne continuation would cancel an authored shot")
            stages.append((cadence, tuple(sorted(shots)), tuple(sorted(layers))))
        ultimate_data = native_record(store, PREFIX + "ultimate_skill")["data"]
        starts = [b["_startFrame"] / 30 for b in ultimate_data["actionGroupData"]["timelineActions"]
                  for node in _nodes(b["_sequenceActionData"]) if node["$type"].endswith("CreateBuffAction+Data")
                  and node["$value"].get("isEnable", True)
                  and any(r["buffId"] == "buff_" + PREFIX + "ultimate_skill" for r in node["$value"]["buffs"])]
        if not starts or len(set(starts)) != 1 or attack["layer"] != 10:
            raise ValueError("Changed Yvonne window activation/layers")
        # The authored switch/end/cached heavy is priced as a nominal held
        # input forecast. It is not a claim that native ComboCache is executed.
        activation = starts[0]
        talent = next(t for t in get_character("yvonne", skill_rank=rank, potential=0).progression.talents
                      if "status_up" in t.parameters and "inflict_up" in t.parameters)
        return cls(activation, store.profile(PREFIX + "ultimate_skill").actionable, ultimate["duration"],
                   store.profile(PREFIX + "ult_attack_end").effect_start, ultimate["atk_scale1"],
                   ultimate["atk_scale2"], ultimate["atk_scale_extra"], attack["normal_dmg_up"],
                   attack["crit_rate_up"] * attack["layer"], int(attack["layer"]), tuple(stages),
                   store.profile(PREFIX + "ult_attack_end").actionable, talent.parameters["status_up"],
                   next(v["valueDouble"] for v in native_record(store, LAYER)["data"]["blackboard"]
                        if v["key"] == "recycle_time"))

    @lru_cache(maxsize=128)
    def attacks(self, cast_at, *, input_at=None, initial_layers=0, initial_layers_until=None, stopped_at=None):
        if any(value is not None and not math.isfinite(value)
               for value in (cast_at, input_at, initial_layers_until, stopped_at)):
            raise ValueError("Invalid normal-window clock")
        if initial_layers != int(initial_layers) or not 0 <= initial_layers <= self.maximum:
            raise ValueError("Invalid projected initial crit layers")
        end = cast_at + self.activation + self.duration
        start = max(cast_at + self.first_input, input_at if input_at is not None else -math.inf)
        stop = min(end, stopped_at if stopped_at is not None else end)
        result, count, index = [], int(initial_layers), 0
        layer_expires = (initial_layers_until if initial_layers_until is not None else start + self.layer_lifetime) if count else None
        while start < stop:
            cadence, shots, layers = self.stages[index]
            for at in sorted(set(shots) | set(layers)):
                if start + at >= stop:
                    continue
                if layer_expires is not None and start + at >= layer_expires:
                    count = 0
                if at in layers:
                    count = min(self.maximum, count + layers.count(at))
                    layer_expires = start + at + self.layer_lifetime
                if at in shots:
                    result.append(WindowAttack(start + at, self.shot_scale, count, "shot", layer_expires))
            start += cadence
            index = min(index + 1, len(self.stages) - 1)
        if (stopped_at is None or stopped_at >= end + self.final_delay) and result:
            # The end attack has its own layer request at frame zero.
            count = min(self.maximum, (count if layer_expires is not None and end < layer_expires else 0) + 1)
            result.append(WindowAttack(end + self.final_delay, self.final_scale, count, "finisher", end + self.layer_lifetime))
        return tuple(result)

    def price(self, actor, panel, state, cast_at, *, from_time=None, input_at=None, until=None,
              initial_layers=0, initial_layers_until=None, frozen=False, stopped_at=None):
        attacks = self.attacks(cast_at, input_at=input_at, initial_layers=initial_layers,
                               initial_layers_until=initial_layers_until, stopped_at=stopped_at)
        damage = conditional = 0.0
        selected = []
        # Within one modifier/attribute phase, this resolver is linear in the
        # multiplier. Collapse equal-layer shots, retaining the explicit trace
        # for inspection, instead of resolving every shot in every beam node.
        cuts = sorted({value for row in (*state.modifiers, *state.attribute_changes, *state.fields.values())
                       for key in ("applied_at", "expires_at")
                       if (value := getattr(row, key, None)) is not None})
        groups = {}
        for attack in attacks:
            if from_time is not None and attack.projected_at < from_time or until is not None and attack.projected_at > until:
                continue
            selected.append(attack)
            key = (bisect_right(cuts, attack.projected_at), attack.layers, attack.kind)
            if key not in groups:
                groups[key] = attack
            else:
                groups[key] = replace(groups[key], multiplier=groups[key].multiplier + attack.multiplier)
        for attack in groups.values():
            adjusted = replace(panel, crit_rate=panel.crit_rate + attack.layers * self.crit_per_layer,
                               crit_damage=panel.crit_damage + (self.full_crit_damage if attack.layers == self.maximum else 0))
            hit = DamageHit(actor, "target", "寒冷", attack.multiplier, panel.bonus_for("寒冷", ("normal",)),
                            "normal", damage_tags=("normal",))
            priced = state.resolve_hit(adjusted, hit, now=attack.projected_at)
            if priced.expected is None:
                return NormalWindowQuote(None, None, tuple(selected), attack.projected_at, priced.unknown)
            damage += priced.expected
            if attack.kind == "finisher":
                # This condition is about the final hit only; it must not make
                # the target Frozen for every earlier shot in the window.
                frozen_panel = replace(adjusted, crit_damage=adjusted.crit_damage + self.frozen_talent_crit_damage)
                final = state.resolve_hit(frozen_panel, hit, now=attack.projected_at)
                extra = state.resolve_hit(frozen_panel, replace(hit, multiplier=self.frozen_scale), now=attack.projected_at)
                if extra.expected is None or final.expected is None:
                    return NormalWindowQuote(None, None, tuple(selected), attack.projected_at, extra.unknown)
                increment = final.expected + extra.expected - priced.expected
                conditional += increment
                if frozen is True:
                    damage += increment
        return NormalWindowQuote(damage, conditional, tuple(selected),
                                 selected[-1].projected_at if selected else (from_time or cast_at))
