"""Build the single final timed-combat runtime binary.

This exporter is an offline/dev tool only. The produced runtime.bin stores no
names, skill ids, descriptions, action trees, or other extraction
intermediates. A data repository can reproduce it and copy only runtime.bin
into the app repository.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.character_capabilities import load_character_capabilities
from src.data.runtime_timing import (
    COMBO_RECORD_SIZE,
    FPS,
    KINDS,
    MAGIC,
    MAX_TEAM,
    RUNTIME_TABLE,
    VERSION,
    _HEADER,
    _KIND_BITS,
    _NONE_U8,
    _NONE_U16,
    _mapping_crc,
    encode_axis,
    total_combinations,
    RuntimeTimingTable,
)
from src.data.skill_rotation import _ordered_slots_with_dependencies
from src.data.skill_timing import load_skill_timings
from src.data.timing_dps import build_options, load_damage_quotes, optimize_cycle

CHARACTERS_FILE = ROOT / "assets" / "data" / "characters.json"
DAMAGE_FILE = ROOT / "assets" / "data" / "damage_baseline.json"
DEFAULT_THRESHOLD_GAIN = 20.0
SP_ERROR_MARGIN = 5.0


def _u8(value):
    value = int(value)
    if not 0 <= value <= 0xFF:
        raise ValueError(f"u8 overflow: {value}")
    return bytes((value,))


def _u16(value):
    value = int(value)
    if not 0 <= value <= 0xFFFF:
        raise ValueError(f"u16 overflow: {value}")
    return value.to_bytes(2, "little")


def _half_u8(value):
    if value is None:
        return _u8(_NONE_U8)
    encoded = round(float(value) * 2)
    if encoded >= _NONE_U8:
        raise ValueError(f"half-u8 overflow: {value}")
    return _u8(encoded)


def _half_u16(value):
    if value is None:
        return _u16(_NONE_U16)
    encoded = round(float(value) * 2)
    if encoded >= _NONE_U16:
        raise ValueError(f"half-u16 overflow: {value}")
    return _u16(encoded)


def _cs_u16(value):
    if value is None:
        return _u16(_NONE_U16)
    encoded = round(float(value) * 100)
    if encoded >= _NONE_U16:
        raise ValueError(f"centisecond u16 overflow: {value}")
    return _u16(encoded)


def _frame(value):
    return round(float(value) * FPS)


def _character_section(keys, characters, store):
    output = bytearray()
    for runtime_id, key in enumerate(keys):
        name = characters[key]["zh"]
        gain = store.normal_attack_sp_gain(name)
        output.extend(_half_u8(gain))

        profiles_by_kind = {}
        for kind in KINDS:
            profiles = store.profiles(name, kind)
            if len(profiles) > 1:
                raise ValueError(f"Ambiguous runtime profile for {name}:{kind}")
            profiles_by_kind[kind] = profiles[0] if profiles else None

        standard_ids = {
            kind: profile.skill_id
            for kind, profile in profiles_by_kind.items()
            if profile is not None
        }

        for kind in KINDS:
            profile = profiles_by_kind[kind]
            if profile is None:
                output.extend(_u8(0))
                continue
            output.extend(_u8(1))
            output.extend(_u16(_frame(profile.duration)))
            output.extend(_u16(_frame(profile.exclusive)))
            output.extend(_cs_u16(profile.cooldown))
            output.extend(_u8(_NONE_U8 if profile.skill_points is None else profile.skill_points))
            output.extend(_half_u16(profile.sp_cost))
            output.extend(_u16(_NONE_U16 if profile.effect_start is None else _frame(profile.effect_start)))

            windows = []
            for start, end, allowed in profile.allow_next:
                mask = 0
                for target, skill_id in standard_ids.items():
                    if skill_id in allowed:
                        mask |= _KIND_BITS[target]
                if mask:
                    windows.append((_frame(start), _frame(end), mask))
            if len(windows) > 0xFF:
                raise ValueError(f"Too many allow-next windows for {name}:{kind}")
            output.extend(_u8(len(windows)))
            for start, end, mask in windows:
                output.extend(_u16(start))
                output.extend(_u16(end))
                output.extend(_u8(mask))

        for kind in ("battle", "ult"):
            spec = store.state_skill(name, kind)
            if spec is None:
                output.extend(_u8(0))
                continue
            output.extend(_u8(1))
            output.extend(_u16(_frame(spec.duration)))
            output.extend(_cs_u16(spec.end_cooldown))
    return bytes(output)


def _team_baseline_entries(team, rows_by_name, caps):
    has_combo = any(caps.get(name) and caps[name].combo_applier for name in team)
    result = {}
    for name in team:
        row = rows_by_name.get(name) or {}
        try:
            value = float(row.get("cycle_expect") or 0)
        except (TypeError, ValueError):
            value = 0.0

        requirement = row.get("full_caliber_requires") or {}
        raw_attach = requirement.get("attach")
        required = [raw_attach] if isinstance(raw_attach, str) else list(raw_attach or [])
        attach_ok = not required or any(
            any(element in (caps.get(other).attach_elements if caps.get(other) else ()) for element in required)
            for other in team
            if other != name
        )
        if not attach_ok:
            conservative = row.get("cycle_expect_conservative")
            if conservative is not None:
                try:
                    value = float(conservative)
                except (TypeError, ValueError):
                    pass
            required_for_order = None
        else:
            required_for_order = required or None
            if has_combo and row.get("cycle_expect_link4") is not None:
                try:
                    value = float(row["cycle_expect_link4"])
                except (TypeError, ValueError):
                    pass
        result[name] = {"value": value, "requires_attach": required_for_order}
    return result


def _fallback_slots(team, rows_by_name, caps):
    entries = _team_baseline_entries(team, rows_by_name, caps)
    return _ordered_slots_with_dependencies(team, entries, caps)


def _threshold_for(team, gains, global_gain):
    values = [gains[name] for name in team if gains.get(name) is not None]
    if any(gains.get(name) is None for name in team) and global_gain is not None:
        values.append(global_gain)
    return max(values, default=DEFAULT_THRESHOLD_GAIN) + SP_ERROR_MARGIN


def _ult_rate(name, store, quotes):
    profiles = store.profiles(name, "ult")
    quote = quotes.get(name)
    if not profiles or quote is None:
        return 0.0
    return quote.ult / max(max(profile.actionable, 0.3) for profile in profiles)


def _combo_record(team, store, templates, quotes, rows_by_name, caps, gains, global_gain):
    fallback = _fallback_slots(team, rows_by_name, caps)

    options = []
    for slot, name in enumerate(team, 1):
        template = templates.get(name)
        if template is None:
            options = []
            break
        options.append(replace(template, slot=str(slot)))
    plan = optimize_cycle(tuple(options)) if options else None

    if plan is not None:
        battle_local = tuple(int(token) - 1 for token in plan.slots)
    else:
        battle_local = tuple(
            slot
            for slot in fallback
            if store.profiles(team[slot], "battle")
        )

    fallback_rank = {slot: index for index, slot in enumerate(fallback)}
    ult_local = tuple(
        sorted(
            fallback,
            key=lambda slot: (
                -_ult_rate(team[slot], store, quotes),
                fallback_rank[slot],
            ),
        )
    )

    threshold_half = round(_threshold_for(team, gains, global_gain) * 2)
    if not 0 <= threshold_half < _NONE_U8:
        raise ValueError(f"SP threshold cannot fit one byte: {threshold_half / 2}")

    return bytes((
        encode_axis(battle_local, len(team)),
        encode_axis(ult_local, len(team)),
        threshold_half,
    ))


def export_runtime_table(path: Path = RUNTIME_TABLE):
    characters = json.loads(CHARACTERS_FILE.read_text(encoding="utf-8"))
    keys = tuple(sorted(characters))
    names = tuple(characters[key]["zh"] for key in keys)

    store = load_skill_timings()
    character_section = _character_section(keys, characters, store)

    rows = json.loads(DAMAGE_FILE.read_text(encoding="utf-8"))
    rows_by_name = {
        str(row.get("character") or ""): row
        for row in rows
        if isinstance(row, dict) and row.get("character")
    }
    caps = load_character_capabilities()
    quotes = load_damage_quotes(list(names))
    gains = {name: store.normal_attack_sp_gain(name) for name in names}
    global_gain = store.global_normal_attack_sp_gain()

    templates = {}
    for name in names:
        option = build_options([name], store, quotes)
        templates[name] = option[0] if len(option) == 1 else None

    combo_count = total_combinations(len(keys))
    combo_section = bytearray(combo_count * COMBO_RECORD_SIZE)
    cursor = 0
    counts = {}
    for size in range(1, MAX_TEAM + 1):
        counts[size] = 0
        for ids in combinations(range(len(keys)), size):
            team = tuple(names[index] for index in ids)
            record = _combo_record(
                team,
                store,
                templates,
                quotes,
                rows_by_name,
                caps,
                gains,
                global_gain,
            )
            combo_section[cursor:cursor + COMBO_RECORD_SIZE] = record
            cursor += COMBO_RECORD_SIZE
            counts[size] += 1

    if cursor != len(combo_section):
        raise RuntimeError("Runtime combo writer length mismatch")

    header = _HEADER.pack(
        MAGIC,
        VERSION,
        len(keys),
        FPS,
        COMBO_RECORD_SIZE,
        _mapping_crc(keys),
        len(character_section),
        combo_count,
    )
    data = header + character_section + combo_section
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)

    # Full read-back proves the committed runtime consumer can parse it.
    table = RuntimeTimingTable(path=path)
    if table.character_count != len(keys) or table.combo_count != combo_count:
        raise RuntimeError("Runtime table read-back mismatch")

    return {
        "characters": len(keys),
        "character_bytes": len(character_section),
        "combo_records": combo_count,
        "combo_bytes": len(combo_section),
        "records_by_size": counts,
        "bytes": len(data),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", nargs="?", type=Path, default=RUNTIME_TABLE)
    args = parser.parse_args()
    stats = export_runtime_table(args.output)
    print(f"characters:       {stats['characters']}")
    print(f"character bytes:  {stats['character_bytes']:,}")
    print(f"team records:     {stats['combo_records']:,} {stats['records_by_size']}")
    print(f"team table bytes: {stats['combo_bytes']:,}")
    print(f"runtime binary:   {stats['bytes']:,} bytes")
    print(f"wrote: {args.output}")


if __name__ == "__main__":
    main()
