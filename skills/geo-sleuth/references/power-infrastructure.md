# Power infrastructure: observation, search and cross-validation

Read when pylons, overhead transmission lines, bundled conductors or a crossing with a road, railway or river are visible. These features can generate candidates **and** test a candidate found through another clue. A tower must not disappear from the investigation just because a mountain or building looks convincing.

## 1. Inventory the image before choosing a location

Inspect the original frame and an ordinary enlarged crop. Record each distinct locating constraint with `board.py clue --kind infra --status observed --file <crop> --source <image region>`. Describe what is visible separately from its interpretation:

| Observation | Record | Do not silently infer |
|---|---|---|
| Tower structure | Lattice/monopole; silhouette and crossarm arrangement; suspension or angle/tension appearance if discernible | Exact voltage or a unique tower design from apparent height alone |
| Conductors | Sub-conductors **in one phase bundle**; spacer arrangement; count or alternatives such as `{4,6}` | Number of phases, circuits, earth wires or overlapping lines = bundle count |
| Parallel structures | Single tower, two separate towers side by side, successive towers along a line; left/right order | Two towers = two circuits on one tower |
| Spatial relationship | Which line crosses which road; before/after a bend; tower on each side; skyline, road and tower order | An apparent crossing in the photo = a mapped physical crossing |
| Image limits | Motion blur, occlusion, compression, insufficient pixels, possible mirror/zoom | Upscaling reveals wires that were not resolved in the original |

Use a second crop/visible span to check a bundle count. If it remains ambiguous, keep all plausible counts or `unknown`; preserve the crossing/tower clues even when voltage cannot be estimated. Image generation or generative enhancement is not measurement evidence.

Each `infra/observed` clue should describe a visible object or spatial relationship that a candidate can be checked against. Attach unreadable voltage/bundle details as limitations of that clue. A separate statement such as "wires are too blurred to count" belongs under `image-quality`, not a new infrastructure constraint requiring a positive match. For an unclassified faint shape, record its shape as a visual observation and its possible tower identity as `inferred`; do not upgrade that identity merely to satisfy the board.

After `board.py init`, record the frame review:

```bash
uv run scripts/board.py inspect --infra present --file photo.jpg \
  --why 'A lattice pylon is visible left of the ridge; conductor count unresolved'
uv run scripts/board.py clue 'Pylon left of ridge, below the road bend' \
  --kind infra --status observed --file tower-crop.png --source 'left midground'
```

`inspect --infra absent` means the agent actually inspected the frame and found no locating infrastructure, with a file and reason. `unclear` records a visibility limitation. Neither an omitted inspection nor `unclear` means absent. The script audits these records; it does not detect towers in pixels.

## 2. Use bundle count to form voltage hypotheses

For mainland-China transmission scenes, four sub-conductors make 500 kV a useful hypothesis to investigate, but this is not a one-to-one classifier. Six sub-conductors also do not uniquely imply 750 kV or a particular DC voltage. Verify the **local line section**, its AC/DC arrangement, circuit layout, insulators and dated engineering/map information. Keep the voltage claim as `inferred` until supported by a source for that line.

Two documented counterexamples to a fixed lookup table:

- The 500 kV Huimao AC line used six bundled sub-conductors in its upgrade: [National Energy Administration, 2012](https://www.nea.gov.cn/2012-04/27/c_131554827.htm).
- A section of the 220 kV Weizhu line relocation uses four bundled sub-conductors: [Shapingba transport authority project notice, 2024](https://www.cqspb.gov.cn/bm/qjtj_63709/jczwgk/zdjsxm/ssxx/sgxx/202409/t20240903_13589113.html).

These establish exceptions, not a complete classifier. Similar tower silhouettes elsewhere are reference examples, not proof of the photographed line's voltage. Do not assign a minimum voltage merely because spacers are visible; bundles can also increase current capacity at lower voltages ([OSM wires documentation](https://wiki.openstreetmap.org/wiki/Key:wires)).

OSM fields have different meanings:

| Field | Meaning and use |
|---|---|
| `wires` | Sub-conductors per bundle, e.g. `single`, `double`, `quad`; inspect unusual values rather than coercing them |
| `cables` | Electrically separated phase/pole conductors, possibly bundled; `cables=6` does **not** mean six-way bundling |
| `circuits` | Number of circuits; separate from the number of physical towers |
| `voltage` | Nominal voltage in volts; preserve semicolon-separated values on mixed lines |
| `frequency` | Useful AC/DC context when present; absent is unknown |
| `name`, `ref`, way ID | Trace the actual line/section and its provenance |

See [OSM cables](https://wiki.openstreetmap.org/wiki/Key:cables) and [voltage](https://wiki.openstreetmap.org/wiki/Key:voltage). Missing or stale tags are not a visual mismatch. [OpenInfraMap displays OSM data](https://openinframap.org/about); checking both is one map-data source, not independent confirmation.

## 3. Turn a journey corridor into discrete crossing candidates

When a journey is supplied, treat it as a hypothesis unless independently established. Keep plausible roads/branches and directions. Bound the search to manageable corridor sections and check map coverage. For a transmission line crossing an expressway, start with all mapped voltage classes:

```bash
uv run scripts/osm.py intersect --bbox <s,w,n,e> \
  --a '["highway"~"^(motorway|trunk)$"]' --b '["power"="line"]' \
  --cluster 80 --out crossings.json --report crossings-report.json
uv run scripts/board.py add --from crossings.json --level area --radius 500
uv run scripts/tiles.py sheet --points crossings.json --zoom 18 --out crossings-sheet.jpg
```

Choose the road tags and buffer for the actual scene; include other road classes or links if their classification is uncertain. Neither the example tags nor the example radius define a universal search area.

`crossings.json` supplies search centers. The report retains each real map intersection, both ways' IDs and full tags, local line bearings, crossing angle and cluster membership. **A cluster center is not necessarily a crossing and is never automatically the camera position.** Use member coordinates and the road approach to place and test separate camera hypotheses. A mapped planar crossing does not by itself establish clearance, vertical order or visibility from the camera.

Rank rather than remove candidates on uncertain voltage/bundle details. Keep unknown-tag candidates visible alongside apparent matches. Inspect satellite/ground imagery or dated project route maps where OSM is sparse; an empty query cannot exclude that corridor.

Compare candidates using:

1. Road direction and curvature, line direction, crossing before/after the bend.
2. Towers on the two sides of the road, paired versus successive towers, relative order and spacing.
3. Tower silhouette, crossarm/circuit arrangement and bundle/spacer details that the pixels support.
4. Near/far occlusion and which side of the road the camera sees. A map bearing follows way-node order, not travel direction; consider its reverse. A perspective image angle is not directly a map-plane angle.
5. Terrain skyline, distinctive buildings/bridges, and independently derived sun bearing where available.

`osm.py intersect --bend-min ...` annotates bends; do not use `--bend-filter` on an unverified interpretation. For line-and-mountain scenes, reuse `terrain.py scan/fit`; for candidate camera geometry, reuse `geo.py bearings/frame` and `pose.py`. A known tower height may feed `measure.py range`, but a generic voltage-class tower height is an assumption, not a surveyed scale.

## 4. Check the proposed camera position, including contrary evidence

Acquire candidate ground views through [street-workflow.md](street-workflow.md), execute the existing `match.py` handoff if useful, and visually inspect invariant details. A downloaded image or ranking score does not establish a match. Preserve panorama location, view heading, capture date and projection. If streets lack coverage, use satellite geometry, terrain and suitable public ground photos; report visibility and date limits.

Write a comparison artifact for each proposed final candidate. For each visible infrastructure clue include the crop, candidate/member IDs, comparison source, concrete matching or conflicting relationship, visibility limits, and outcome. Keep discovery and confirmation separate: if the crossing generated the candidate, confirm it with terrain, buildings or ground geometry that was not already used to select it. Voltage inferred from the same bundle crop is not an independent second constraint.

Register camera hypotheses under the area/road that contains them, using their actual tested extent, before recording comparisons. Do not copy a cluster center into an asserted camera point:

```bash
uv run scripts/board.py add 'camera A' --level point --parent '<containing area or road>' \
  --bbox <south,west,north,east-of-tested-camera-hypothesis>
```

```bash
uv run scripts/board.py verify --candidate 'camera A' --clue K1 \
  --outcome matched --file candidate-a-comparison.json \
  --why 'Paired towers occupy the same side of the road and bracket the same ridge notch'
uv run scripts/board.py verify --candidate 'camera B' --clue K1 \
  --outcome contradicted --file candidate-b-comparison.json \
  --why 'Visible tower lies on the opposite side after testing both travel directions'
```

The other outcomes are `inconclusive` and `not-applicable`; both retain their reason and leave the precise-verification gate unresolved. Record missing coverage as inconclusive, never contradicted. Checks do not add LR or exclude candidates. Apply the existing evidence/exclusion rules separately; uncertain tower identification can only down-weight.

Before a precise conclusion, run `board.py check --candidate <final-camera-point> --require-verified` and `board.py report --candidate <final-camera-point> --require-verified --merge result.json`. The point must be first at its level; update the evidence if the ranking disagrees. Inspect the candidate-specific audit. A linked computation (`--resolves`), marking a clue used, or checking another candidate cannot substitute for this comparison. File/input changes invalidate the affected recorded checks. `infrastructure_ready` only means this workflow has been reviewed; the skill's independent-constraint, ground-match and error-radius requirements still apply. A blocked gate permits an honest broader report without `--require-verified`.

Strict report checks the merged result's `lat`, `lon` and `radius_m` against the selected candidate before writing. A stated radius of at most 100 m requires a point with a valid bounding box or center; a road's review cannot certify it. A known coordinate mismatch or skipped candidate level blocks the merge. These checks establish consistency of the exported claim, not the physical correctness of the radius. Strict failures leave an existing result file unchanged: do not consume that older file as a successful new report.

Register input clues with `--depends-on` and shared sources with `--group` as described in [search review](search-review.md). When a date or other premise changes, use `board.py revise`, revisit derived solar/weather filters, exclusions and comparisons, then recompute and record fresh evidence and checks. The board traces explicitly registered dependencies and reopens stale exclusions; it cannot automatically identify every scientific dependency. In particular, sun azimuth plus 180 degrees is a reversed ray, not a general camera-heading formula; use the sun's image position and camera geometry.

## 5. Lessons extracted from case narratives

These are generalized workflow scenarios, not published photographs or GPS-ground-truth benchmarks:

- **Hill scene with a sun and towers:** follow the line, distinguish side-by-side towers from successive towers, then test terrain and a hilltop structure. Corrected capture dates require a fresh solar/weather branch.
- **Road, bridge, masts and waterway:** use left/right order, relative angles and river shape together. A promising visual resemblance remains a candidate while relationships are unresolved.
- **Street fixture found a neighborhood:** confirm with a distinct roof/building feature withheld from the discovery search.
- **Multiple buildings used as distance references:** one miscounted floor total can distort a range; compare several independently checked objects rather than treating repeated calculations of one height as independent support.

Regression scenarios should include an omitted visible tower, ambiguous four/six bundles, missing map voltage, a correct-voltage/wrong-crossing candidate, a mismatched tower pair, changed capture date, and a line match contradicted by terrain. A remembered highway-crossing success without its source image and ground truth is a scenario to reconstruct, not an accuracy result.
