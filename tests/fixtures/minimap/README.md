# Minimap regression fixtures

`low_texture_hud.png` is the top-left 340x280 HUD crop from a 1920x1080 WGC
capture on 2026-11-09. No account identifier or main scene is included.
Reconstruct its original geometry by placing it at (0, 0) in a blank 1920x1080
frame. Terrain detail exceeds the old threshold in only two of eight sectors,
but the circular HUD rim remains visible.

`low_texture_map.png` is an independent 1024x1024 crop of the calibrated map02
resource in nav-grid-data revision a78e109. The affine transform in
`low_texture_mapping.json` includes the crop offset. The expected world position
comes from the full-map diagnostic search, before changing the production gate.
These fixtures exercise real image matching without requiring the submodule or
a running game. Loading/blank/rim-only variants are constructed by the tests.

`dense_icons_hud.png` is a second 340x280 HUD crop captured on the same day,
with bright city terrain and several outlined icons. Connected dim-pixel growth
leaves only 52.9% terrain, triggering the 55% coverage guard. The former fallback
also shrank icon padding to one pixel, leaving dark borders in the match and
preventing acquisition. Seed-only masking with resolution-scaled padding retains
64.1% terrain and passes the unchanged acquisition thresholds.

`dense_icons_map.png` is an independent map02 resource crop at pixel (2330, 8723),
1024x1024, from the same nav-grid-data revision. Its mapping includes that offset.
The reference position comes from the full-map search and visual alignment of
roads/buildings; it is not an independent measurement of the game's coordinates.
The fixture is intended to test acquisition and consistent calibration. Neither
HUD fixture contains an account identifier or the main scene.
