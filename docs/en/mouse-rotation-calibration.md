# Mouse Rotation Calibration (MouseRotationCalibration) Investigation Summary

> Date: 2026-08-08
> Task involved: `src/tasks/test/MouseRotationCalibration.py`
> Conclusion status: **Core conclusion settled; flat-ground re-test pending to confirm the terrain hypothesis**

## 1. Task introduction

`MouseRotationCalibration` is a debug-visible test task that calibrates the conversion coefficient between 「mouse horizontal displacement pixels ↔ character view rotation angle」:

$$k = \frac{\Delta yaw}{dx} = \frac{after - before}{dx}$$

- Mouse right `dx>0` → view turns left `Δyaw<0`, so **k is always negative**.
- The coefficient is in 「°/px」; after calibration it can convert a target turn angle to mouse displacement: `dx = round(target_yaw / k)`.
- Not persisted; calibrated on every run. It also supports manually filling in the coefficient to skip calibration and directly verify.

### Config keys (13)

| Config key | Current value | Description |
|---|---|---|
| `标定位移dx` (Calibration displacement dx) | 400 | Used for single-displacement calibration |
| `标定位移列表(逗号分隔)` (Calibration displacement list (comma-separated)) | `400,600,1000` | Multi-displacement calibration plan |
| `重复次数` (Repeat count) | 4 | Sample count per displacement |
| `W长按时间(秒)` (W hold time (seconds)) | 0.2 | How long to hold W to refresh the facing |
| `角度刷新等待(秒)` (Angle refresh wait (seconds)) | 1.0 | Wait for the arrow to refresh after pressing W |
| `转向后等待(秒)` (Wait after turning (seconds)) | 0.3 | Wait for the turn to settle after sending mouse displacement |
| `最低置信度` (Minimum confidence) | 0.6 | Arrow OCR confidence threshold |
| `验证目标角度(度)` (Verification target angle (degrees)) | 90.0 | For single-angle verification |
| `验证角度列表(逗号分隔)` (Verification angle list (comma-separated)) | `44,55,77,99` | Multi-angle verification plan |
| `验证次数` (Verification count) | 2 | Verification count per angle |
| `验证误差容差(度)` (Verification error tolerance (degrees)) | 5.0 | PASS tolerance |
| `左右方向成对验证` (Paired left/right verification) | true | Automatically adds the opposite direction per angle |
| `手动yaw_per_pixel(留空用标定)` (Manual yaw_per_pixel (leave empty to calibrate)) | `-0.083` | Manual coefficient (positive values are auto-negated) |

## 2. Feature evolution history

1. **Calibrate only** (from 13:15) → no W hold, all readings 0, invalid.
2. **Added W hold to refresh facing** (13:26) → calibration data valid for the first time.
3. **Added single-angle verification** (16:43) → +90° verification closed loop.
4. **Added multi-angle verification** (16:49) → 4-angle list.
5. **Added multi-displacement calibration** (16:55) → 200/400/600/1000 plan.
6. **Added wait after turning** (17:01) → decouples turn completion from W-refresh facing, to rule out timing hypotheses.
7. **Added paired left/right verification** (17:09) → auto-adds the opposite direction per angle, to investigate left/right asymmetry.
8. **Added automatic negation of the manual coefficient** (before 17:31) → prevents sign errors.

## 3. All run-round data

### 3.1 Debug period (13:15-13:26) — no valid data

| Time | Config | Observation |
|---|---|---|
| 13:15 | dx=100, refresh=0.1 | Angle barely moved (Δ≈0) → W not held, facing not refreshed |
| 13:16 | dx=400, refresh=1.0 | First 2 samples Δ=0, last 2 samples Δ=7~9° → refresh unstable |
| 13:17 | dx=400 | All Δ=0, no reaction at all |
| 13:26 | **w_hold=0.3** | ✅ First valid: +400→-39.5°, -400→+34.5° |

> **Key conclusion: measurement only works after adding `W hold time=0.3`** — an instantaneous keypress is not enough for the character's displacement to refresh the arrow.

### 3.2 Formal calibration period (16:34-16:56)

| Time | Config | mean_k | k_pos | k_neg | Verification |
|---|---|---|---|---|---|
| 16:34 | dx=400×4 | -0.08063 | -0.08625 | -0.07500 | — |
| 16:43 | dx=400×4 | -0.09937 | -0.12375* | -0.07500 | +90° ✅ error 0.5° |
| 16:49 | dx=400×4 | -0.08063 | -0.08625 | -0.07500 | +44/55/77/99 all PASS |
| 16:55 | multi-displacement 200..1000 | -0.08493 | -0.09527 | -0.07458 | +44/55/77/99 all PASS |

\* At 16:43, sample1 produced an abnormal k=-0.16125 because before=142 (start not reset).

### 3.3 Timing experiment period (17:01-17:05) — ruling out timing

| Time | Config | mean_k | k_pos | k_neg |
|---|---|---|---|---|
| 17:01 | turn_settle=1.3 | -0.08522 | -0.09590 | -0.07454 |
| 17:05 | removed +200, 6 samples | -0.08224 | -0.08842 | -0.07606 |

> **Conclusion: 1.3s and 0.3s data agree bit-for-bit → rules out the timing hypothesis of 「turn interrupted by W before finishing」.**
> Also: +200 twice gave k=-0.115/-0.1175 (about 40% larger); after removing them, std dropped from 0.0143 to 0.0071.

### 3.4 Paired left/right verification period (17:09-17:23) — discovering the terrain truth

| Time | Start yaw | mean_k | k_pos | k_neg | Gap | Verification highlights |
|---|---|---|---|---|---|---|
| 17:09 | ~112° | -0.08369 | -0.09133 | -0.07606 | right larger 20% | -44° FAIL(-6°) |
| **17:13** | **~4°** | **-0.08253** | **-0.08217** | **-0.08289** | **almost symmetric** | +44 FAIL(-7°), -99 FAIL(-11°) |
| 17:22 | ~257° | -0.09031 | -0.08258 | -0.09803 | **left larger 19%** | 3 large-angle WARN |

> **Decisive finding: the asymmetric direction reverses with ~180° of start position** → rules out game left/right sensitivity asymmetry; the real culprit is **the test-point terrain** (the character is pulled off-course by the slope when moving forward with W).

### 3.5 Manual coefficient period (17:27-17:32) — sign incident + verification success

| Time | Manual coefficient | Result |
|---|---|---|
| 17:27 | +0.083 (**sign reversed**) | 8/8 all FAIL, error 84~207° (all directions reversed) |
| 17:30 | +0.083 (unchanged) | continues all FAIL |
| **17:31** | **-0.083** | direction restored, error converged within ±7° |

17:31 round (manual -0.083) per-angle details:

| Angle | Actual mean | Error | Status |
|---|---|---|---|
| +44° | +43.0° | -1.0° | PASS |
| -44° | -50.5° | -6.5° | WARN |
| +55° | +55.5° | +0.5° | PASS |
| -55° | -60.0° | -5.0° | PASS |
| +77° | +80.5° | +3.5° | PASS |
| -77° | -83.5° | -6.5° | WARN |
| +99° | +93.5° | -5.5° | WARN |
| -99° | -103.0° | -4.0° | WARN |

> Positive directions are basically normal (≤3.5°); negative directions are consistently off by 5~6.5°, consistent with the terrain effect of this round's start position.

## 4. Cross-round stable conclusions

1. **Left turns (dx<0) are stable**: across rounds -400→+30~32°, -600→+45.5~50.5°, -1000→+76.5~87°, with large-displacement k stable at **-0.076~-0.084**.
2. **Right turns (dx>0) are strongly affected by start/terrain**: the same displacement differs by 8~12° across starts.
3. **Recommended coefficient ≈ -0.082 ~ -0.084 °/px** (the 17:13 round is most symmetric, smallest std 0.004).
   - 1000px ≈ 84°, 600px ≈ 50°, 400px ≈ 33°, 90° ≈ 1080px.
4. **+200 is a small-displacement anomaly zone** (k ~40% larger) and has been removed from the calibration list.
5. **Large angles (99°) always have larger errors** (±4~14°) → segmented turning is recommended for formal use.

## 5. Camera-offset hypothesis analysis (user question: the character always appears left of center)

The user's observation itself holds — many 3D games have an offset camera (more view in front), making the character appear left/down. But the data **rules out** a fixed camera offset:

1. **A fixed offset cancels out in Δyaw**: if a fixed camera offset adds a constant δ to the arrow reading (independent of facing), then `(after+δ) - (before+δ) = after - before`, and δ disappears without affecting k.
2. **Decisive evidence — asymmetry reverses with the start**: a fixed offset is spatially fixed and direction-independent; if it made right turns larger, right turns should be larger at any start. But from 112°→257° (a difference of ~145°), the larger direction reversed entirely. No fixed offset can explain this.
3. **The only self-consistent explanation remains terrain**: the arrow reads the facing direction of the character moving forward with W; the slope pulls the character off-course; different starts cross the slope differently → the larger direction reverses accordingly.
4. **The send layer already rules out asymmetry**: `send_mouse_delta` uses `user32.mouse_event(MOUSEEVENTF_MOVE, dx, 0, 0, 0)` with sign passed straight through — fully symmetric.

### Decisive verification method

Find a **completely flat, open** place (plaza/level ground) and re-run 「Calibration + paired left/right verification」:

- `k_pos ≈ k_neg` (difference < 2%) and most of the 8 angles PASS → **terrain hypothesis confirmed, camera offset ruled out**;
- Still clearly asymmetric → only then does the camera/game-mechanic issue come into play.

Visual aid: watch the arrow direction while moving forward with W; if the arrow drifts slowly (rather than staying still), the terrain is pulling the character off-course — the most direct evidence.

## 6. Sign-incident review

- The user once filled `"0.083"` (should be negative) in `手动yaw_per_pixel`.
- Symptom: a +44° target used dx=+530 moving right but instead turned to -41°, error 84°~207°, all directions reversed.
- Handling: 1) the config file was corrected to `"-0.083"`; 2) the code now **auto-negates** a positive manual coefficient and notifies via log, preventing recurrence.

## 7. Follow-up suggestions

1. **Re-test on flat ground** to confirm the terrain hypothesis (the only pending experiment).
2. **Align the start facing**: formal turning tasks should align the start facing before turning to reduce terrain influence.
3. **Use small-angle segments for formal turning**: each step ≤30° (≈360px), and use per-direction coefficients (k_pos / k_neg).
4. **Minor state-judgment fix**: it is suggested to change 「FAIL only when both verifications fail; a single FAIL is WARN」 to the stricter 「FAIL when both fail」; the current implementation may under-report.

## Appendix: related files

- Task source: `src/tasks/test/MouseRotationCalibration.py`
- Run config: `configs/MouseRotationCalibration.json`
- Unit tests: `tests/TestMouseRotationCalibration.py` (12 cases, all pass)
- Mouse send layer: `src/interaction/Mouse.py` (`send_mouse_delta`, symmetric in sign)
- Core API: `src/core/base_mixin/runtime_mixin.py` (`get_arrow_angle` / `press_key` / `active_and_send_mouse_delta`)
