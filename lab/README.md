# Isolated DSH workflow lab

The launcher requires Linux (including WSL2) because it verifies process ownership through `/proc`. It was tested with DSH 0.1.5-rc.1 and uses that installation's profile and preset layout; other versions may require adaptation. The measurement and street-image scripts are independent of this optional DSH launcher.

The lab uses separate `geo-lab-headless` and `geo-lab-web` profiles under `~/work/geo-sleuth-lab/dsh-home`. Ordinary DSH profiles, configuration and sessions are not edited. Only this checkout's `skills/` root is enabled. Profiles alone are not enough to isolate DSH global overlays and storage, so every command sets the separate `DSH_HOME`.

From this checkout:

```bash
python3 lab/dsh_lab.py init
python3 lab/dsh_lab.py start-web --port 3097
python3 lab/dsh_lab.py verify
python3 lab/dsh_lab.py open-web
```

`init` copies the installed DSH templates and creates the lab-owned overlays. It regenerates these overlays when repeated, so apply any experimental model override after initialization. The web preset is `geo-lab`; new sessions use `~/work/geo-sleuth-lab/workspace` as their writable workspace. The skill is read from this checkout. Write experiment outputs inside that workspace; DSH's default workspace sandbox cannot write elsewhere. UV and Hugging Face caches also live there. DSH discovers `geo-sleuth` through its skill provider, not by a custom system-prompt paste.

`verify` authenticates locally, creates a blank session and checks that the catalog contains exactly the model-invocable `geo-sleuth` skill. It sends no model prompt and does not verify model credentials. The separate behavioral test does that. `open-web` opens the authenticated local URL in the configured desktop browser without printing its token. If no browser is configured, the local launch URL is in `~/work/geo-sleuth-lab/web.private.log`; keep this private file out of version control.

For one actual model task:

```bash
python3 lab/dsh_lab.py headless "/geo-sleuth Use the measurement workflow on $PWD/examples/workflow-lab/measurement/range.json; save the result in this session workspace and explain its assumptions."
```

The installed adapter reads `DEEPSEEK_API_KEY` from the inherited environment. No credential files are copied or printed. The installed DSH version used for this lab is recorded in `lab.json`; its current default model/provider are `deepseek-flash` / `deepseek-official`. Model behavior and availability are separate from skill discovery. `DEEPSEEK_BASE_URL`, if set, is also inherited.

Stop only the lab web process with:

```bash
python3 lab/dsh_lab.py stop-web
```

The launcher records the process start identifier and profile before stopping it. It never stops another DSH profile. `--state-root <directory>` selects another lab state directory; run `init` there first. Runtime state, tokens, private logs and run artifacts stay outside the repository.

Validation results and remaining limits are in [the lab report](../docs/workflow-lab-report.md).

Repeat the offline checks from the repository root:

```bash
python3 -m pytest skills/geo-sleuth/tests/test_workflow.py skills/geo-sleuth/tests/test_measure.py skills/geo-sleuth/tests/test_street.py -q
uv run skills/geo-sleuth/tests/test_runtime.py
uv run skills/geo-sleuth/tests/test_board.py
uv run skills/geo-sleuth/tests/test_infra_board.py
uv run skills/geo-sleuth/tests/test_osm_intersections.py
uv run skills/geo-sleuth/tests/test_regions.py
```

These checks do not download ML models or send image queries to live services. Street-provider reachability and the headless model test are separate checks described in the report.
