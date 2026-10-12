# Left squad HUD marker

`dead_marker.png` is a grayscale crop of the universal circle/slash marker,
not a character portrait. Source: the asset submodule's
`screenshot_20261010_085936.png`, rectangle `(185, 932, 35, 35)` at 1920×1080.
The source screenshot hash and HUD fixture crop are recorded in
`tests/fixtures/squad_hud/sources.json`.

The detector normalizes only the left HUD crop to a 1080p reference before
matching. The search includes the rightward offset when no medicine is equipped.
Death is confirmed across distinct frames; the death slot remains occupied.
The client can also show this marker when an ability object is unavailable during
initialization, so the result describes a stable death/unavailable HUD state,
not access to authoritative game entity data.

Health estimates use the primary fill inside the fixed container defined by its
endcaps, excluding the white combo bar above it. An unreadable/occluded fill is
unknown. A confirmed death overrides health to zero, including when blue VFX
remain visible. LV remains the existing out-of-combat signal.
