# Enemy direction real-image fixtures

Lossless 816×576 center crops from the main repository's 1920×1080 captures.
`sources.json` records the original file SHA-256, frame dimensions, crop position,
and visually checked marker directions. Tests restore crops at their native
coordinates on a blank full-size frame. The crop includes the entire sampled
ellipse and its radial verification strip; no relevant detector pixels are lost.

Positive cases cover overlapping markers, a strong tail, and a weak tail. Negative
cases cover a red floor effect, cherry blossoms, a billboard, a red enemy model,
and a red scene effect that previously survived ring-only fallback. Additional
cases cover uniform red clothing, enemy health bars, menu edges, and a real
marker whose near tail remains visible despite weak far-tail evidence. They do
not establish an accuracy estimate for an independently collected dataset.
