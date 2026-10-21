"""Reviewed nominal normal-window pricing; no native world or detection."""

from __future__ import annotations

import hashlib
import json
import math
from bisect import bisect_right
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parents[2] / "assets/data/normal_attack_windows"


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
class NormalAttackWindow:
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

    @lru_cache(maxsize=128)
    def attacks(self, cast_at, *, input_at=None, initial_layers=0, initial_layers_until=None, stopped_at=None):
        if any(
            value is not None and not math.isfinite(value)
            for value in (cast_at, input_at, initial_layers_until, stopped_at)
        ):
            raise ValueError("Invalid normal-window clock")
        if initial_layers != int(initial_layers) or not 0 <= initial_layers <= self.maximum:
            raise ValueError("Invalid projected initial crit layers")
        end = cast_at + self.activation + self.duration
        start = max(cast_at + self.first_input, input_at if input_at is not None else -math.inf)
        stop = min(end, stopped_at if stopped_at is not None else end)
        result, count, index = [], int(initial_layers), 0
        layer_expires = (
            (initial_layers_until if initial_layers_until is not None else start + self.layer_lifetime)
            if count
            else None
        )
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
            result.append(
                WindowAttack(end + self.final_delay, self.final_scale, count, "finisher", end + self.layer_lifetime)
            )
        return tuple(result)

    def price(
        self,
        cast_at,
        resolve,
        *,
        cuts=(),
        from_time=None,
        input_at=None,
        until=None,
        initial_layers=0,
        initial_layers_until=None,
        frozen=None,
        stopped_at=None,
    ):
        """Call the existing master price resolver for grouped nominal events."""
        attacks = tuple(
            a
            for a in self.attacks(
                cast_at,
                input_at=input_at,
                initial_layers=initial_layers,
                initial_layers_until=initial_layers_until,
                stopped_at=stopped_at,
            )
            if (from_time is None or a.projected_at >= from_time) and (until is None or a.projected_at <= until)
        )
        groups = {}
        for attack in attacks:
            key = (bisect_right(cuts, attack.projected_at), attack.layers, attack.kind)
            if key not in groups:
                groups[key] = attack
            else:
                groups[key] = replace(groups[key], multiplier=groups[key].multiplier + attack.multiplier)
        damage = conditional = 0.0
        for attack in groups.values():
            basic = resolve(attack, False)
            if basic is None:
                return NormalWindowQuote(None, None, attacks, attack.projected_at, ("Unresolved normal-window price",))
            damage += basic
            if attack.kind == "finisher":
                final = resolve(attack, True)
                extra = resolve(replace(attack, multiplier=self.frozen_scale), True)
                if final is None or extra is None:
                    return NormalWindowQuote(
                        None, None, attacks, attack.projected_at, ("Unresolved Frozen final price",)
                    )
                increment = final + extra - basic
                conditional += increment
                if frozen is True:
                    damage += increment
        return NormalWindowQuote(
            damage, conditional, attacks, attacks[-1].projected_at if attacks else (from_time or cast_at)
        )


@lru_cache(maxsize=4)
def read_windows(path=SNAPSHOT):
    path = Path(path)
    manifest = json.loads((path / "index.json").read_bytes())
    raw = (path / "windows.json").read_bytes()
    if (
        manifest["schema_version"] != 1
        or hashlib.sha256(raw).hexdigest() != manifest["payload_sha256"]
        or len(manifest["source_revision"]) != 40
        or not manifest["source_hashes"]
    ):
        raise ValueError("Normal-window provenance/hash mismatch")
    data = json.loads(raw)
    if data["schema_version"] != 1 or set(data["actors"]) != {"yvonne"}:
        raise ValueError("Unreviewed normal-window family")
    row = data["actors"]["yvonne"]
    if row["profile"] != {"character_level": 90, "skill_rank": 12, "potential": 0}:
        raise ValueError("Unreviewed normal-window build")
    values = dict(row["model"])
    values["stages"] = tuple((duration, tuple(shots), tuple(layers)) for duration, shots, layers in values["stages"])
    model = NormalAttackWindow(**values)
    numeric = [value for key, value in values.items() if key != "stages"]
    expected = (
        (22, (9, 15, 21), (9, 15, 21)),
        (24, (11, 14, 17, 21), (11, 14, 17)),
        (13, (1, 4, 7, 11), (1, 4, 7)),
        (29, tuple(range(13, 29, 2)), tuple(range(13, 29, 2))),
        (17, tuple(range(1, 17, 2)), tuple(range(1, 17, 2))),
    )
    frames = tuple(
        (round(d * 30), tuple(round(v * 30) for v in shots), tuple(round(v * 30) for v in layers))
        for d, shots, layers in model.stages
    )
    if (
        any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in numeric)
        or model.maximum != 10
        or model.duration != 7
        or model.layer_lifetime != 4
        or len(model.stages) != 5
        or frames != expected
        or any(
            not math.isclose(d, approved[0]/30, abs_tol=1e-9)
            or any(not math.isclose(v, frame/30, abs_tol=1e-9) for v, frame in zip(shots, approved[1]))
            or any(not math.isclose(v, frame/30, abs_tol=1e-9) for v, frame in zip(layers, approved[2]))
            for (d, shots, layers), approved in zip(model.stages, expected)
        )
        or any(not math.isfinite(v) for d, shots, layers in model.stages for v in (d, *shots, *layers))
        or any(
            not math.isclose(value, approved, abs_tol=1e-7)
            for value, approved in (
                (model.activation, 61 / 30),
                (model.first_input, 2.283333333333333),
                (model.final_delay, 28 / 30),
                (model.final_recovery, 2.15),
                (model.final_scale, 3),
                (model.shot_scale, 0.2),
                (model.frozen_scale, 6),
                (model.crit_per_layer, 0.03),
                (model.full_crit_damage, 0.6),
                (model.frozen_talent_crit_damage, 0.4),
            )
        )
    ):
        raise ValueError("Unreviewed normal-window cadence/parameters")
    return model, row, manifest["payload_sha256"]
