# Synthetic measurement fixtures

These requests contain no real location or identity. Coordinates were generated
from synthetic three-dimensional scenes using the independent world-to-image
projector in `skills/geo-sleuth/tests/test_measure.py`.

| File | Expected behavior |
|---|---|
| `range.json` | Conditional depth 19.3131–20.7136 m; nominal 20 m; `partial` because no source image is attached. |
| `range-missing-focal.json` | `needs_input`, exit 2; no depth result. |
| `shadow.json` | Object height 2 m, shadow length 5 m, solar elevation 21.801409°; `partial` with explicit model limitations. |
| `shadow-sensitivity.json` | Reproducible sensitivity envelope with height uncertainty; not a confidence interval. |
| `shadow-horizon.json` | `needs_input`, exit 2 because ground rays are too close to the horizon. |

The shadow scene uses a 1600 × 1200 image, focal length 1200 pixels, principal
point (800, 600), camera height 1.6 m, pitch −8°, roll 12°, object foot
(3, 0, 20), and shadow tip (7, 0, 23) in right/up/forward world coordinates.
The horizon fixture instead places the ground points 100 m forward and uses
zero pitch and roll.

Run from the repository root:

```bash
uv run skills/geo-sleuth/scripts/measure.py range \
  --input examples/workflow-lab/measurement/range.json --out /tmp/range-result.json
uv run skills/geo-sleuth/scripts/measure.py shadow \
  --input examples/workflow-lab/measurement/shadow.json --out /tmp/shadow-result.json
uv run skills/geo-sleuth/tests/test_measure.py
```

These fixtures verify implementation and refusal behavior. They do not validate
real-image endpoint selection, camera calibration, an actual location, or the
uncertainty reported by a historical case study.
