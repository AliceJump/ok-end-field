# Minimap regression fixtures

`low_texture_hud.png` is the top-left 340x280 HUD crop from a 1920x1080 WGC
capture on 2026-11-09. No account identifier or main scene is included.
Reconstruct its original geometry by placing it at (0, 0) in a blank 1920x1080
frame. Terrain detail exceeds the old threshold in only two of eight sectors,
but the circular HUD rim remains visible.

`low_texture_mapping.json` records a 1024x1024 crop of the calibrated map02
resource. The test reads that source region and its affine calibration directly
from the `assets/nav-grid-data` submodule, so no production map copy is stored
in this repository. The expected world position comes from the full-map
diagnostic search, before changing the production gate. Loading, blank and
rim-only variants are constructed by the tests.

`dense_icons_hud.png` is a second 340x280 HUD crop captured on the same day,
with bright city terrain and several outlined icons. Connected dim-pixel growth
leaves only 52.9% terrain, triggering the 55% coverage guard. The former fallback
also shrank icon padding to one pixel, leaving dark borders in the match and
preventing acquisition. Seed-only masking with resolution-scaled padding retains
64.1% terrain and passes the unchanged acquisition thresholds.

`dense_icons_mapping.json` records a second map02 crop at pixel (2330, 8723).
Its reference position comes from the full-map search and visual alignment of
roads/buildings; it is not an independent measurement of the game's coordinates.
The case tests acquisition and calibration consistency. Neither HUD fixture
contains an account identifier or the main scene.

`loading_hud_1.png` is the 450x370 top-left crop used to verify that loading
clears tracking and requires fresh acquisition. The former `loading_hud_2.png`
was byte-identical and has been removed; repeating it provided no second scene.
