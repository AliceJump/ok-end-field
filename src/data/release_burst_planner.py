"""Release-certain buffs priced over the existing scheduler's burst candidates.

No native world, hit inference or resource confirmation lives
here. Fixed panels and resolved rules are exported from a pinned review revision.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

from src.data.battle_conditions import (
    BattleConditionAssessment,
    ConditionRequirement,
    ConditionTransition,
    ForecastConditions,
)
from src.data.highlight_damage import HighlightDamageBinding, HighlightDamageComponent
from src.data.mechanism_prediction import (
    MechanismQuote,
    MechanismState,
    after_action,
    branch_quote,
    validate_contract,
)

SNAPSHOT = Path(__file__).resolve().parents[2] / "assets/data/release_burst"
ELEMENTS = {"all", "物理", "寒冷", "灼热", "电磁", "自然"}
TAGS = {"normal", "skill", "combo", "ultimate"}


def _validate_stance(row):
    stance = row.get("stance")
    if stance is None:
        return
    if (row["key"] != "zhuang_fangyi" or stance["trigger"] != "ult"
            or stance["free_battle_uses"] != 1 or set(stance["quotes"]) != {"normal", "battle"}
            or not stance["source_evidence"]["native_record_sha256"]
            or any(not math.isfinite(stance[k]) or stance[k] <= 0 for k in
                   ("duration", "normal_duration", "battle_handoff", "battle_actionable"))
            or not math.isfinite(stance["starts_after"]) or stance["starts_after"] < 0):
        raise ValueError("Unconfirmed release stance")


def _validate_quote(quote):
    if (quote["quote_basis"]["element"] not in ELEMENTS - {"all"}
            or not set(quote["quote_basis"]["damage_tags"]) <= TAGS
            or not all(math.isfinite(quote[key]) for key in ("non_crit", "crit_expect", "bonus_pct"))):
        raise ValueError("Invalid fixed quote")


def _validate_rule(spec):
    magnitude = spec["magnitude"]
    if (spec["kind"] not in {"battle", "ult"} or spec["trigger"] != {
            "battle": "battle_cast", "ult": "ultimate_cast"}[spec["kind"]]
            or spec["recipient"] not in {"self", "team", "other_allies"}
            or spec["bucket"] not in {"attack", "damage_bonus", "amplification"}
            or not spec["elements"] or not set(spec["elements"]) <= ELEMENTS
            or not set(spec["damage_tags"]) <= TAGS or not spec["sources"]
            or magnitude["terms"] or magnitude["by_count"] or magnitude["count_input"]
            or magnitude["accumulation"] != "linear" or not math.isfinite(magnitude["base"])
            or spec["unresolved"] or spec["condition_inputs"] or spec["duration_input"]
            or spec["permanent"] or spec["lifetime_scope"] != "timed"
            or spec["evaluation"] != "application" or spec["max_stacks"] != 1
            or spec["stack_policy"] != "replace" or spec["duration"] is None
            or not math.isfinite(spec["duration"]) or spec["duration"] <= 0):
        raise ValueError("Unconfirmed release rule cannot enter burst pricing")
    if (not math.isfinite(spec["starts_after"]) or spec["starts_after"] < 0
            or not set(spec["blocks_source_actions"]) <= {"battle", "normal"}
            or not set(spec.get("preserves_source_actions", ())) <= {"link"}):
        raise ValueError("Invalid release window")


def _validate_actor(row):
    for key in ("attack_white", "attack_percent", "attack_flat", "attribute_factor"):
        if not math.isfinite(row["panel"][key]):
            raise ValueError("Invalid fixed attack basis")
    _validate_stance(row)
    validate_contract(row.get('mechanism'), actor=row['key'])
    quotes = list(row["quotes"].values())
    if row.get("stance") is not None:
        quotes.extend(row["stance"]["quotes"].values())
    for quote in quotes:
        _validate_quote(quote)
    for spec in row["rules"]:
        _validate_rule(spec)


@lru_cache(maxsize=4)
def read_snapshot(path=SNAPSHOT):
    path = Path(path)
    manifest = json.loads((path / "index.json").read_text(encoding="utf8"))
    payload = (path / "snapshot.json").read_bytes()
    data = json.loads(payload)
    if (manifest["schema_version"] != 1 or data["schema_version"] != 1
            or data["scope"] != "confirmed_release_bonuses"
            or hashlib.sha256(payload).hexdigest() != manifest["snapshot_sha256"]
            or len(manifest["source_revision"]) != 40 or not manifest["source_hashes"]):
        raise ValueError("Release snapshot provenance/hash mismatch")
    if hashlib.sha256((path / "support-evidence.json").read_bytes()).hexdigest() != manifest["support_evidence_sha256"]:
        raise ValueError("Support evidence hash mismatch")
    actors = data["actors"]
    if (len(actors) != manifest["actors"] or len({r["character"] for r in actors}) != len(actors)
            or sum(len(r["rules"]) for r in actors) != manifest["rules"]
            or sum(r.get("stance") is not None for r in actors) != manifest["stances"]):
        raise ValueError("Release snapshot selection mismatch")
    for row in actors:
        _validate_actor(row)
    return {row["character"]: row for row in actors}


@dataclass(frozen=True)
class ReleaseBurstAction:
    slot: str
    kind: str
    duration: float
    value: float
    sp_gate: float = 0
    sp_cost: float = 0
    same_actor_duration: float | None = None
    observed_bonus: float = 0
    base_components: tuple[HighlightDamageComponent, ...] = ()
    observed_components: tuple[HighlightDamageComponent, ...] = ()
    condition: ConditionRequirement | None = None
    queue_position: int = 0
    damage_duration: float | None = None
    mechanism_assessment: BattleConditionAssessment | None = None


@dataclass(frozen=True)
class BurstSpValue:
    direct_damage: float
    followup_contribution: float
    sp_cost: float
    damage_per_sp: float | None


@dataclass(frozen=True)
class ReleaseBurstDecision:
    action: ReleaseBurstAction
    sequence: tuple[tuple[str, str], ...]
    damage: float
    gain: float
    mechanism: MechanismQuote | None = None
    sp_value: BurstSpValue | None = None


@dataclass(frozen=True)
class ActiveReleaseBonus:
    spec: dict
    source: str
    recipient: str
    expires_at: float
    starts_at: float


@dataclass(frozen=True)
class ActiveReleaseStance:
    starts_at: float
    expires_at: float
    free_battle: bool = True


class ReleaseBurstPlanner:
    def __init__(self, team, *, store=None, snapshot=SNAPSHOT):
        self.team = tuple(team)
        self.rows = {str(i + 1): read_snapshot(snapshot)[name] for i, name in enumerate(team)
                     if name in read_snapshot(snapshot)}
        self.rules = {(actor, kind): tuple(s for s in row["rules"] if s["kind"] == kind)
                      for actor, row in self.rows.items() for kind in ("battle", "ult")}
        self.bonuses = []
        self.stances = {}
        self.last_confirmed = {}
        self.mechanism_state = MechanismState()
        self.mechanism_assessments = {}
        if store is not None:
            for row in self.rows.values():
                for skill, digest in (row.get('mechanism') or {}).get('native_hashes', {}).items():
                    if store.record(skill)['source']['sha256'] != digest:
                        raise ValueError('Unreviewed live mechanism record')
        self.normal_durations = {}
        self.normal_entries = {}
        self.stance_normal_entries = {}
        if store is not None:
            self._load_normal_timings(store)
        self.highlight_damage = {actor: binding for actor, row in self.rows.items()
                                 if (binding := HighlightDamageBinding.for_actor(row, store)) is not None}
        self.condition_transitions = {(actor, kind): transition for actor, row in self.rows.items()
                                      for kind, transition in ConditionTransition.for_actor(row, store).items()}

    @staticmethod
    def _basic_normal_profiles(store, character):
        # Ambiguous ultimate variants do not establish a basic normal chain.
        native_ids = {p.skill_id.split("_ultimate")[0] for p in store.profiles(character, "ult")}
        if len(native_ids) != 1:
            return []
        native_id = native_ids.pop()
        profiles = []
        for index in range(1, 7):
            try:
                profiles.append(store.profile(f"{native_id}_attack{index}"))
            except KeyError:
                break
        return profiles

    def _load_normal_timings(self, store):
        for actor, row in self.rows.items():
            profiles = self._basic_normal_profiles(store, row["character"])
            if not profiles:
                continue
            self.normal_durations[actor] = sum(max(p.actionable, .3) for p in profiles)
            self.normal_entries[actor] = (profiles[0],)
            spec = row.get("stance")
            if spec is not None:
                self.stance_normal_entries[actor] = (store.profile(spec["normal_profiles"][0]),)

    def preserve(self, previous):
        unchanged = {str(i + 1) for i, name in enumerate(self.team)
                     if i < len(previous.team) and name == previous.team[i]}
        self.bonuses = [b for b in previous.bonuses if b.source in unchanged]
        self.last_confirmed = {key: time for key, time in previous.last_confirmed.items() if key[0] in unchanged}
        self.stances = {actor: stance for actor, stance in previous.stances.items() if actor in unchanged}
        self.mechanism_state = previous.mechanism_state.keep(unchanged)

    def observe_conditions(self, assessments):
        self.mechanism_assessments = dict(assessments)
        for owner, row in self.rows.items():
            spec = row.get('mechanism')
            if spec is not None:
                self.mechanism_state = self.mechanism_state.observe(owner, assessments.get(owner), spec)

    def mechanism_quote(self, actor, now, assessment=None, *, state=None, stances=None, forecast=False):
        row = self.rows.get(actor)
        if row is None:
            return None
        if assessment is None and not forecast:
            assessment = self.mechanism_assessments.get(actor)
        return branch_quote(row, actor, now, assessment, self.mechanism_state if state is None else state,
                            stance=self.stance_at(actor, now, stances=stances), forecast=forecast)

    def stance_at(self, actor, now, *, stances=None):
        current = (self.stances if stances is None else stances).get(actor)
        return current if current is not None and current.starts_at <= now < current.expires_at else None

    def stance_spec(self, actor):
        return self.rows.get(actor, {}).get("stance")

    def _advance_stance(self, stances, actor, kind, started):
        spec = self.stance_spec(actor)
        if spec is None:
            return
        if kind == spec["trigger"]:
            starts = started + spec["starts_after"]
            stances[actor] = ActiveReleaseStance(starts, starts + spec["duration"])
        elif kind == "battle" and self.stance_at(actor, started, stances=stances) is not None:
            stances[actor] = replace(stances[actor], free_battle=False)

    def normal_duration(self, actor, now):
        spec = self.stance_spec(actor)
        return spec["normal_duration"] if self.stance_at(actor, now) is not None else self.normal_durations.get(actor)

    def normal_entry(self, actor, now):
        entries = self.stance_normal_entries if self.stance_at(actor, now) is not None else self.normal_entries
        return entries.get(actor, ())

    def _stance_action(self, action, now, stances):
        stance = self.stance_at(action.slot, now, stances=stances)
        if stance is None:
            return action
        spec = self.stance_spec(action.slot)
        if action.kind == "normal":
            return replace(action, duration=spec["normal_duration"], same_actor_duration=spec["normal_duration"])
        if action.kind == "battle":
            return replace(action, duration=spec["battle_handoff"], same_actor_duration=spec["battle_actionable"],
                           sp_gate=0 if stance.free_battle else action.sp_gate,
                           sp_cost=0 if stance.free_battle else action.sp_cost)
        return action

    def _release(self, bonuses, actor, kind, now):
        bonuses[:] = [b for b in bonuses if now < b.expires_at
                      and not (b.source == actor and b.spec["ends_on_source_action"]
                               and kind not in b.spec.get("preserves_source_actions", ()))]
        for spec in self.rules.get((actor, kind), ()):
            recipients = (actor,) if spec["recipient"] == "self" else tuple(str(i + 1) for i in range(len(self.team)))
            if spec["recipient"] == "other_allies":
                recipients = tuple(a for a in recipients if a != actor)
            bonuses[:] = [b for b in bonuses if not (b.source == actor and b.spec["key"] == spec["key"])]
            starts = now + spec["starts_after"]
            bonuses.extend(ActiveReleaseBonus(spec, actor, recipient, starts + spec["duration"], starts)
                           for recipient in recipients)

    def action_blocked(self, actor, kind, now, *, bonuses=None):
        stance = self.stances.get(actor) if bonuses is None else None
        if kind == "ult" and stance is not None and now < stance.expires_at:
            return True  # The live ultimate button is now the stance cancel action.
        current = self.bonuses if bonuses is None else bonuses
        return any(b.source == actor and now < b.expires_at and kind in b.spec["blocks_source_actions"] for b in current)

    def release_handoff(self, actor, kind):
        rules = self.rules.get((actor, kind), ())
        return max((s["starts_after"] + .05 for s in rules if s["blocks_source_actions"]), default=0)

    def source_unavailable(self, actor):
        # Channel-dependent windows end with the source; timed ally buffs do not.
        self.bonuses[:] = [b for b in self.bonuses if b.source != actor or not b.spec["blocks_source_actions"]]
        self.stances.pop(actor, None)
        self.mechanism_state = self.mechanism_state.keep(set(self.rows)-{actor})
        self.mechanism_assessments.pop(actor, None)

    def confirm(self, actor, kind, started, *, mechanism=None):
        if self.last_confirmed.get((actor, kind)) == started:
            return
        self.last_confirmed[actor, kind] = started
        row = self.rows.get(actor)
        if row is not None:
            quote = mechanism if mechanism is not None else self.mechanism_quote(actor, started)
            self.mechanism_state = after_action(self.mechanism_state, row, actor, kind, started,
                                                quote=quote, stance=self.stance_at(actor, started))
        self._release(self.bonuses, actor, kind, started)
        self._advance_stance(self.stances, actor, kind, started)

    def quote(self, actor, kind):
        row = self.rows.get(actor)
        return row["quotes"].get(kind) if row is not None else None

    def preview_release_price(self, actor, kind, started, duration, *, mechanism=None):
        bonuses = list(self.bonuses)
        self._release(bonuses, actor, kind, started)
        row = self.rows.get(actor)
        model = (after_action(self.mechanism_state, row, actor, kind, started, quote=mechanism,
                               stance=self.stance_at(actor, started), forecast=True) if row else self.mechanism_state)
        return self.price(actor, kind, started+duration, bonuses=bonuses, mechanism=mechanism, mechanism_state=model)

    def cast_only_quote(self, actor, kind):
        quote = self.quote(actor, kind)
        return quote is not None and quote.get("pricing_scope", "").startswith("unconditional_cast_only;")

    def battle_values(self, actor, assessment=None, *, now=None):
        timestamp = now if now is not None else assessment.observed_at if assessment is not None else 0
        mechanism = self.mechanism_quote(actor, timestamp, assessment)
        if mechanism is not None:
            return mechanism.value, 0.0
        binding = self.highlight_damage.get(actor)
        if binding is not None:
            return binding.values(assessment)
        quote = self.quote(actor, 'battle')
        return (quote['crit_expect'], 0.0) if quote is not None else (None, 0.0)

    def battle_components(self, actor, assessment=None):
        binding = self.highlight_damage.get(actor)
        return binding.components(assessment) if binding is not None else ((), ())

    def price_components(self, actor, now, components, *, bonuses=None):
        values = [self._price_value(actor, now, c.element, set(c.damage_tags), c.bonus_pct, c.value, bonuses)
                  for c in components]
        return sum(values) if all(v is not None for v in values) else None

    def price(self, actor, kind, now, *, value=None, bonuses=None, stances=None, mechanism=None, mechanism_state=None):
        quote = self.quote(actor, kind)
        if quote is None:
            return None
        if kind == 'battle' and mechanism is None:
            mechanism = self.mechanism_quote(actor, now, stances=stances)
        if self.stance_at(actor, now, stances=stances) is not None:
            enhanced = self.stance_spec(actor)["quotes"].get(kind)
            if enhanced is not None:
                quote = enhanced
                value = None  # A base master value cannot undo an action replacement.
        if mechanism is not None:
            value = mechanism.value
        elif quote.get("pricing_scope", "").startswith(("support_opener_only", "unconditional_cast_only;")):
            value = quote["crit_expect"]
        model = self.mechanism_state if mechanism_state is None else mechanism_state
        bonuses, extra = self._mechanism_windows(actor, now, quote, model, mechanism, bonuses)
        return self._price_value(actor, now, quote["quote_basis"]["element"],
                                 set(quote["quote_basis"]["damage_tags"]), quote["bonus_pct"],
                                 quote["crit_expect"] if value is None else value, bonuses, extra)

    def _mechanism_windows(self, actor, now, quote, model, mechanism, bonuses):
        if mechanism is not None and mechanism.damage_until is not None:
            current = self.bonuses if bonuses is None else bonuses
            bonuses = [b for b in current if mechanism.quote_started >= b.starts_at
                       and mechanism.damage_until < b.expires_at]
            extra = model.weapon_bonus(actor, mechanism.quote_started, quote['quote_basis']['element'],
                                        until=mechanism.damage_until)
            extra += model.consumption_bonus(actor, mechanism.quote_started, quote['quote_basis']['element'],
                                              until=mechanism.damage_until,
                                              damage_tags=quote['quote_basis']['damage_tags'])
        else:
            extra = model.weapon_bonus(actor, now, quote['quote_basis']['element'])
            extra += model.consumption_bonus(actor, now, quote['quote_basis']['element'],
                                              damage_tags=quote['quote_basis']['damage_tags'])
        return bonuses, extra+(mechanism.extra_damage_bonus if mechanism is not None else 0)

    def _price_value(self, actor, now, element, tags, bonus_pct, value, bonuses, extra_damage_bonus=0):
        panel = self.rows[actor]["panel"]
        # The exported quote already includes fixed weapon/gear/type bonuses.
        # Adding panel.damage_bonus again would dilute each dynamic increment.
        base_bonus = bonus_pct / 100
        base_amp = panel["amplification"].get(element, 0)
        additions = {"attack": 0.0, "damage_bonus": extra_damage_bonus, "amplification": 0.0}
        for bonus in self.bonuses if bonuses is None else bonuses:
            spec = bonus.spec
            if (bonus.recipient == actor and bonus.starts_at <= now < bonus.expires_at
                    and ("all" in spec["elements"] or element in spec["elements"])
                    and (not spec["damage_tags"] or set(spec["damage_tags"]) & tags)):
                additions[spec["bucket"]] += spec["magnitude"]["base"]
        attack = panel["attack_white"] * (1 + panel["attack_percent"]) + panel["attack_flat"]
        if attack <= 0 or 1 + base_bonus <= 0 or 1 + base_amp <= 0:
            return None
        ratio = ((attack + panel["attack_white"] * additions["attack"]) / attack
                 * max(0, 1 + base_bonus + additions["damage_bonus"]) / (1 + base_bonus)
                 * max(0, 1 + base_amp + additions["amplification"]) / (1 + base_amp))
        return value * ratio

    def _forecast_step(self, node, action, index, now, *, releases, horizon, predecessors):
        damage, time, budget, indices, current, actor_locks, stances, conditions, queue_delay, mechanism_state = node
        if index in indices or any(i not in indices for i in predecessors[index]):
            return None
        if indices and predecessors.get("normal", -1) in indices:
            return None
        started = max(time, actor_locks.get(action.slot, now))
        pending_stance = stances.get(action.slot)
        if pending_stance is not None and action.kind in {"battle", "normal"}:
            started = max(started, pending_stance.starts_at)
        action = self._stance_action(action, started, stances)
        if budget < max(action.sp_gate, action.sp_cost):
            return None
        if self.action_blocked(action.slot, action.kind, started, bonuses=current):
            return None
        end = started + action.duration
        if end > now + horizon:
            return None
        stance = self.stance_at(action.slot, started, stances=stances)
        if stance is not None and action.kind in {"normal", "battle"} and end >= stance.expires_at:
            return None  # Do not credit a complete enhanced action across an uncertain exit.
        forecast = list(current)
        next_stances = dict(stances)
        if releases:
            self._release(forecast, action.slot, action.kind, started)
        pricing_bonuses = forecast
        if action.kind == "link":
            # A whole-skill quote has no per-hit timing. Credit a window only
            # when it covers the complete authored interval, not just handoff.
            pricing_bonuses = [b for b in forecast if b.starts_at <= started
                              and started + (action.damage_duration or action.duration) < b.expires_at]
        # Scene evidence survives only reviewed state-preserving actions.
        # Unscoped legacy valuations still apply solely to the first action.
        enhanced = conditions.supports(action.condition) if action.condition is not None else not indices
        mechanism = (self.mechanism_quote(action.slot, started,
                                          action.mechanism_assessment if enhanced else None,
                                          state=mechanism_state, stances=stances, forecast=True)
                     if action.kind == 'battle' else None)
        row = self.rows.get(action.slot)
        next_mechanism = (after_action(mechanism_state, row, action.slot, action.kind, started,
                                       quote=mechanism, stance=stance, forecast=True)
                          if row is not None else mechanism_state)
        pricing_mechanism = next_mechanism
        if action.kind == 'link':
            pricing_mechanism = replace(next_mechanism, combo_weapon=tuple(
                w for w in next_mechanism.combo_weapon if w.starts <= started
                and started+(action.damage_duration or action.duration) < w.expires))
        value = action.value + (action.observed_bonus if enhanced else 0)
        if action.base_components:
            components = action.base_components + (action.observed_components if enhanced else ())
            priced = self.price_components(action.slot, end, components, bonuses=pricing_bonuses)
        else:
            priced = self.price(action.slot, action.kind, end, value=value,
                                bonuses=pricing_bonuses, stances=stances, mechanism=mechanism,
                                mechanism_state=pricing_mechanism)
        if priced is None:
            return None
        if releases:
            self._advance_stance(next_stances, action.slot, action.kind, started)
        locks = dict(actor_locks)
        locks[action.slot] = started + max(action.duration, action.same_actor_duration or 0)
        next_conditions = conditions.after(action.slot, action.kind, self.condition_transitions.get((action.slot, action.kind)))
        return (damage + priced, end, min(300.0, budget - action.sp_cost),
                (*indices, index), forecast, locks, next_stances, next_conditions,
                queue_delay + (started - now if action.kind == "link" else 0), next_mechanism)

    @staticmethod
    def _select_beam(expanded, quota):
        expanded.sort(key=lambda entry: (-entry[0], -entry[2], entry[8], entry[1], entry[3]))
        per_start, beam = {}, []
        for entry in expanded:
            first = entry[3][0]
            if per_start.get(first, 0) < quota:
                beam.append(entry)
                per_start[first] = per_start.get(first, 0) + 1
        return beam

    def _search(self, actions, sp, now, *, releases, bonuses, horizon, width, conditions, by_start=False):
        predecessors = {i: tuple(j for j, a in enumerate(actions) if a.kind == "link"
                                 and a.queue_position < action.queue_position)
                        if action.kind == "link" else () for i, action in enumerate(actions)}
        predecessors["normal"] = (next((i for i, a in enumerate(actions) if a.kind == "normal"), -1)
                                  if any(a.kind == "link" for a in actions) else -1)
        beam = [(0.0, now, sp, (), bonuses, {}, dict(self.stances) if releases else {}, conditions, 0.0,
                 self.mechanism_state)]
        best = beam[0]
        starts = {}
        for _ in actions:
            expanded = [entry for node in beam for index, action in enumerate(actions)
                        if (entry := self._forecast_step(node, action, index, now,
                                                        releases=releases, horizon=horizon,
                                                        predecessors=predecessors)) is not None]
            if not expanded:
                break
            candidate = max(expanded, key=lambda entry: (entry[0], entry[2], -entry[8], -entry[1]))
            if (candidate[0], candidate[2], -candidate[8], -candidate[1]) > (best[0], best[2], -best[8], -best[1]):
                best = candidate
            if by_start:
                for entry in expanded:
                    first = entry[3][0]
                    old = starts.get(first)
                    if old is None or (entry[0], entry[2], -entry[8], -entry[1]) > (old[0], old[2], -old[8], -old[1]):
                        starts[first] = entry
            beam = self._select_beam(expanded, max(1, width // len(actions)))
        return (best, starts, predecessors) if by_start else best

    def _opener_sp_value(self, plan, actions, sp, now, *, horizon, conditions, predecessors):
        """Remove the opener and replay its actual feasible followers, without retiming earlier.

        This is a marginal comparison for this candidate, not a second damage
        sum. Missing queue predecessors, stance unlocks, SP and actor locks
        still apply in the counterfactual.
        """
        initial = (0.0, now, sp, (), list(self.bonuses), {}, dict(self.stances), conditions, 0.0,
                   self.mechanism_state)
        original = initial
        schedule = []
        first_value = first_cost = 0.0
        for i in plan[3]:
            action = actions[i]
            started = max(original[1], original[5].get(action.slot, now))
            pending = original[6].get(action.slot)
            if pending is not None and action.kind in {'battle', 'normal'}:
                started = max(started, pending.starts_at)
            step = self._forecast_step(original, action, i, now, releases=True,
                                       horizon=horizon, predecessors=predecessors)
            if step is None:
                raise ValueError('Stored burst route cannot be replayed')
            if not schedule:
                first_value, first_cost = step[0], sp-step[2]
            schedule.append((i, started, step[1]))
            original = step
        without = (*initial[:1], schedule[0][2], *initial[2:])
        for i, started, end in schedule[1:]:
            without = (without[0], max(without[1], started), *without[2:])
            step = self._forecast_step(without, actions[i], i, now, releases=True,
                                       horizon=horizon, predecessors=predecessors)
            if step is not None:
                without = step
            without = (without[0], max(without[1], end), *without[2:])
        contribution = original[0]-without[0]-first_value
        return BurstSpValue(first_value, contribution, first_cost,
                            (first_value+contribution)/first_cost if first_cost>0 else None)

    def choose(self, actions, sp, now, *, horizon=12.0, width=16, conditions=ForecastConditions()):
        actions = tuple(actions)
        positions = sorted(a.queue_position for a in actions if a.kind == "link")
        if (not actions or len(actions) > 13 or sum(a.kind == "normal" for a in actions) > 1
                or len({(a.slot, a.kind) for a in actions}) != len(actions)
                or positions != list(range(1, len(positions) + 1)) or len(positions) > 4
                or not math.isfinite(sp) or not math.isfinite(now) or sp < 0
                or any(a.kind not in {"normal", "battle", "ult", "link"} or a.duration <= 0
                       or not all(math.isfinite(v) for v in (a.duration, a.value, a.sp_gate, a.sp_cost))
                       or a.value < 0 or (a.kind != "link" and a.queue_position != 0)
                       or a.sp_gate < 0 or a.sp_cost < 0
                       or (a.damage_duration is not None and (not math.isfinite(a.damage_duration)
                                                             or a.damage_duration < a.duration))
                       for a in actions)):
            return None
        blocked_link = min((a.queue_position for a in actions if a.kind == "link"
                            and self.action_blocked(a.slot, a.kind, now)), default=5)
        actions = tuple(a for a in actions if not self.action_blocked(a.slot, a.kind, now)
                        and (a.kind != "link" or a.queue_position < blocked_link))
        if not actions:
            return None
        self.bonuses[:] = [b for b in self.bonuses if now < b.expires_at]
        self.stances = {actor: stance for actor, stance in self.stances.items() if now < stance.expires_at}
        has_links = any(a.kind == "link" for a in actions)
        # A corrected conditional cast must not fall back to the old row-sum
        # ordering merely because a different, unbuffed action wins.
        mechanism_quotes = [self.mechanism_quote(a.slot, now, a.mechanism_assessment)
                            for a in actions if a.kind == 'battle']
        needs_cast_pricing = (has_links or any(self.cast_only_quote(a.slot, a.kind) for a in actions)
                              or any(q is not None and (q.sources or q.minimum_recall) for q in mechanism_quotes))
        needs_sp_pricing = sum(a.kind=='battle' and a.sp_cost>0 for a in actions)>1
        needs_cast_pricing = needs_cast_pricing or needs_sp_pricing
        if (not needs_cast_pricing and not self.bonuses and not self.stances
                and not any(self.rules.get((a.slot, a.kind)) for a in actions)):
            return None
        best, starts, predecessors = self._search(actions, min(sp, 300), now, releases=True,
                            bonuses=list(self.bonuses), horizon=horizon, width=width, conditions=conditions, by_start=True)
        values = {}
        def opener_value(plan):
            first = plan[3][0]
            if first not in values:
                values[first] = self._opener_sp_value(plan, actions, min(sp,300), now, horizon=horizon,
                                                       conditions=conditions, predecessors=predecessors)
            return values[first]
        if best[3]:
            opener_value(best)
        if best[3] and values[best[3][0]].sp_cost>0:
            # Only compare like-for-like paid openers. Actual free casts and
            # energy/queue actions retain the existing finite-horizon comparison;
            # zero SP never supplies infinity or turns a cancel into a producer.
            # Keep a beneficial support before its recipients and preserve real
            # burst deadlines. Do not trade away known sequence damage merely
            # to display a larger ratio for one isolated hit.
            paid = [plan for i, plan in starts.items()
                    if plan[0]>=best[0]-1e-6 and plan[2]>=best[2]-1e-6
                    and opener_value(plan).damage_per_sp is not None and values[i].damage_per_sp>0]
            if paid:
                best = max(paid, key=lambda plan: (values[plan[3][0]].damage_per_sp,
                                                   plan[0], -plan[8], -plan[1]))
        plain = self._search(actions, min(sp, 300), now, releases=False, bonuses=[], horizon=horizon, width=width,
                             conditions=conditions)
        if (not best[3] or (not needs_cast_pricing and (best[0] <= plain[0] + 1e-6
                                             or actions[best[3][0]].kind == "normal"))):
            return None  # Normals are an existing held-output tail, not a new input.
        return ReleaseBurstDecision(actions[best[3][0]],
                                    tuple((actions[i].slot, actions[i].kind) for i in best[3]),
                                    best[0], best[0] - plain[0],
                                    self.mechanism_quote(actions[best[3][0]].slot, now,
                                                         actions[best[3][0]].mechanism_assessment)
                                    if actions[best[3][0]].kind == 'battle' else None,
                                    values[best[3][0]])
