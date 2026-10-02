# Compact runtime timing table experiment

This branch tests separating **offline extraction** from **runtime scheduling**.

- `runtime_v1.bin` is a precomputed runtime artifact. The game-side repository
  only needs this artifact plus a tiny reader; it does not need the original
  decoded records to answer ordinary team/profile queries.
- The current prototype stores 34 character identities plus standard
  battle/link/ultimate duration, exclusive frame, cooldown, SP cost and
  allow-next windows.
- The binary is **5,455 bytes**. The equivalent compact JSON selection is
  15,706 bytes. The existing `index.json + records.json.gz` snapshot is about
  5.03 MB, so this first binary prototype is roughly 0.11% of those two files.
- Team lookup is slot-preserving and accepts `?` at any position. It therefore
  does not require a complete four-person team and composes with the runtime
  dead-slot mask on the parent branch.

This is deliberately an experiment, not yet the authoritative
`SkillTimingStore`: effect-start, normal-attack finisher SP and state-skill
evidence still need to be exported into the offline artifact before the large
lossless records can be removed from runtime completely. The export should live
in the data/research repository; this repository should receive only the
generated binary.
