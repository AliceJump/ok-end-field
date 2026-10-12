# Skill keycap regression crops

These are unchanged bottom-right crops from `AliceJump/ok-end-field-x-anylabeling-asset`
at `74cbc39142643c128caded5d228cdcce4c6f44a2` (including locally available captures).
`sources.json` records the original filename, frame dimensions, and expected physical
keycap occupancy. Each crop covers the last 420 by 116 pixels at 1080p equivalent;
tests restore it at the original frame's bottom-right corner on a blank canvas.

- `delivery`: all four keys are visible, but the fourth outline blends into the scene.
- `effects`: all four outlines remain visible with colored effects behind the keys.
- `native_three`: native keys 1–3 occupy the three rightmost physical positions.
- `single_outline`: an isolated outline is insufficient to confirm a team.
- `menu_1`–`menu_8`: warehouse menus that fooled a relaxed text/background prototype.

Brightness perturbations, erased slots, and ultrawide padding are synthetic test
inputs, not captures of actual disabled/dead key hints or ultrawide gameplay.
