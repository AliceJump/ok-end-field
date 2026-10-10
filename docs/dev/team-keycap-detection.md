# Team keycap detection

`in_team()` now tries a common keycap detector before digit templates. It examines
four bottom-right, uniformly scaled regions, normalizes each to 44×42 pixels, and
requires three supported sides of the keycap outline. After at least two outlines
confirm the HUD, neutral lettering brighter than a neutral dark body can recover
an obscured slot. It uses relative contrast, not an absolute white-text threshold.
The delivery capture previously producing `1110` now produces `1111`.

This is a presence fast path, not digit recognition. A known team needs two visible
non-disabled slots at its original right-aligned positions. A fully visible unknown
four-person layout can establish its size directly. Unknown partial layouts, single
members, one remaining survivor, and uncertain outlines retain the original digit
template path. An extra keycap left of a known short team also forces a template
recheck, as does a missing first key without a known disabled first slot. Hidden
HUDs still clear the count; the combat exit confirmation rules are
unchanged.

## Actual `in_team` replay and timing

Baseline: master `b4470848`. Dataset: 174 PNG captures from the local screenshot
submodule (41 template-positive, 133 template-negative). Positives include ordinary
team HUDs outside combat; templates provide comparison results, not ground truth.
Cold and known-team profiles both retained every boolean result and member count.
The known-team profile uses the baseline size for positive captures and four for
negative captures.

Nine alternating warm repetitions per loaded frame, one OpenCV thread, no image
decoding or logging inside timing. The table averages per-frame median milliseconds
from a run without the full test suite running concurrently.

| Profile / captures | Before (ms) | After (ms) | Change |
|---|---:|---:|---:|
| Cold / 41 team HUDs | 0.508 | 0.271 | 46.7% faster |
| Known team / 41 team HUDs | 0.504 | 0.243 | 51.8% faster |
| Cold / 133 other frames | 1.787 | 2.061 | 15.3% slower |
| Known team / 133 other frames | 1.790 | 2.052 | 14.7% slower |
| Cold / all 174 | 1.486 | 1.639 | 10.3% slower |
| Known team / all 174 | 1.487 | 1.626 | 9.4% slower |

The additional outline probe costs time on frames that fall back to templates.
These results support faster HUD-positive checks, not an overall speedup across
a corpus dominated by menus.

```powershell
uv run --locked python -X utf8 scripts/testing/benchmark_team_keycaps.py --assets ./ok_templates --baseline b4470848 --output ./tmp/team-keycap-benchmark.json
uv run --locked python -X utf8 -m unittest tests.TestTeamKeycapDetector tests.TestCombatPresenceLifecycle tests.TestStateDrivenWaits -v
```

The replay needs the screenshot submodule and the baseline Git object. Tests carry
small unchanged HUD/menu crops and run without the submodule. Fixture provenance is
in `tests/fixtures/team_keycaps/README.md` and `sources.json`.

Regression coverage includes obscured outlines, effects, native three-person teams,
disabled slots, single-survivor fallback, hidden HUDs, eight warehouse menus, and
synthetic dimming at 0.546×/0.3× and 0.546×+40. Uniform resizing to 1440p/2160p and
ultrawide padding check the anchors. There is no actual disabled-gray failure capture
or real ultrawide capture in this dataset; synthetic transformations do not reproduce
game alpha compositing, input-state transitions, or arbitrary HUD scale changes.
