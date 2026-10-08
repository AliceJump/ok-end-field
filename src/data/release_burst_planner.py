"""Price confirmed release bonuses in the live scheduler's finite burst window.

This deliberately uses ranked direct-damage quotes, not an incomplete native
program or inferred hit/target history. A ready button establishes an action
candidate; it does not establish a hit or additional private resources.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from src.data.character_skills import get_character
from src.data.damage_attributes import DamageAttributeBasis
from src.data.damage_modifiers import DamageBucket, DamageModifierSpec, ModifierMagnitude
from src.data.damage_release_rules import release_rules
from src.data.damage_resolution import DamageHit, FixedDamagePanel, TimedDamageState

BASELINE = Path(__file__).resolve().parents[2] / "assets/data/fixed_damage_baseline.json"
KIND = {"战技": "battle", "终结技": "ult", "普通攻击": "normal"}
EVENT = {"battle": "battle_cast", "ult": "ultimate_cast"}


def _cast_attack_rule(character, store, actor):
    """Reuse Akekuri's reviewed P3 block, not an inferred universal ATK proc."""
    from src.data.combat_simulation import CombatWorldState
    from src.data.native_action_program import compile_native_action
    from src.data.native_attribute_modifiers import AKEKURI_TEAM_ATTACK, reviewed_attack_binding

    if character.character_id != "akekuri":
        return None
    binding = reviewed_attack_binding(character, store)
    if binding is None:
        return None
    timing = store.profile(binding["producer"])
    program = compile_native_action(store, character, timing, actor, "ult")
    # Execute only the actual release instant to check its selected P3 gate and
    # living-team selector. Unknown later parts remain outside this contract.
    world = CombatWorldState((actor,))
    world.characters[actor].energy = program.energy_cost
    if not world.start(program, action_id="reviewed-release"):
        raise ValueError("Reviewed Akekuri release block no longer starts")
    world.advance(0)
    buffs = [b for b in world.native_buff_instances.values() if b.key == AKEKURI_TEAM_ATTACK]
    if len(buffs) != 1:
        raise ValueError("Reviewed Akekuri P3 gate/selector changed")
    instance = buffs[0]
    if (instance.action_finish_at != 5 or instance.attack_addition != binding["selected_parameter"]
            or instance.parent_scope is not None):
        raise ValueError("Reviewed Akekuri action window changed")
    return DamageModifierSpec(AKEKURI_TEAM_ATTACK, DamageBucket.ATTACK, ("all",), "team",
                              ModifierMagnitude(instance.attack_addition), "ultimate_cast", duration=5,
                              sources=(binding["sources"]["passive"], "native:release_block:0..150frames"))


@dataclass(frozen=True)
class ReleaseBurstAction:
    slot: str
    kind: str
    duration: float
    value: float
    sp_gate: float = 0.0
    sp_cost: float = 0.0


@dataclass(frozen=True)
class ReleaseBurstDecision:
    action: ReleaseBurstAction
    sequence: tuple[tuple[str, str], ...]
    damage: float
    gain: float


def _copy_state(state):
    copied = TimedDamageState(state.team)
    copied.modifiers = list(state.modifiers)
    copied.fields = dict(state.fields)
    copied.attribute_changes = list(state.attribute_changes)
    return copied


class ReleaseBurstPlanner:
    def __init__(self, team, *, baseline=BASELINE, store=None):
        actors = tuple(str(index + 1) for index in range(len(team)))
        self.team = tuple(team)
        self.state = TimedDamageState(actors)
        self.empty = TimedDamageState(actors)
        self.rules = {}
        self.panels = {}
        self.quotes = {}
        self.normal_durations = {}
        self.last_confirmed = {}
        rows = {row["character"]: row for row in json.loads(Path(baseline).read_text(encoding="utf8"))}
        self.deferred = []
        for actor, name in zip(actors, team, strict=True):
            row = rows.get(name)
            if row is None:
                continue
            panel = FixedDamagePanel(**row["panel"]["damage_basis"])
            if "attribute_basis" in row:
                panel = replace(panel, attribute_basis=DamageAttributeBasis.from_dict(row["attribute_basis"]))
            self.panels[actor] = panel
            profile = row["profile"]
            character = get_character(row["key"], skill_rank=profile["skill_rank"], potential=profile["potential"])
            for kind, specs in release_rules(character, row).items():
                if kind not in EVENT:
                    continue  # The existing anonymous combo path remains unchanged.
                enabled = []
                for spec in specs:
                    if (spec.recipient in {"self", "team", "other_allies"}
                            and spec.lifetime_scope == "timed" and spec.duration is not None
                            and not spec.condition_inputs and not spec.duration_input
                            and not spec.unresolved and spec.magnitude.evaluate({}) is not None):
                        enabled.append(spec)
                    else:
                        self.deferred.append((actor, spec.key))
                self.rules[actor, kind] = tuple(enabled)
            if store is not None:
                attack_rule = _cast_attack_rule(character, store, actor)
                if attack_rule is not None:
                    self.rules[actor, "ult"] = (*self.rules.get((actor, "ult"), ()), attack_rule)
            for skill in row["skills"]:
                kind = KIND.get(skill["type"])
                if kind is not None:
                    self.quotes[actor, kind] = skill
            if store is not None:
                # One basic normal-chain quote, with authored timing hints.
                # Not a measured hit count or an enhanced-stance multiplier.
                chain = []
                for index in range(1, 7):
                    try:
                        chain.append(store.profile(f"{character.progression.native_id}_attack{index}"))
                    except KeyError:
                        break
                if chain:
                    self.normal_durations[actor] = sum(max(p.actionable, .3) for p in chain)

    def preserve(self, previous):
        """Keep accepted timers when unknown portraits are filled in later."""
        unchanged = {str(i + 1) for i, name in enumerate(self.team)
                     if i < len(previous.team) and name == previous.team[i]}
        self.state.modifiers = [m for m in previous.state.modifiers
                                if m.source_actor in unchanged]
        self.state.attribute_changes = [c for c in previous.state.attribute_changes
                                        if c.actor in unchanged and c.source in unchanged]
        self.last_confirmed = {key: time for key, time in previous.last_confirmed.items() if key[0] in unchanged}

    def apply_release(self, state, actor, kind, now):
        # Akekuri's ATK exists only in its source action window; taking that
        # actor's next accepted action terminates the previous window early.
        state.remove("buff_chr_0019_karin_potential_3", actor)
        for spec in self.rules.get((actor, kind), ()):
            state.apply(spec, source_actor=actor, now=now, event=EVENT[kind])

    def confirm(self, actor, kind, started):
        if self.last_confirmed.get((actor, kind)) == started:
            return
        self.last_confirmed[actor, kind] = started
        self.apply_release(self.state, actor, kind, started)

    def price(self, actor, kind, now, *, value=None, state=None):
        quote = self.quotes.get((actor, kind))
        panel = self.panels.get(actor)
        if quote is None or panel is None:
            return None
        if value is None:
            value = quote.get("crit_expect", quote["non_crit"])
        basis = quote["quote_basis"]
        tags = tuple(basis["damage_tags"])
        hit = DamageHit(actor, "target", basis["element"], 1,
                        damage_bonus=quote["bonus_pct"] / 100,
                        damage_tag=tags[0], damage_tags=tags)
        before = self.empty.resolve_hit(panel, hit, now=now).expected
        after = (state or self.state).resolve_hit(panel, hit, now=now).expected
        if before is None or before <= 0 or after is None:
            return None
        return value * after / before

    def _search(self, actions, sp, now, *, apply_releases, state, horizon, width):
        # At most four ready ultimates, four currently legal battles and one
        # current main-control normal-chain quote, once each. No assumed future
        # energy, hit results, SP regeneration or extra casts.
        beam = [(0.0, now, sp, (), state)]
        best = beam[0]
        for _ in range(len(actions)):
            expanded = []
            for damage, time, budget, indices, damage_state in beam:
                for index, action in enumerate(actions):
                    if index in indices or budget < max(action.sp_gate, action.sp_cost):
                        continue
                    end = time + action.duration
                    if end > now + horizon:
                        continue
                    forecast = _copy_state(damage_state)
                    if apply_releases:
                        self.apply_release(forecast, action.slot, action.kind, time)
                    priced = self.price(action.slot, action.kind, end, value=action.value, state=forecast)
                    if priced is None:
                        continue
                    entry = (damage + priced, end, budget - action.sp_cost, (*indices, index), forecast)
                    expanded.append(entry)
                    if (entry[0], -entry[1]) > (best[0], -best[1]):
                        best = entry
            if not expanded:
                break
            expanded.sort(key=lambda entry: (-entry[0], entry[1], entry[3]))
            # Retain zero-direct-damage support starts until their beneficiaries
            # are priced. Pure accumulated-damage pruning would discard them.
            per_start = {}
            beam = []
            quota = max(1, width // len(actions))
            for entry in expanded:
                first = entry[3][0]
                if per_start.get(first, 0) < quota:
                    beam.append(entry)
                    per_start[first] = per_start.get(first, 0) + 1
        return best

    def choose(self, actions, sp, now, *, horizon=12.0, width=16):
        actions = tuple(actions)
        if (not actions or len(actions) > 9 or sum(a.kind == "normal" for a in actions) > 1
                or len({(a.slot, a.kind) for a in actions}) != len(actions)):
            return None
        self.state.expire(now)
        if not self.state.modifiers and not self.state.attribute_changes and not any(
            self.rules.get((a.slot, a.kind)) for a in actions
        ):
            return None
        best = self._search(actions, sp, now, apply_releases=True,
                            state=_copy_state(self.state), horizon=horizon, width=width)
        plain = self._search(actions, sp, now, apply_releases=False,
                             state=TimedDamageState(self.state.team), horizon=horizon, width=width)
        if not best[3] or best[0] <= plain[0] + 1e-6:
            return None
        if actions[best[3][0]].kind == "normal":
            return None  # Held normals provide a forecast tail, not a new input/lock.
        return ReleaseBurstDecision(actions[best[3][0]],
                                    tuple((actions[i].slot, actions[i].kind) for i in best[3]),
                                    best[0], best[0] - plain[0])
