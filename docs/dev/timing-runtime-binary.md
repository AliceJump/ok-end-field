# Runtime timing binary experiment

This branch separates **data mining/export** from **runtime consumption**.

The current snapshot ships both:

- `index.json`: 1,468,718 bytes
- `records.json.gz`: 3,563,277 bytes
- total runtime source data: 5,031,995 bytes

The experiment exports only values already consumed by timed combat into one
`runtime_timing.bin`.

## Binary format

The file is deliberately simple and dependency-free:

```text
8 bytes   magic: OKETB1\0\0
1 byte    schema version
4 bytes   uncompressed payload length (little-endian)
4 bytes   CRC32 of uncompressed payload
remaining zlib(level=9) compressed canonical UTF-8 JSON
```

The payload contains precomputed per-character runtime values only:

- aliases / character id;
- battle / link / ultimate `SkillTiming` profiles;
- effect-start timing;
- SP cost and cooldown;
- allow-next windows;
- normal-attack combo-finisher SP gain;
- battle/ultimate state-skill replacement metadata and manual-end cooldown.

It intentionally does **not** contain the original 1,348 SkillData/BuffData
records, raw binary data, blackboards, or unrelated action fields.

## Export workflow

The full data repository owns the extraction logic and runs:

```powershell
uv run python -X utf8 scripts/skill-data/export_runtime_timing.py
```

It produces:

```text
assets/data/skill_timings/runtime_timing.bin
```

The exporter performs a read-back verification and prints:

- canonical payload size;
- final binary size;
- current `index.json + records.json.gz` size;
- binary/source ratio.

After a game-data update, the application repository only needs the newly
exported `.bin`.

## Runtime query

```python
from src.data.timing_runtime_binary import load_runtime_timing_bundle

bundle = load_runtime_timing_bundle()
result = bundle.team(["梨诺", "?", "诀"])
```

The query is **slot preserving** and does not require four complete members.
`None`, empty strings, and `"?"` remain empty slots instead of causing later
members to shift left. This matches the timed-combat death mask: if slot 2 dies,
slot 3 remains slot 3.

The result includes:

- one entry per original input slot;
- resolved battle/link/ult profiles;
- state-skill metadata;
- each operator's normal-attack finisher SP gain;
- the maximum known finisher gain among present members;
- the corresponding `max_gain + 5 SP` low-cost confirmation threshold.

## Scope of this branch

This branch proves the transport and query format. It does not remove the
lossless snapshot yet. The intended migration is:

1. keep lossless extraction in the external data repository;
2. export and verify `runtime_timing.bin`;
3. copy only that artifact into this repository;
4. timed combat reads `RuntimeTimingBundle` by default;
5. after equivalence tests pass, the lossless snapshot can remain an offline/export fixture rather than a runtime dependency.

The path is intentionally stable (no snapshot date in the filename), so a game-data
update can replace exactly one binary file without changing application code.
