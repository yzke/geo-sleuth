# Street workflow reproduction

The prior render helpers can return a decoded, full-size gray image when retrieval fails. A file's existence therefore cannot justify visual comparison. The general change adds a pixel gate, per-provider failures, current-run manifests, and a conditional handoff to the existing matcher. Existing render/ranking behavior remains available through the original scripts.

Offline before/after fixture:

```bash
python3 examples/workflow-lab/street/make_local_fixture.py /tmp/street-fixture
python3 skills/geo-sleuth/scripts/street.py validate --input /tmp/street-fixture/local.json --out-dir /tmp/street-checked
python3 -m pytest skills/geo-sleuth/tests/test_street.py -q
```

All three input files exist; one is a useful synthetic scene, one is a gray placeholder, and one is corrupt. The workflow should report `partial`, accept one image, preserve two rejection reasons, and hand only its current-run image directory to the ranker. It does not run the model. Repeating the command creates a different image directory, preventing old files from silently affecting ranking.

The tests also mock service failure vs coverage absence, gray-image provider fallback, provider-specific FOV conventions, Mapillary heading 0°, a requested target bearing, projection/date retention, missing tokens, budgets and invalid input. These test control flow and metadata handling independently of provider reachability.

`public-fetch.json` is a bounded public-location reachability smoke input, not a labeled visual-location benchmark:

```bash
python3 skills/geo-sleuth/scripts/street.py fetch --input examples/workflow-lab/street/public-fetch.json --out-dir /tmp/street-live
```

`unavailable` is an honest result if no source returns usable pixels. Mapillary is skipped with `missing_token` unless `MAPILLARY_TOKEN` is configured. Never infer that a candidate is wrong from any of these statuses. Live results and actual agent behavior belong in the lab report, separate from these deterministic tests.
