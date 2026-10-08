"""Release-certain buffs priced over the existing scheduler's burst candidates.

No native world, hit inference, combo ownership or resource confirmation lives
here. Fixed panels and resolved rules are exported from a pinned review revision.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

from src.data.battle_conditions import ConditionRequirement, ConditionTransition, ForecastConditions
from src.data.highlight_damage import HighlightDamageBinding, HighlightDamageComponent

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
            or not set(spec["blocks_source_actions"]) <= {"battle", "normal"}):
        raise ValueError("Invalid release window")


def _validate_actor(row):
    for key in ("attack_white", "attack_percent", "attack_flat", "attribute_factor"):
        if not math.isfinite(row["panel"][key]):
            raise ValueError("Invalid fixed attack basis")
    _validate_stance(row)
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


@dataclass(frozen=True)
class ReleaseBurstDecision:
    action: ReleaseBurstAction
    sequence: tuple[tuple[str, str], ...]
    damage: float
    gain: float


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
                      and not (b.source == actor and b.spec["ends_on_source_action"])]
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

    def confirm(self, actor, kind, started):
        if self.last_confirmed.get((actor, kind)) == started:
            return
        self.last_confirmed[actor, kind] = started
        self._release(self.bonuses, actor, kind, started)
        self._advance_stance(self.stances, actor, kind, started)

    def quote(self, actor, kind):
        row = self.rows.get(actor)
        return row["quotes"].get(kind) if row is not None else None

    def battle_values(self, actor, observation=None):
        binding = self.highlight_damage.get(actor)
        if binding is not None:
            return binding.values(observation)
        quote = self.quote(actor, 'battle')
        return (quote['crit_expect'], 0.0) if quote is not None else (None, 0.0)

    def battle_components(self, actor, observation=None):
        binding = self.highlight_damage.get(actor)
        return binding.components(observation) if binding is not None else ((), ())

    def price_components(self, actor, now, components, *, bonuses=None):
        values = [self._price_value(actor, now, c.element, set(c.damage_tags), c.bonus_pct, c.value, bonuses)
                  for c in components]
        return sum(values) if all(v is not None for v in values) else None

    def price(self, actor, kind, now, *, value=None, bonuses=None, stances=None):
        quote = self.quote(actor, kind)
        if quote is None:
            return None
        if self.stance_at(actor, now, stances=stances) is not None:
            enhanced = self.stance_spec(actor)["quotes"].get(kind)
            if enhanced is not None:
                quote = enhanced
                value = None  # A base master value cannot undo an action replacement.
        if quote.get("pricing_scope", "").startswith("support_opener_only"):
            value = quote["crit_expect"]
        return self._price_value(actor, now, quote["quote_basis"]["element"],
                                 set(quote["quote_basis"]["damage_tags"]), quote["bonus_pct"],
                                 quote["crit_expect"] if value is None else value, bonuses)

    def _price_value(self, actor, now, element, tags, bonus_pct, value, bonuses):
        panel = self.rows[actor]["panel"]
        # The exported quote already includes fixed weapon/gear/type bonuses.
        # Adding panel.damage_bonus again would dilute each dynamic increment.
        base_bonus = bonus_pct / 100
        base_amp = panel["amplification"].get(element, 0)
        additions = {"attack": 0.0, "damage_bonus": 0.0, "amplification": 0.0}
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

    def _forecast_step(self, node, action, index, now, *, releases, horizon):
        damage, time, budget, indices, current, actor_locks, stances, conditions = node
        if index in indices:
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
        # Scene evidence survives only reviewed state-preserving actions.
        # Unscoped legacy valuations still apply solely to the first action.
        enhanced = conditions.supports(action.condition) if action.condition is not None else not indices
        value = action.value + (action.observed_bonus if enhanced else 0)
        if action.base_components:
            components = action.base_components + (action.observed_components if enhanced else ())
            priced = self.price_components(action.slot, end, components, bonuses=forecast)
        else:
            priced = self.price(action.slot, action.kind, end, value=value,
                                bonuses=forecast, stances=stances)
        if priced is None:
            return None
        if releases:
            self._advance_stance(next_stances, action.slot, action.kind, started)
        locks = dict(actor_locks)
        locks[action.slot] = started + max(action.duration, action.same_actor_duration or 0)
        next_conditions = conditions.after(action.slot, action.kind, self.condition_transitions.get((action.slot, action.kind)))
        return (damage + priced, end, min(300.0, budget - action.sp_cost),
                (*indices, index), forecast, locks, next_stances, next_conditions)

    @staticmethod
    def _select_beam(expanded, quota):
        expanded.sort(key=lambda entry: (-entry[0], entry[1], entry[3]))
        per_start, beam = {}, []
        for entry in expanded:
            first = entry[3][0]
            if per_start.get(first, 0) < quota:
                beam.append(entry)
                per_start[first] = per_start.get(first, 0) + 1
        return beam

    def _search(self, actions, sp, now, *, releases, bonuses, horizon, width, conditions):
        beam = [(0.0, now, sp, (), bonuses, {}, dict(self.stances) if releases else {}, conditions)]
        best = beam[0]
        for _ in actions:
            expanded = [entry for node in beam for index, action in enumerate(actions)
                        if (entry := self._forecast_step(node, action, index, now,
                                                        releases=releases, horizon=horizon)) is not None]
            if not expanded:
                break
            candidate = max(expanded, key=lambda entry: (entry[0], -entry[1]))
            if (candidate[0], -candidate[1]) > (best[0], -best[1]):
                best = candidate
            beam = self._select_beam(expanded, max(1, width // len(actions)))
        return best

    def choose(self, actions, sp, now, *, horizon=12.0, width=16, conditions=ForecastConditions()):
        actions = tuple(actions)
        if (not actions or len(actions) > 9 or sum(a.kind == "normal" for a in actions) > 1
                or len({(a.slot, a.kind) for a in actions}) != len(actions)
                or not math.isfinite(sp) or not math.isfinite(now) or sp < 0
                or any(a.kind not in {"normal", "battle", "ult"} or a.duration <= 0
                       or not all(math.isfinite(v) for v in (a.duration, a.value, a.sp_gate, a.sp_cost))
                       or a.value < 0 for a in actions)):
            return None
        actions = tuple(a for a in actions if not self.action_blocked(a.slot, a.kind, now))
        if not actions:
            return None
        self.bonuses[:] = [b for b in self.bonuses if now < b.expires_at]
        self.stances = {actor: stance for actor, stance in self.stances.items() if now < stance.expires_at}
        if not self.bonuses and not self.stances and not any(self.rules.get((a.slot, a.kind)) for a in actions):
            return None
        best = self._search(actions, min(sp, 300), now, releases=True,
                            bonuses=list(self.bonuses), horizon=horizon, width=width, conditions=conditions)
        plain = self._search(actions, min(sp, 300), now, releases=False, bonuses=[], horizon=horizon, width=width,
                             conditions=conditions)
        if not best[3] or best[0] <= plain[0] + 1e-6 or actions[best[3][0]].kind == "normal":
            return None  # Normals are an existing held-output tail, not a new input.
        return ReleaseBurstDecision(actions[best[3][0]],
                                    tuple((actions[i].slot, actions[i].kind) for i in best[3]),
                                    best[0], best[0] - plain[0])
