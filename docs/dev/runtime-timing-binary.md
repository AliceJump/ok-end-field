# Final runtime timing binary

`runtime.bin` is the only generated timed-combat data artifact intended for
runtime use. Extraction/mining intermediates stay outside the runtime artifact.

## Stable character ids

Runtime character id = the zero-based position of the character key after
lexicographically sorting `assets/data/characters.json` keys. The binary stores
only the mapping CRC; it stores no character names or keys.

## Binary contents

The file contains:

1. a small numeric per-character section required by the scheduler
   (timings, SP cost/gain and precomputed state durations);
2. one 3-byte record for every unordered 1..4-character combination.

With 32 characters there are exactly:

`C(32,1)+C(32,2)+C(32,3)+C(32,4)=41,448` team records.

Each team record stores only:

- encoded battle-skill axis;
- encoded ultimate priority axis;
- precomputed SP confirmation threshold in 0.5-SP units.

A team is ranked directly by its sorted numeric ids (combinadic/colex rank), so
there is no hash table, string key, team tuple or offset index in the binary.
Input order is irrelevant. If a member dies, runtime simply queries the smaller
surviving-id set and maps the returned ids back to their original HUD slots.

The exporter remains on this experiment branch only to prove reproducibility.
The intended update flow is for the external data repository to regenerate the
single binary and copy only that artifact into the app repository.
