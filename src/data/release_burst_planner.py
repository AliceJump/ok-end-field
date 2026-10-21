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
    same_actor_duration: float | None = None


@dataclass(frozen=True)
class ReleaseBurstDecision:
    action: ReleaseBurstAction
    sequence: tuple[tuple[str, str], ...]
    damage: float
    gain: float


@dataclass(frozen=True)
class _NormalProjection:
    cast_at: float
    input_at: float
    layers: int = 0
    layers_until: float | None = None


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
        self.window_models = {}
        self.window_casts = {}
        self.cast_seconds = {}
        from src.data.character_mechanics import load_main_control_policies

        self.control_policies = load_main_control_policies()
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
                for kind in EVENT:
                    timings = store.profiles(name, kind)
                    if timings:
                        self.cast_seconds[actor, kind] = max(p.actionable for p in timings)
                if row["key"] == "yvonne" and profile["potential"] == 0:
                    from src.data.yvonne_normal_window import YvonneNormalWindow

                    try:
                        self.window_models[actor] = YvonneNormalWindow.from_store(store, profile["skill_rank"])
                    except (KeyError, ValueError) as error:
                        self.deferred.append((actor, f"yvonne_normal_window: {error}"))
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
        self.window_casts = {actor: value for actor, value in previous.window_casts.items()
                             if actor in unchanged and actor in self.window_models}

    def _interrupt_normals(self, windows, started, resumed):
        for actor, projection in tuple(windows.items()):
            model = self.window_models[actor]
            previous = [a for a in model.attacks(projection.cast_at, input_at=projection.input_at,
                        initial_layers=projection.layers, initial_layers_until=projection.layers_until)
                        if a.kind == "shot" and a.projected_at < started]
            # Restart the nominal primary continuation after another command;
            # retain projected layers, never count attacks during its lock.
            count = previous[-1].layers if previous else projection.layers
            expires = previous[-1].layer_expires_at if previous else projection.layers_until
            if expires is not None and resumed >= expires:
                count, expires = 0, None
            windows[actor] = _NormalProjection(projection.cast_at, max(projection.input_at, resumed), count, expires)

    def normal_window_price(self, actor, now, *, until=None, state=None):
        projection = self.window_casts.get(actor)
        if projection is None:
            return None
        return self.window_models[actor].price(actor, self.panels[actor], _copy_state(state or self.state),
            projection.cast_at, input_at=projection.input_at, initial_layers=projection.layers,
            initial_layers_until=projection.layers_until, from_time=now, until=until)

    def interrupt_normals(self, started, resumed):
        """An accepted anonymous action blocks output without inventing its owner."""
        self._interrupt_normals(self.window_casts, started, resumed)

    def apply_release(self, state, actor, kind, now):
        # Akekuri's ATK exists only in its source action window; taking that
        # actor's next accepted action terminates the previous window early.
        state.remove("buff_chr_0019_karin_potential_3", actor)
        for spec in self.rules.get((actor, kind), ()):
            state.apply(spec, source_actor=actor, now=now, event=EVENT[kind])

    def confirm(self, actor, kind, started, *, recovered_at=None):
        if self.last_confirmed.get((actor, kind)) == started:
            return
        self.last_confirmed[actor, kind] = started
        self._expire_normal_windows(started)
        resumed = max(started + self.cast_seconds.get((actor, kind), 0), recovered_at or started)
        self._interrupt_normals(self.window_casts, started, resumed)
        if kind == "ult" and actor in self.window_models:
            self.window_casts[actor] = _NormalProjection(started, max(resumed,
                                                        started + self.window_models[actor].first_input))
        self.apply_release(self.state, actor, kind, started)

    def _expire_normal_windows(self, now):
        self.window_casts = {actor: projection for actor, projection in self.window_casts.items()
                            if now <= projection.cast_at + self.window_models[actor].activation
                            + self.window_models[actor].duration + self.window_models[actor].final_delay}

    def price(self, actor, kind, now, *, value=None, state=None):
        if kind == "ult" and actor in self.window_models:
            return 0.0  # The old row-sum quote is not instantaneous ultimate damage.
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

    def _ordinary_tail(self, actor, started, end, state):
        """Existing ordinary-chain DPS over the same comparison horizon."""
        seconds = self.normal_durations.get(actor)
        if seconds is None or seconds <= 0 or started >= end:
            return 0.0
        boundaries = sorted({started, end, *(m.expires_at for m in (*state.modifiers, *state.attribute_changes)
                            if m.expires_at is not None and started < m.expires_at < end)})
        copied = _copy_state(state)
        result = 0.0
        for left, right in zip(boundaries, boundaries[1:]):
            value = self.price(actor, "normal", (left + right) / 2, state=copied)
            if value is None:
                return None
            result += value * (right - left) / seconds
        return result

    def _search(self, actions, sp, now, *, apply_releases, state, horizon, width, main=None, normal_tail=False):
        # At most four ready ultimates, four currently legal battles and one
        # current main-control normal-chain quote, once each. No assumed future
        # energy, hit results, SP regeneration or extra casts.
        windows = dict(self.window_casts) if apply_releases else {}
        beam = [(0.0, now, sp, (), state, {}, windows, main)]

        def terminal(entry):
            damage, time, budget, indices, damage_state, locks, windows, control = entry
            projection = windows.get(control)
            if projection is None:
                if normal_tail:
                    ordinary = self._ordinary_tail(control, time, now + horizon, damage_state)
                    return (damage + ordinary if ordinary is not None else -float("inf"), *entry[1:])
                return entry
            quote = self.window_models[control].price(control, self.panels[control], _copy_state(damage_state),
                projection.cast_at, input_at=projection.input_at, initial_layers=projection.layers,
                initial_layers_until=projection.layers_until, from_time=time, until=now + horizon)
            if quote.damage is None:
                return (-float("inf"), *entry[1:])
            resume = projection.cast_at + self.window_models[control].activation + self.window_models[control].duration
            resume += self.window_models[control].final_recovery
            ordinary = self._ordinary_tail(control, max(time, resume), now + horizon, damage_state)
            return (damage + quote.damage + ordinary if ordinary is not None else -float("inf"),
                    max(time, quote.ends_at), *entry[2:])

        best = terminal(beam[0])
        for _ in range(len(actions)):
            expanded = []
            for damage, time, budget, indices, damage_state, actor_locks, windows, control in beam:
                for index, action in enumerate(actions):
                    if index in indices or budget < max(action.sp_gate, action.sp_cost):
                        continue
                    if action.kind == "normal" and (normal_tail or action.slot in windows):
                        continue  # One held-input tail; do not add the ordinary chain again.
                    started = max(time, actor_locks.get(action.slot, now))
                    end = started + action.duration
                    if end > now + horizon:
                        continue
                    forecast = _copy_state(damage_state)
                    projected = dict(windows)
                    next_control = control
                    if apply_releases:
                        self.apply_release(forecast, action.slot, action.kind, started)
                        if action.kind != "normal":
                            self._interrupt_normals(projected, started, end)
                        if action.kind == "ult" and action.slot in self.window_models:
                            projected[action.slot] = _NormalProjection(started, max(end,
                                started + self.window_models[action.slot].first_input))
                            next_control = action.slot  # Reviewed ultimate switches to its owner.
                        else:
                            policy = self.control_policies.get((self.team[int(action.slot) - 1], action.kind))
                            if policy is not None and policy.mode.value == "prefer" and policy.switches_automatically:
                                next_control = action.slot
                    priced = self.price(action.slot, action.kind, end, value=action.value, state=forecast)
                    if priced is None:
                        continue
                    locks = dict(actor_locks)
                    locks[action.slot] = started + max(action.duration, action.same_actor_duration or 0)
                    entry = (damage + priced, end, min(300.0, budget - action.sp_cost), (*indices, index), forecast,
                             locks, projected, next_control)
                    total = terminal(entry)
                    expanded.append((total, entry))
                    if (total[0], -total[1]) > (best[0], -best[1]):
                        best = total
            if not expanded:
                break
            expanded.sort(key=lambda pair: (-pair[0][0], pair[0][1], pair[0][3]))
            # Retain zero-direct-damage support starts until their beneficiaries
            # are priced. Pure accumulated-damage pruning would discard them.
            per_start = {}
            beam = []
            quota = max(1, width // len(actions))
            for _, entry in expanded:
                first = entry[3][0]
                if per_start.get(first, 0) < quota:
                    beam.append(entry)
                    per_start[first] = per_start.get(first, 0) + 1
        return best

    def choose(self, actions, sp, now, *, horizon=12.0, width=16, main=None):
        actions = tuple(actions)
        if (len(actions) > 9 or sum(a.kind == "normal" for a in actions) > 1
                or len({(a.slot, a.kind) for a in actions}) != len(actions)):
            return None
        self.state.expire(now)
        self._expire_normal_windows(now)
        window_candidate = main in self.window_casts or any(a.slot in self.window_models and a.kind == "ult" for a in actions)
        if not window_candidate and not self.state.modifiers and not self.state.attribute_changes and not any(
            self.rules.get((a.slot, a.kind)) for a in actions
        ):
            return None
        best = self._search(actions, sp, now, apply_releases=True,
                            state=_copy_state(self.state), horizon=horizon, width=width, main=main,
                            normal_tail=window_candidate and main is not None)
        plain = self._search(actions, sp, now, apply_releases=False,
                             state=_copy_state(self.state) if window_candidate else TimedDamageState(self.state.team),
                             horizon=horizon, width=width, main=main,
                             normal_tail=window_candidate and main is not None)
        if best[0] <= plain[0] + 1e-6:
            if window_candidate and main is not None and not best[3] and best[0] > 0:
                return ReleaseBurstDecision(ReleaseBurstAction(main, "normal_window", 0, best[0]),
                                            ((main, "normal"),), best[0], 0)
            return None
        if best[3] and actions[best[3][0]].kind == "normal":
            return None  # Held normals provide a forecast tail, not a new input/lock.
        if not best[3] and best[7] not in best[6] and not (window_candidate and main is not None):
            return None
        selected = actions[best[3][0]] if best[3] else ReleaseBurstAction(best[7], "normal_window", 0, best[0])
        sequence = tuple((actions[i].slot, actions[i].kind) for i in best[3])
        if best[7] in best[6]:
            sequence += ((best[7], "normal_window"),)
        return ReleaseBurstDecision(selected, sequence,
                                    best[0], best[0] - plain[0])
