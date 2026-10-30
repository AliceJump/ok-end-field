Real 1920×1080 captures from the Chinese client, 2026-10-30.

Only two horizontal strips are retained: card headers at y=240:330 and the
over-10 warning area at y=850:900. Tests reconstruct their original positions;
UID and unrelated UI are excluded. These are regression fixtures, not templates.

- `empty`: no cards.
- `one`, `two`, `three`: 1, 2, 3 revealed cards; power remains at or below 10.
- `four_over_ten`: 4 cards and the red over-10 warning; the power ruler has scrolled.
- `enemy_dialog`: enemy-information popup; its white background must not count as 5 cards.

Resized fixture checks validate coordinate scaling only, not live support at
other resolutions or in other languages.
