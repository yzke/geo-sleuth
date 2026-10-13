# Measurement workflow

Use this workflow when a photograph contains an object with a defensible known
size, or a vertical object whose foot, top, and shadow tip can be measured.
Read the photograph first. Record what was measured separately from what was
assumed. A computed number does not validate its inputs.

## Agent procedure

1. Work in one pixel coordinate frame: the displayed image after EXIF rotation.
   Keep its dimensions, crop/resize history, camera calibration source, and the
   source of every physical dimension. Save annotations separately from the
   original image. Never measure an AI-restored edge as if it were original.
2. Select a model. `range` estimates optical-axis depth for a known dimension
   parallel to the image plane. `shadow` reconstructs two ground intersections
   and a vertical object's top from calibrated image rays. It requires a level
   ground plane plus known camera height, pitch, and roll.
3. Write a JSON request. Use `[lower, upper]` for uncertain numerical inputs;
   give an explanation in the corresponding source field. Unknown focal length,
   crop history, posture, or ground slope is missing information, not zero.
4. Run the script with `--out` and inspect the structured status, assumptions,
   warnings, evidence references, and returned bounds before drawing a map.
5. Use the returned depth or elevation as a conditional constraint. For shadow
   results, pass the solar-elevation interval to the existing `sun.py` workflow
   together with a dated, timezone-aware capture-time interval. A shadow ray
   yields a solar direction relative to the camera; absolute azimuth requires
   a separately established camera bearing.
6. Compare the conditional result with independent anchors. One pixel/geometry
   inference chain is one evidence source. Do not automatically create a high
   likelihood ratio, exclude a candidate, or claim a confirmed location.

```bash
uv run "$S/measure.py" range --input request.json --out range-result.json
uv run "$S/measure.py" shadow --input request.json --out shadow-result.json
```

## Request format

Every request has `image` and `camera` objects. Dimensions and all pixel points
refer to the displayed image **after EXIF orientation is applied**. Pixel x
increases rightward and y downward. Numerical measurements accept either a
number or `[lower, upper]`; image dimensions must be positive integers.

```json
{
  "image": {
    "width": 1600, "height": 1200,
    "crop": "none",
    "source": "Original image; dimensions after EXIF orientation",
    "geometry": "rectilinear_square_pixels",
    "pixel_frame": "exif_transposed"
  },
  "camera": {
    "focal_px": [1190, 1210],
    "focal_source": "Calibration on the current image with stated uncertainty",
    "calibration_frame": "current_image"
  },
  "target": {
    "size_m": [1.98, 2.02], "size_px": [118, 122],
    "size_source": "Measured physical dimension, including tolerance",
    "pixel_source": "Endpoints marked on the displayed image",
    "geometry": "frontoparallel"
  }
}
```

`image.path` is optional. Relative paths resolve against the request file's
directory. A supplied image is decoded, EXIF-transposed, checked against the
declared dimensions, and SHA-256 fingerprinted. Pixel-marking semantics are
always unverified: the script does not inspect whether a person selected the
correct endpoints. Without the image, the result is `partial` and explicitly
warns that source pixels could not be checked.

Use exactly one focal input. `focal_px` requires
`camera.calibration_frame: "current_image"`. Alternatively supply
`camera.equiv_focal_mm` and `focal_source`, with `image.crop: "none"`; conversion
uses the actual image diagonal and the 36 × 24 mm reference diagonal. An
uncertain crop requires a current-image calibration. Non-uniform resizing,
uncorrected fisheye/panorama projection, and residual distortion are outside
this model.

For `shadow`, replace `target` with `ground`, `object`, and `points`, and add the
camera pose. This complete request uses a level synthetic scene; its object
top is 2 m high and its ground shadow is 5 m long:

```json
{
  "image": {
    "width": 1600, "height": 1200,
    "crop": "none",
    "source": "Synthetic full sensor frame",
    "geometry": "rectilinear_square_pixels",
    "pixel_frame": "exif_transposed"
  },
  "ground": "horizontal",
  "object": {
    "height_m": 2,
    "height_source": "Independently measured vertical object",
    "geometry": "vertical"
  },
  "points": {
    "foot": [980, 696],
    "head": [980, 576],
    "shadow_tip": [944, 676.8],
    "source": "Pixel coordinates of foot, top and shadow tip"
  },
  "camera": {
    "focal_px": 1200,
    "focal_source": "Current image calibration",
    "calibration_frame": "current_image",
    "principal_point_px": [800, 600],
    "principal_point_source": "Current image calibration",
    "height_m": 1.6,
    "height_source": "Camera optical-center height above the ground",
    "pitch_deg": 0,
    "pitch_source": "Measured attitude; zero is justified here",
    "roll_deg": 0,
    "roll_source": "Measured attitude; zero is justified here"
  }
}
```

This example places the object foot at `(3, 0, 20)` and tip at `(3, 0, 25)` in world right/up/forward
coordinates, so their x image coordinates differ. The runnable fixtures include independently generated
off-axis coordinates with nonzero pitch and roll; use those for regression
tests rather than copying rounded measurements.

Positive pitch points the camera above the horizon; positive roll rotates its
right axis toward the ground when pitch is zero. Camera height is the optical
center's height above the same ground plane as the object and shadow. A point
can include bounds, for example `"foot": [[978, 982], [694, 698]]`.

The default top reprojection tolerance is 3 pixels; an explicit scalar
`head_tolerance_px` may be set in `(0, 50]` when justified by marking quality.
Do not enlarge it just to make a desired location fit. The nominal geometry
must satisfy this check. Ground rays at or below 1 degree of depression trigger
`needs_input`, including when any evaluated sensitivity sample crosses that
threshold.

## Outputs and interpretation

`range` returns `result.depth_m`, `nominal_depth_m`, and `focal_px`. The depth
bounds follow monotonic interval arithmetic under the stated target model.

`shadow` returns `result.solar_elevation_deg`, `shadow_length_m`, and a
`nominal` object with head residual, object ground range, and solar bearing
relative to camera forward. Absolute compass azimuth is not determined.
`result.sampling` records the method, seed, evaluated and accepted samples,
and head residual range. Rejected top/foot combinations remain visible as a
warning. The elevation envelope covers the accepted evaluated samples only;
it is not a guaranteed interval over all input combinations.

`result.sensitivity.ranked_parameters` varies each uncertain scalar between
its endpoints with other inputs held at their midpoints. It ranks the resulting
solar-elevation spans, records accepted/rejected endpoints, and identifies
which supplied uncertainties matter most. Prioritize improving those inputs
before increasing geographic precision. A parameter's rank depends on the
width supplied in this request. A zero span with rejected endpoints does not
prove that parameter is unimportant; resolve the inconsistent geometry first.

At most ten varying scalars use all corners, the midpoint, and 256 seeded
uniform samples. Larger requests use 1,024 seeded samples, marginal endpoints,
and the midpoint. Neither scheme assumes a probability distribution for the
real measurement error. Save the entire request and result when using this
constraint in a case; the result fingerprints the request.

## State transitions

| Status | Agent action |
|---|---|
| `ok` | Inspect assumptions and continue with the conditional constraint. |
| `partial` | Retain only the usable result; preserve every warning and uncertainty. |
| `needs_input` | Read the missing requirement, acquire it or use another independent clue. |
| `invalid` | Correct the request format, coordinate frame, or inconsistent geometry. |
| `unavailable` | Record the unavailable dependency; do not interpret it as geographic evidence. |

Exit code 0 means a usable result, 2 means the request needs correction or more
information, and 3 means a dependency was unavailable. Preserve the JSON even
when the command exits nonzero.

## Limits that matter

- Range is depth along the optical axis. It is not automatically ground range
  or camera-to-object slant distance. A tilted, partly hidden, or unknown-size
  target does not satisfy the frontoparallel-size model.
- A 35 mm equivalent focal length describes an original camera frame. An
  unknown crop or digital zoom invalidates a conversion based only on image
  dimensions. A calibration for the current image can provide pixel focal
  length directly, including known resizing or cropping.
- Shadow inversion is ill-conditioned near the horizon. Visible top/foot
  consistency must be checked before accepting the ground reconstruction.
  Unknown pitch or roll must not silently become a level camera.
- Parallel shadows on the ground need not look parallel in a photograph;
  their image lines converge toward a vanishing point under perspective.
- A sampled sensitivity envelope is not a confidence interval and is not a
  guaranteed bound over every combination of inputs. Broaden uncertain inputs,
  examine residuals, and keep model error separate from pixel uncertainty.
- Numeric-only requests cannot verify the photograph or its pixel markings.
  Attaching the source image permits dimension and fingerprint checks, but
  does not prove that the selected points are semantically correct.

Use the synthetic requests under `examples/workflow-lab/measurement/` in the
repository for reproducible checks. They are test fixtures, not real-location
ground truth.
