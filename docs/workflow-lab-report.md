# Measurement and street-image workflow lab

This fork extends [Oldcircle/geo-sleuth](https://github.com/Oldcircle/geo-sleuth) from development base `b5419379c1936d6ee07283950fa3ac1fb81563e8`, integrated with upstream `418aea2` before publication. It adds two bounded workflows to the existing Agent Skill. The implementation is maintained in [yzke/geo-sleuth](https://github.com/yzke/geo-sleuth) for further testing before an upstream contribution.

## Design and scope

The skill selects the branch and explains the next action. Python scripts validate inputs and produce reproducible artifacts. Existing `sun.py`, `pose.py`, `match.py` and the candidate board retain their responsibilities. The additions do not assign evidence likelihood ratios or eliminate geographic candidates automatically.

The shared JSON contract distinguishes `ok`, `partial`, `needs_input`, `invalid` and `unavailable`. An error must tell the agent whether to obtain a missing measurement, repair the request or try another source. Every calculated range is conditional on stated camera/scene assumptions. Multiple calculations using the same input are one evidence chain.

Measurement makes known-size ranging and perspective-corrected shadow reconstruction executable. It requires focal/crop provenance and distinguishes optical-axis depth from ground or slant distance. The general shadow reconstruction uses camera rays intersecting a horizontal ground plane, with consistency checks on a vertical reference object.

Street acquisition sits before the existing DINOv2/SIFT ranking. It preserves source metadata and distinguishes API failures, absent coverage and unusable images. Only accepted perspective images from the current run may enter ranking. A valid image is still not a verified scene match, and similarity is not a geographic confidence score.

## Validation log

- Upstream runtime regression after integration: 39 tests passed (`uv run skills/geo-sleuth/tests/test_runtime.py`). Network-failure messages here are mocked test cases.
- Original board regression: 13 tests passed (`uv run skills/geo-sleuth/tests/test_board.py`).
- Original region regression: 23 tests passed (`uv run skills/geo-sleuth/tests/test_regions.py`).
- Shared result contract: 5 tests and 12 subtests passed at initial implementation.
- Agent Skills frontmatter: `quick_validate.py skills/geo-sleuth` passed after entrypoint changes.

- Isolated DSH initialized successfully from installed 0.1.5-rc.1 templates; composed configuration checked without printing credentials.
- Lab web started on `http://127.0.0.1:3097`, bound to loopback. Authenticated HTML, blank session creation and the exact model-invocable `geo-sleuth` skill catalog passed. No model request was sent by this check.
- Live Baidu acquisition returned a valid 640x480 perspective image for a bounded public street sample. The pixel gate passed; the image was opened and visually inspected. Capture date remains unknown. This is an acquisition success, not a scene-match claim.
- Live GSV acquisition at a public crossing returned a service error; Mapillary fallback reported a missing environment token. The result was `unavailable`, exit 3, with no matching handoff or geographic exclusion.

Run artifacts remain local and are not included in this repository. The default `<lab-state>` is `~/work/geo-sleuth-lab`; validation artifacts included: `verification.json`, `live-street/run/manifest.json` and `live-street/gsv-run/manifest.json`.

## Final checks

- New workflow suite: 38 tests passed, with 35 additional subtests (shared contract 5, measurement 14, street 19).
- After integrating the upstream Windows compatibility and evidence-spec validation fixes, all 75 upstream runtime/board/region tests and 38 new workflow tests passed: 113 automated tests in total. Skill frontmatter validation and `git diff --check` also passed.
- Independent read-only review found three important issues: unstructured measurement output-write failures, ambiguous Google lookup replies mislabeled as definite absence, and interrupted PNG writes entering the accepted-image directory. Each was fixed and covered by a regression. Final review found no remaining critical or important issue. A requested focal-orientation regression was also added and passed.

## Actual DSH behavioral evaluation

The first live model run invoked the range workflow correctly but exposed a lab configuration problem: the default DSH sandbox could write only under the checkout, while the requested artifact destination was elsewhere. Its artifacts were retained in `initial-agent-artifacts/`. The launcher now uses the dedicated `workspace/` under the lab state as the DSH session cwd and places UV/Hugging Face caches there. The skill remains loaded from the checkout. No global permission relaxation or ordinary-profile change was needed.

After the repair, an actual headless DSH model completed three requests in 52.1 seconds, exit 0. The prompt supplied only task descriptions and raw inputs, with no expected answers or commands. It requested offline execution and prohibited model downloads, credential access and skill-code edits.

| Task | Observed result |
|---|---|
| Known-size ranging | Invoked `measure.py range`; `partial`, 19.3131–20.7136 m optical-axis depth, nominal 20 m. Stated the model assumptions and unverified pixel markings. |
| Shadow with a missing camera attitude field | Invoked `measure.py shadow`; `needs_input`, empty result, explicitly identified missing `camera.pitch_deg`. Did not silently assume a level camera. |
| Three local street-image candidates | Invoked `street.py validate`; `partial`, accepted the real image and rejected the uniform gray and corrupt files with distinct reasons. Preserved a ranking handoff without running ML. |

An independent check verified the distance interval arithmetic, missing-input behavior, accepted directory contents, failure causes, equality of run/latest manifests, and request/image fingerprints: all eight assertions passed. The final outputs are in `<lab-state>/workspace/behavioral-results/`; the grading record is `<lab-state>/agent-behavior/grading.json`. Private model transcripts are kept outside the repository.

## Running the lab

See [lab/README.md](../lab/README.md). `geo-lab-web` was verified on loopback port 3097; `open-web` opens its authenticated URL locally. `geo-lab-headless` runs one task and exits. Both use the separate lab DSH home and only the experimental skill root.

## Limits

Synthetic geometry tests establish mathematical correctness under modeled assumptions, not measurement quality on arbitrary real photographs. Real-photo distance and location accuracy require separate calibration and held-out validation.

Provider connectivity, panorama coverage and valid pixels are separate from visual matching. Existing DINOv2/SIFT weights are not required by the offline acquisition tests; a handoff test must not be described as a completed visual match.
