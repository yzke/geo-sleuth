# Search review: context, coverage and corrected evidence

Use when creating a candidate pool, when searches repeatedly fail, or when a premise changes. A ranking only compares candidates already on the board. Its normalized share is not the probability that the true location is in that pool.

## 1. Carry applicable context into this case

Review the user's supplied location/time hints and explicitly associated images before proposing regions. Distinguish the same location, the same trip and merely the same photographer. Adjacent messages alone do not establish a shared location. A usual activity region is a soft prior that merits an early check, not a fact about where this photo was taken.

Keep context in the local case board; do not put personal histories, private photo sets or individual travel patterns in the shared skill or region pack. Use a minimal source description rather than copying a private conversation.

```bash
uv run scripts/board.py context --region 'Region A' --region 'Region B' \
  --source 'User-provided context for this case' --relation same-trip \
  --why 'The user explicitly associated this image with the same trip'
```

The command returns a context ID such as `C1`. `--region` uses exact candidate labels and may precede adding those candidates. Available relations are `same-case`, `same-trip`, `same-photographer` and `unconfirmed`. The first three create regions to review; `unconfirmed` preserves the lead as a warning without treating it as an applicable regional obligation. None changes a candidate's score. “Candidate 0” means an early test, not a forced winner.

When the review finds no applicable context:

```bash
uv run scripts/board.py context --none --why 'No applicable context was supplied for this photo'
```

If a hint is corrected or belongs to a different photo, record the replacement with `--supersedes C1` and explain why. Repeat the flag to replace multiple records. `--none` cannot silently discard active regional context: explicitly supersede it. Replacing the case photograph also requires a context review. The board checks recorded links, not the truth of the person's statement or the association itself.

## 2. Record what was searched, including failures

Give each applicable context region an initial scene query before allowing weak visual inferences to push it out of consideration. Also retain plausible visual alternatives. Compare region × feature × method: repeatedly changing place names within one favored area does not test a different geographic hypothesis; repeating the same object description does not test a different feature.

```bash
uv run scripts/board.py search --candidate 'Region A' --method image \
  --feature 'fields, village houses and turbine alignment' \
  --query 'Region A farmland village wind turbines' --outcome no-match \
  --file region-a-image-search.json \
  --why 'Reviewed the saved results; none established the photographed layout'
uv run scripts/board.py search --candidate 'Region B' --method map \
  --feature 'waterway, bridge and field-road layout' \
  --query 'Waterways and roads within the candidate extent' --outcome unavailable \
  --why 'The map service failed before returning usable data'
```

Methods are `image`, `map`, `web`, `street`, and `other`. Outcomes are `found`, `no-match`, `unavailable`, and `deferred`. `found` and `no-match` require an existing, nonempty saved result file; its fingerprint is recorded. Preserve screenshots, query text, returned bounds and capture/retrieval dates where available. `--bbox south,west,north,east` records the actual queried extent and must overlap the candidate when geometry is known. A query for Region B cannot discharge Region A's missing check.

- `found` means a useful lead was returned, not a confirmed location. Inspect it and register actual evidence separately.
- `no-match` means the saved search did not find a match; it never excludes the candidate.
- `unavailable` and `deferred` remain incomplete checks. State the specific limitation and try a feasible alternative rather than repeating an unchanged failure.
- One completed query establishes an **initial check**, not exhaustive coverage. Missing candidates, sources and scene features can still exist. Narrow query bounds do not establish coverage of the whole administrative region.

`next`, `check`, and `report` expose declared context regions that are absent from the candidate pool or have no current completed initial query. Other unsearched candidates are warnings. Precise reporting with `--require-verified` requires the applicable context review and its initial checks. It does not require an exhaustive search of every possible place and cannot detect a region that was never declared.

When several searches fail, inspect the ledger before doing a larger scan. Try a different observed feature, method or justified region: for example, a distinctive traffic fixture instead of generic trees, or the shoreline/road layout instead of an indistinct ridge. Use local-language place-plus-scene queries as well as a query without the favored place name. A weak chain such as “dry roadside material → local industry → region” cannot justify silently dropping the untested alternatives.

## 3. Choose the scan extent for the scene

```bash
uv run scripts/board.py scene --kind rural \
  --why 'Fields and scattered buildings dominate the inspected frame'
uv run scripts/board.py scan-bbox 'Region A' --bbox <south,west,north,east> \
  --scope farmland --why 'Field roads and drainage channels match the visible scene type'
```

Scene kinds are `urban`, `rural`, `coastal`, `mountain`, and `unknown`. Scan scopes are `urban`, `farmland`, `waterway`, `corridor`, `overview`, and `other`. Every `scan-bbox` record requires both `--scope` and `--why`; a narrowing needs a reason grounded in the visible scene or a tested constraint. Changing the scene kind archives existing narrowed scan extents and restores candidate extents until the scan scope is reviewed again. Choose:

| Scene | Useful initial extent |
|---|---|
| Urban streets and dense buildings | Built-up extent, using `board.py urban` when appropriate |
| Fields, scattered houses, turbines or pylons | Farmland, field roads and the relevant facility corridors |
| Estuary, coast, levee or canal | Shoreline/waterway, levee roads and bridges |
| Mountain road or railway | Route corridor and terrain on both sides |
| Unclear or mixed scene | Wider overview; keep alternatives until the scene supports narrowing |

`urban` queries built-up land use, not every inhabitable part of a region. It must not be the automatic next step for a rural or unknown scene. Scan extents set work priorities; a successful built-up-area lookup does not eliminate fields outside it. Compare relative structure—road and channel directions, crossing order, tower alignment, houses between camera and facilities—rather than counts or visual similarity alone.

OSM coverage checks assess that dataset, not whether the geographic candidate has been disproved. Facility counts must use comparable bounds, dates, feature definitions and deduplication before comparison. OpenInfraMap and OSM do not provide independent support for the same mapped object.

## 4. Correct premises and avoid repeated scoring

Declare scientific inputs with repeatable `--depends-on` and shared sources with `--group` when adding clues. `--resolves` also registers the tested clue as a dependency, but other inputs such as the capture time still need explicit links. The board does not infer a dependency just because two descriptions mention the same date, object or source.

```bash
uv run scripts/board.py clue 'Capture time reported by the source' \
  --kind hint --status inferred --file time-source.json --group capture-time
# Use the returned ID, shown as K1 here.
uv run scripts/board.py clue 'Solar-angle test for the stated time window' \
  --kind sun --status computed --file sun-test.json --depends-on K1
uv run scripts/board.py revise K1 --text 'Corrected capture-time window' \
  --file corrected-time-source.json --why 'The source corrected the original time'
```

Recompute the solar test, then revise the derived clue with its new output and reason before recording fresh evidence or exclusions. Apply the same sequence to a corrected floor count, object height, focal length or crop. Repeating a calculation with the same uncertain height is still one evidence chain.

The board tracks registered dependencies transitively. A changed premise makes dependent records stale; their scores cannot be reused, and stale exclusions reopen while keeping their history. A fresh calculation alone does not restore an old likelihood ratio: inspect it and record the new comparison. Infrastructure checks also need renewed candidate-specific artifacts when their inputs change.

For a given candidate and clue, only the latest evidence row contributes. Clues with a shared ancestor or an explicit shared group contribute at most one same-direction score, using the strongest contribution; this is a conservative scoring rule, not a learned probability model. Opposing contributions in one chain require reconciliation and block a precise claim. Assign different groups only when the measurements or sources are genuinely independent; independent websites copying one map or report remain a single source chain.

Use `board.py check` to inspect stale inputs and unresolved groups. `check --require-verified` and `report --require-verified` block a precise claim while these review conditions remain unresolved. Review unregistered dependencies manually: the script cannot automatically recognize every scientific premise.

## 5. Weather and phenology comparisons

Record capture time, timezone and uncertainty separately from upload time. Compare each candidate against weather information for the same time window and area, retaining source, station/grid resolution, observation versus forecast, and missing data. A daily “rain” or “overcast” summary cannot rule out a brief sunlit photograph; a weather station and distant cloud patch do not necessarily describe the camera's sky. Service failure or absent archives is unknown, not a mismatch.

For flowers, crops or leaf state, retain uncertain species identification, cultivar, planting/harvest practices, elevation and date. Use plausible alternatives and seek a separate structural confirmation. A plant identification or a broad climate tendency alone cannot justify excluding an entire region. Corrected dates require a fresh weather/phenology review and revision of registered dependent clues.
