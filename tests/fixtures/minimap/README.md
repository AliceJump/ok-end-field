# Sparse minimap regression

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
