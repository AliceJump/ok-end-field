"""Release-certain buffs priced over the existing scheduler's burst candidates.

No native world, hit inference, combo ownership or resource confirmation lives
here. Fixed panels and resolved rules are exported from a pinned review revision.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parents[2] / "assets/data/release_burst"
ELEMENTS = {"all", "物理", "寒冷", "灼热", "电磁", "自然"}
TAGS = {"normal", "skill", "combo", "ultimate"}


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
    actors = data["actors"]
    if (len(actors) != manifest["actors"] or len({r["character"] for r in actors}) != len(actors)
            or sum(len(r["rules"]) for r in actors) != manifest["rules"]):
        raise ValueError("Release snapshot selection mismatch")
    for row in actors:
        panel = row["panel"]
        for key in ("attack_white", "attack_percent", "attack_flat", "attribute_factor"):
            if not math.isfinite(panel[key]):
                raise ValueError("Invalid fixed attack basis")
        for quote in row["quotes"].values():
            if (quote["quote_basis"]["element"] not in ELEMENTS - {"all"}
                    or not set(quote["quote_basis"]["damage_tags"]) <= TAGS
                    or not all(math.isfinite(quote[key]) for key in ("non_crit", "crit_expect", "bonus_pct"))):
                raise ValueError("Invalid fixed quote")
        for spec in row["rules"]:
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


class ReleaseBurstPlanner:
    def __init__(self, team, *, store=None, snapshot=SNAPSHOT):
        self.team = tuple(team)
        self.rows = {str(i + 1): read_snapshot(snapshot)[name] for i, name in enumerate(team)
                     if name in read_snapshot(snapshot)}
        self.rules = {(actor, kind): tuple(s for s in row["rules"] if s["kind"] == kind)
                      for actor, row in self.rows.items() for kind in ("battle", "ult")}
        self.bonuses = []
        self.last_confirmed = {}
        self.normal_durations = {}
        if store is not None:
            for actor, row in self.rows.items():
                # One basic normal-chain tail, never an enhanced stance or hit proof.
                profiles = []
                ult = store.profiles(row["character"], "ult")
                if not ult:
                    continue
                native_ids = {p.skill_id.split("_ultimate")[0] for p in ult}
                if len(native_ids) != 1:
                    continue  # Ambiguous variants do not establish a normal chain.
                native_id = native_ids.pop()
                for index in range(1, 7):
                    try:
                        profiles.append(store.profile(f"{native_id}_attack{index}"))
                    except KeyError:
                        break
                if profiles:
                    self.normal_durations[actor] = sum(max(p.actionable, .3) for p in profiles)

    def preserve(self, previous):
        unchanged = {str(i + 1) for i, name in enumerate(self.team)
                     if i < len(previous.team) and name == previous.team[i]}
        self.bonuses = [b for b in previous.bonuses if b.source in unchanged]
        self.last_confirmed = {key: time for key, time in previous.last_confirmed.items() if key[0] in unchanged}

    def _release(self, bonuses, actor, kind, now):
        bonuses[:] = [b for b in bonuses if now < b.expires_at
                      and not (b.source == actor and b.spec["ends_on_source_action"])]
        for spec in self.rules.get((actor, kind), ()):
            recipients = (actor,) if spec["recipient"] == "self" else tuple(str(i + 1) for i in range(len(self.team)))
            if spec["recipient"] == "other_allies":
                recipients = tuple(a for a in recipients if a != actor)
            bonuses[:] = [b for b in bonuses if not (b.source == actor and b.spec["key"] == spec["key"])]
            bonuses.extend(ActiveReleaseBonus(spec, actor, recipient, now + spec["duration"])
                           for recipient in recipients)

    def confirm(self, actor, kind, started):
        if self.last_confirmed.get((actor, kind)) == started:
            return
        self.last_confirmed[actor, kind] = started
        self._release(self.bonuses, actor, kind, started)

    def quote(self, actor, kind):
        row = self.rows.get(actor)
        return row["quotes"].get(kind) if row is not None else None

    def price(self, actor, kind, now, *, value=None, bonuses=None):
        quote = self.quote(actor, kind)
        if quote is None:
            return None
        panel = self.rows[actor]["panel"]
        element, tags = quote["quote_basis"]["element"], set(quote["quote_basis"]["damage_tags"])
        # The exported quote already includes fixed weapon/gear/type bonuses.
        # Adding panel.damage_bonus again would dilute each dynamic increment.
        base_bonus = quote["bonus_pct"] / 100
        base_amp = panel["amplification"].get(element, 0)
        additions = {"attack": 0.0, "damage_bonus": 0.0, "amplification": 0.0}
        for bonus in self.bonuses if bonuses is None else bonuses:
            spec = bonus.spec
            if (bonus.recipient == actor and now < bonus.expires_at
                    and ("all" in spec["elements"] or element in spec["elements"])
                    and (not spec["damage_tags"] or set(spec["damage_tags"]) & tags)):
                additions[spec["bucket"]] += spec["magnitude"]["base"]
        attack = panel["attack_white"] * (1 + panel["attack_percent"]) + panel["attack_flat"]
        if attack <= 0 or 1 + base_bonus <= 0 or 1 + base_amp <= 0:
            return None
        ratio = ((attack + panel["attack_white"] * additions["attack"]) / attack
                 * max(0, 1 + base_bonus + additions["damage_bonus"]) / (1 + base_bonus)
                 * max(0, 1 + base_amp + additions["amplification"]) / (1 + base_amp))
        return (quote["crit_expect"] if value is None else value) * ratio

    def _search(self, actions, sp, now, *, releases, bonuses, horizon, width):
        beam = [(0.0, now, sp, (), bonuses, {})]
        best = beam[0]
        for _ in actions:
            expanded = []
            for damage, time, budget, indices, current, actor_locks in beam:
                for index, action in enumerate(actions):
                    if index in indices or budget < max(action.sp_gate, action.sp_cost):
                        continue
                    started = max(time, actor_locks.get(action.slot, now))
                    end = started + action.duration
                    if end > now + horizon:
                        continue
                    forecast = list(current)
                    if releases:
                        self._release(forecast, action.slot, action.kind, started)
                    priced = self.price(action.slot, action.kind, end, value=action.value, bonuses=forecast)
                    if priced is None:
                        continue
                    locks = dict(actor_locks)
                    locks[action.slot] = started + max(action.duration, action.same_actor_duration or 0)
                    entry = (damage + priced, end, min(300.0, budget - action.sp_cost), (*indices, index), forecast, locks)
                    expanded.append(entry)
                    if (entry[0], -entry[1]) > (best[0], -best[1]):
                        best = entry
            if not expanded:
                break
            expanded.sort(key=lambda entry: (-entry[0], entry[1], entry[3]))
            per_start, beam = {}, []
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
                or len({(a.slot, a.kind) for a in actions}) != len(actions)
                or not math.isfinite(sp) or not math.isfinite(now) or sp < 0
                or any(a.kind not in {"normal", "battle", "ult"} or a.duration <= 0
                       or not all(math.isfinite(v) for v in (a.duration, a.value, a.sp_gate, a.sp_cost))
                       or a.value < 0 for a in actions)):
            return None
        self.bonuses[:] = [b for b in self.bonuses if now < b.expires_at]
        if not self.bonuses and not any(self.rules.get((a.slot, a.kind)) for a in actions):
            return None
        best = self._search(actions, min(sp, 300), now, releases=True,
                            bonuses=list(self.bonuses), horizon=horizon, width=width)
        plain = self._search(actions, min(sp, 300), now, releases=False, bonuses=[], horizon=horizon, width=width)
        if not best[3] or best[0] <= plain[0] + 1e-6 or actions[best[3][0]].kind == "normal":
            return None  # Normals are an existing held-output tail, not a new input.
        return ReleaseBurstDecision(actions[best[3][0]],
                                    tuple((actions[i].slot, actions[i].kind) for i in best[3]),
                                    best[0], best[0] - plain[0])
