# Street-image acquisition and comparison workflow

Use this branch after a small set of candidate camera positions exists. It acquires images, tests whether their pixels are usable, and hands a **current-run image directory** to the existing visual ranker. It does not decide which location is correct.

## 1. Record the comparison question

Specify candidate IDs, WGS84 latitude/longitude, provider order, search radius and the view to compare. A candidate coordinate is a proposed camera/search position, not automatically the location of a photographed object. State which visible structures should agree: for example, road curvature, building order and a distinctive roof intersection. Keep those observations independent of the image retrieval status.

For a compass-directed view use `heading_deg`; to look at a target point use `toward`. Supply exactly one. Heading is clockwise from true north, pitch is positive upward, and `hfov_deg` is **horizontal**. The Baidu adapter converts it to that renderer's vertical convention; Google uses horizontal FOV.

```json
{
  "candidates": [{"id": "candidate-a", "lat": 35.6595, "lon": 139.7005}],
  "providers": ["gsv", "mapillary"],
  "view": {"heading_deg": 0, "hfov_deg": 90, "pitch_deg": 0},
  "radius_m": 50,
  "width": 640,
  "height": 480,
  "query_image": "query.jpg"
}
```

`query_image` is optional; paths resolve relative to the request JSON. The image stays local; the acquisition commands send only candidate coordinates and view settings. Without a query image, acquisition still writes images and a manifest but does not construct a ranking command.

Alternatively replace `view` with:

```json
{"toward": {"lat": 35.6600, "lon": 139.7010}, "hfov_deg": 90, "pitch_deg": 0, "heading_tolerance_deg": 30}
```

Bounds: 1–10 candidates with distinct IDs; 1–3 distinct providers in order; radius 1–500 m; dimensions 64–1024 pixels; horizontal FOV 10–120°; pitch −60–60°. Missing view direction returns `needs_input`; do not invent north as a measured direction. Omitted FOV/pitch/dimensions/radius use the explicit defaults above and are recorded in the result.

## 2. Fetch and inspect the manifest

```bash
uv run "$CLAUDE_SKILL_DIR/scripts/street.py" fetch --input request.json --out-dir street-run
```

Read `street-run/manifest.json` before running the matcher. It uses the shared `schema_version`, `workflow`, `status`, `result`, `assumptions`, `warnings`, `evidence` contract. Exit codes are 0 for `ok`/`partial`, 2 for `invalid`/`needs_input`, 3 for `unavailable`.

| Status | Meaning | Agent action |
|---|---|---|
| `ok` | Every candidate has one image passing the pixel gate | Inspect metadata and images; then compare |
| `partial` | Some candidates have a usable image | Compare the available subset; explicitly retain untested candidates |
| `unavailable` | No usable image, or artifacts could not be written | Read each failure, try a feasible alternative or report the coverage gap |
| `needs_input` | Required direction/query data are missing or unusable | Obtain that input or continue with another clue |
| `invalid` | The JSON values violate the input contract | Correct the named field before retrying |

The result contains `images`, `attempts`, per-candidate status, `valid_image_dir`, and `match_handoff`. Every accepted file records its SHA-256, provider ID, source camera coordinates when available, projection, heading, pitch/FOV when known, capture date or an explicit unknown value, dimensions and pixel statistics. Request/query fingerprints permit auditing. The provider position can differ from the requested coordinate within the search radius: review that offset before claiming geometric agreement.

The gate rejects empty, undecodable, smaller than 64 pixels on either axis, and constant/near-constant images. This catches the legacy renderers' gray placeholders. Passing this gate does **not** establish that an image depicts a street, that it has the right direction, or that it matches the query; inspect the image.

Each invocation creates a fresh `runs/<id>/images` directory. A complete immutable `runs/<id>/manifest.json` retains this run's provenance and request/query fingerprints. The root manifest is replaced atomically with the same content and points only to the latest run. Old runs and their manifests remain available for auditing but never enter the generated matching command. Failed provider cache files are kept outside the valid image directory. Accepted images are encoded and hashed in a temporary file outside that directory, then atomically renamed into it; a partial output write cannot contaminate the matcher's input set.

## 3. Follow provider fallback without changing the hypothesis

For each candidate, providers run in the declared order until one usable perspective image is obtained. There are at most three image download attempts per candidate across providers. Google/Baidu use the existing nearest-panorama/render adapters and inherit their endpoint reliability and request timeouts. Their web endpoints can fail independently of whether the location has coverage.

- `service_error` is a request/service failure; error bodies and credential-bearing URLs are not printed.
- `lookup_empty_or_failed` is deliberately ambiguous: the existing Baidu lookup collapses transport failures and empty responses; the existing Google lookup also returns an empty result for some unexpected response layouts. Neither value establishes absence of coverage.
- `no_coverage_in_bounded_search` means Mapillary returned an empty data page; it is limited to that bounded query.
- `constant_or_near_constant_image` indicates unusable pixels, including gray placeholder renders.
- `outside_requested_radius` means the returned camera is too far from the requested position.
- `artifact_write_failed` is a local output/storage failure. It does not retry network providers for that candidate and is separate from service availability.
- `missing_token` means Mapillary was not queried. Supply `MAPILLARY_TOKEN` through the environment, never in the JSON, prompt, saved URL or report.

Mapillary requests one page of at most 20 results, with at most three image downloads subject to the shared budget. It accepts only native `perspective` images; spherical, fisheye and unknown projections require a separate verified rectification workflow. It filters the **actual capture heading**, preserving 0°, against the requested heading or the bearing from the camera toward the target. The default heading tolerance is 30° (configurable 1–90°). Native images are not relabeled as rendered views: their pitch/FOV remain unknown. Heading agreement is only a coarse filter and does not prove that a target lies inside the image or is unoccluded. A bounded page without eligible images is not exhaustive absence of coverage. The API metadata definitions are documented by the [Mapillary SDK](https://mapillary.github.io/mapillary-python-sdk/docs/mapillary.config.api/mapillary.config.api.entities/).

If all providers fail, do not run the ranker and do not exclude the location. Report the exact availability status; use a browser or another independent source only if appropriate and record its provenance separately.

## 4. Compare images and update the case

If `match_handoff` is present, its `argv`/shell-quoted `command` calls the existing ranker:

```bash
uv run "$CLAUDE_SKILL_DIR/scripts/match.py" rank --query query.jpg \
  --images /absolute/current-run/images --out ranking.json --sheet comparison.jpg
```

This is a handoff, not an executed step. `match.py` may download its existing ML model on first use. Review `ranking.json` and the contact sheet. A DINO cosine rank is an ordering, **not** a confidence probability. SIFT inliers help confirm repeated geometry but low counts do not exclude a candidate when viewpoint, resolution or date differ. Check at least two spatial relationships, camera direction and capture date. State any decisive contradiction explicitly.

Only then update the existing candidate board with the observed comparison and its source file. Avoid counting the downloaded image, its embedding rank and SIFT output as three independent evidence sources. None of the retrieval statuses automatically adds evidence/LR or a geographic exclusion.

## Local images and offline reproduction

For manually obtained, authorized images, use the same gate with explicit projection:

```json
{
  "images": [{"id": "manual-a", "path": "candidate.jpg", "projection": "perspective", "capture_date": null}],
  "query_image": "query.jpg"
}
```

```bash
uv run "$CLAUDE_SKILL_DIR/scripts/street.py" validate --input local.json --out-dir checked
```

Optional metadata fields are `heading_deg`, `pitch_deg`, `hfov_deg`, `wgs84_lat_lon` and `capture_date`; missing values stay unknown. An unrectified panorama must not be labeled perspective merely to pass the gate. Original and copied-image fingerprints are preserved. Offline mock/fixture checks live outside the skill in `examples/workflow-lab/street/` and in `tests/test_street.py`; they validate workflow behavior, not live-provider availability or real-location accuracy.
