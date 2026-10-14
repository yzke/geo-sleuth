"""Case-local context and declared initial-search checks, never location evidence.

An initial check proves that the operator recorded a query and retained its
artifact. It does not prove exhaustive coverage or that the result is correct.
"""
from __future__ import annotations

import copy
import math
from datetime import datetime, timezone
from pathlib import Path

from _infra import artifact

RELATIONS = ("same-case", "same-trip", "same-photographer", "unconfirmed")
METHODS = ("image", "map", "web", "street", "other")
OUTCOMES = ("found", "no-match", "unavailable", "deferred")
COMPLETED = {"found", "no-match"}
_SCOPE_KEYS = ("level", "parent", "bbox", "scan_bbox", "center", "cid", "osm_id", "country")


def register_parsers(subparsers) -> None:
    context = subparsers.add_parser("context", help="review sourced, case-local context without changing candidate scores")
    choice = context.add_mutually_exclusive_group(required=True)
    choice.add_argument("--region", action="append", help="exact candidate label to check; repeatable, may precede add")
    choice.add_argument("--none", action="store_true", help="explicitly reviewed: no applicable known context")
    context.add_argument("--source", help="source and applicability of this context; required with --region")
    context.add_argument("--why", required=True, help="why this context applies, is unconfirmed, or is absent")
    context.add_argument("--relation", choices=RELATIONS, default="same-case")
    context.add_argument("--supersedes", action="append", default=[], metavar="CONTEXT_ID",
                         help="explicitly replace an active context review; repeatable, preserves history")

    search = subparsers.add_parser("search", help="record a candidate-specific initial check, without scoring or exclusion")
    search.add_argument("--candidate", required=True, help="exact candidate label")
    search.add_argument("--method", required=True, choices=METHODS)
    search.add_argument("--feature", required=True, help="observed feature being tested; a clue kind or ID is useful")
    search.add_argument("--query", required=True, help="actual query, map request, or comparison operation")
    search.add_argument("--outcome", required=True, choices=OUTCOMES)
    search.add_argument("--file", help="existing retained result; required for found/no-match, SHA-256 recorded")
    search.add_argument("--why", required=True, help="what was checked and the limitations or failure reason")
    search.add_argument("--bbox", help="actual searched extent s,w,n,e, when applicable; not a claim of full coverage")


def _text(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty")
    return value.strip()


def _identity(board: dict, board_path: Path) -> dict:
    """Bind review to case/photo identity and available bytes, without requiring a file."""
    photo = board.get("photo", "")
    origin = board.get("photo_path") if board.get("photo_path_for") == photo else None
    path = Path(origin or photo) if origin or photo else None
    if path is not None and not path.is_absolute():
        path = board_path.resolve().parent / path
    try:
        photo_artifact = artifact(path) if path is not None and path.is_file() else None
    except OSError as error:
        # Old boards may refer to unavailable source images. Keep that state
        # readable, while never retaining a fingerprint from an earlier frame.
        photo_artifact = {"unavailable": type(error).__name__}
    return {"case": board.get("case"), "photo": photo,
            "photo_path": str(path.resolve()) if path is not None else None,
            "photo_artifact": photo_artifact}


def _active_contexts(board: dict) -> list[dict]:
    history = board.get("context_reviews", [])
    superseded = {key for row in history for key in row.get("supersedes", [])}
    return [row for row in history if row.get("id") not in superseded]


def _candidate_snapshot(board: dict, name: str) -> list[dict]:
    chain, seen = [], set()
    while name:
        if name in seen:
            raise ValueError(f"Candidate ancestry contains a cycle: {name}")
        seen.add(name)
        candidate = board.get("candidates", {}).get(name)
        if candidate is None:
            chain.append({"name": name, "missing": True})
            break
        chain.append({"name": name, "inputs": {key: copy.deepcopy(candidate.get(key)) for key in _SCOPE_KEYS}})
        name = candidate.get("parent")
    return chain


def _bbox(value) -> list[float]:
    try:
        values = value.split(",") if isinstance(value, str) else value
        if len(values) != 4:
            raise ValueError()
        box = [float(v) for v in values]
        south, west, north, east = box
        if not all(math.isfinite(v) for v in box):
            raise ValueError()
        if not (-90 <= south <= north <= 90 and -180 <= west <= east <= 180):
            raise ValueError()
        return box
    except (TypeError, ValueError):
        raise ValueError("bbox must be finite ordered s,w,n,e in latitude/longitude bounds; split antimeridian extents") from None


def _search_bbox(board: dict, name: str, value) -> list[float] | None:
    if value is None:
        return None
    box = _bbox(value)
    if box[0] == box[2] or box[1] == box[3]:
        raise ValueError("search --bbox must have nonzero area")
    candidate = board["candidates"][name]
    extent = candidate.get("bbox")
    if extent is not None:
        south, west, north, east = _bbox(extent)
        latitude_overlap = (max(south, box[0]) <= min(north, box[2]) if south == north else
                            max(south, box[0]) < min(north, box[2]))
        longitude_overlap = (max(west, box[1]) <= min(east, box[3]) if west == east else
                             max(west, box[1]) < min(east, box[3]))
        overlap = latitude_overlap and longitude_overlap
    else:
        center = candidate.get("center")
        if not isinstance(center, (list, tuple)) or len(center) != 2:
            raise ValueError("Set candidate geometry before recording a bounded search")
        lat, lon = center
        try:
            lat, lon = float(lat), float(lon)
            overlap = math.isfinite(lat) and math.isfinite(lon) and box[0] <= lat <= box[2] and box[1] <= lon <= box[3]
        except (TypeError, ValueError):
            overlap = False
    if not overlap:
        raise ValueError("Search bbox does not overlap the named candidate; record the actual candidate/scope")
    return box


def handle(args, board: dict, board_path: Path) -> str:
    """Validate fully before mutating; caller owns saving and logging the board."""
    why = _text(args.why, "--why")
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    identity = _identity(board, board_path)
    if args.cmd == "context":
        active = _active_contexts(board)
        active_ids = {row["id"] for row in active}
        supersedes = list(dict.fromkeys(getattr(args, "supersedes", []) or []))
        if not set(supersedes) <= active_ids:
            raise ValueError("--supersedes must name active context review IDs")
        regions = list(dict.fromkeys(_text(name, "--region") for name in (args.region or [])))
        no_context = bool(args.none)
        if bool(regions) == no_context:
            raise ValueError("Use either --region or --none")
        relation = args.relation
        if relation not in RELATIONS:
            raise ValueError(f"--relation must be one of {RELATIONS}")
        source = _text(args.source, "--source") if regions else (args.source or "").strip()
        retained = [row for row in active if row["id"] not in supersedes]
        if no_context and any(row.get("regions") for row in retained):
            raise ValueError("--none cannot clear active regional context; explicitly --supersedes each replaced context ID")
        # A new affirmative review replaces the earlier 'none' state semantically,
        # but the old review remains in history and requires no implicit deletion.
        record = {"id": f"C{len(board.get('context_reviews', [])) + 1}", "at": timestamp,
                  "regions": regions, "none": no_context, "source": source,
                  "why": why, "relation": relation, "supersedes": supersedes, "case_snapshot": identity}
        board.setdefault("context_reviews", []).append(record)
        return f"{record['id']}: context reviewed; sourced regions are initial-check priorities, not location evidence."
    if args.cmd != "search":
        raise ValueError(f"Unsupported search-audit command: {args.cmd}")
    name = _text(args.candidate, "--candidate")
    if name not in board.get("candidates", {}):
        raise ValueError(f"No exact candidate {name!r}; add it first or use its full board label")
    if args.method not in METHODS or args.outcome not in OUTCOMES:
        raise ValueError("Invalid search method or outcome")
    feature = _text(args.feature, "--feature")
    query = _text(args.query, "--query")
    file = getattr(args, "file", None)
    if args.outcome in COMPLETED and not file:
        raise ValueError("found/no-match require --file with the retained query result")
    saved_artifact = artifact(file) if file else None
    if args.outcome in COMPLETED and not saved_artifact["bytes"]:
        raise ValueError("A completed initial check needs a nonempty retained result")
    box = _search_bbox(board, name, getattr(args, "bbox", None))
    snapshot = _candidate_snapshot(board, name)
    record = {"id": f"S{len(board.get('search_checks', [])) + 1}", "at": timestamp,
              "candidate": name, "method": args.method, "feature": feature, "query": query,
              "outcome": args.outcome, "why": why, "bbox": box, "artifact": saved_artifact,
              "candidate_snapshot": snapshot, "case_snapshot": identity}
    board.setdefault("search_checks", []).append(record)
    return f"{record['id']}: {name} {args.outcome}; records an initial check only, with no scoring or exclusion."


def _query_problems(board: dict, identity: dict, record: dict, file_cache: dict) -> list[str]:
    issues = []
    name = record.get("candidate")
    if name not in board.get("candidates", {}):
        issues.append("candidate no longer exists")
    else:
        try:
            if record.get("candidate_snapshot") != _candidate_snapshot(board, name):
                issues.append("candidate geometry, identity or ancestry changed")
        except ValueError as error:
            issues.append(str(error))
    if record.get("case_snapshot") != identity:
        issues.append("case/photo changed")
    if record.get("outcome") in COMPLETED:
        saved = record.get("artifact")
        if not isinstance(saved, dict) or not saved.get("path") or not saved.get("sha256") or not saved.get("bytes"):
            issues.append("retained result fingerprint missing or empty")
        else:
            path = saved["path"]
            if path not in file_cache:
                try:
                    file_cache[path] = artifact(path)
                except (OSError, ValueError):
                    file_cache[path] = None
            if file_cache[path] is None:
                issues.append("retained result is unavailable")
            elif file_cache[path] != saved:
                issues.append("retained result changed")
    return issues


def _exclusion_problems(board: dict, board_path: Path, name: str) -> list[str]:
    try:
        from _reasoning import exclusion_problems
    except ImportError:
        return ["exclusion currentness cannot be verified"]
    return exclusion_problems(board, board_path, name)


def audit(board: dict, board_path: Path) -> dict:
    """Read-only audit. Only applicable context regions require initial checks."""
    blockers, warnings, regions, summaries, suggestions = [], [], [], [], []
    active = _active_contexts(board)
    identity = _identity(board, board_path)
    if not active:
        blockers.append("Case context has not been reviewed: use context --region with a source, or context --none --why")
    required: dict[str, list[str]] = {}
    # Old no-context reviews are historical once a later regional context exists.
    regional_reviews = [row for row in active if row.get("regions")]
    relevant = regional_reviews or active[-1:]
    for row in relevant:
        key = row.get("id", "unknown")
        if row.get("case_snapshot") != identity:
            blockers.append(f"{key}: context belongs to a different case/photo; explicitly supersede and review again")
            continue
        if row.get("relation") == "unconfirmed":
            warnings.append(f"{key}: cross-photo relation is unconfirmed; listed regions are not inherited as priorities")
            continue
        for name in row.get("regions", []):
            required.setdefault(name, []).append(key)

    complete: dict[str, list[dict]] = {}
    file_cache: dict = {}
    for record in board.get("search_checks", []):
        issues = _query_problems(board, identity, record, file_cache)
        state = "stale" if issues else "complete" if record.get("outcome") in COMPLETED else "incomplete"
        name = record.get("candidate")
        if state == "complete":
            complete.setdefault(name, []).append(record)
        if issues:
            warnings.append(f"{record.get('id', '?')} ({name}): " + "; ".join(issues))
        summaries.append({"id": record.get("id"), "candidate": name, "method": record.get("method"),
                          "feature": str(record.get("feature", ""))[:120], "query": str(record.get("query", ""))[:240],
                          "outcome": record.get("outcome"), "state": state, "bbox": record.get("bbox"), "issues": issues})

    candidates = board.get("candidates", {})
    for name, context_ids in required.items():
        candidate = candidates.get(name)
        checks = complete.get(name, [])
        state = "missing" if candidate is None else "checked" if checks else "unsearched"
        if candidate is None:
            blockers.append(f"{name}: context region is missing from the candidate board; add it before narrowing")
        elif candidate.get("status") == "excluded":
            issues = _exclusion_problems(board, board_path, name)
            if not issues:
                state = "excluded"
                warnings.append(f"{name}: current explicit exclusion discharges its initial-check requirement; context alone never excludes")
            else:
                state = "stale-exclusion"
                blockers.append(f"{name}: exclusion is not current: " + "; ".join(issues))
        elif not checks:
            blockers.append(f"{name}: no completed current initial check; another region's query, failure or deferral does not count")
        regions.append({"name": name, "context_ids": context_ids, "candidate_present": candidate is not None,
                        "level": candidate.get("level") if candidate else None, "state": state,
                        "completed_checks": [row["id"] for row in checks]})

    other_unsearched = [name for name, candidate in candidates.items()
                        if name not in required and name not in complete and candidate.get("status") != "excluded"]
    if other_unsearched:
        names = ", ".join(other_unsearched[:8])
        suffix = f" (+{len(other_unsearched) - 8} more)" if len(other_unsearched) > 8 else ""
        warnings.append(f"Other candidates without a recorded current initial check: {names}{suffix}. Pool rank does not prove candidate coverage.")

    observed = {str(clue.get("kind")) for clue in board.get("clues", {}).values()
                if clue.get("status") in ("observed", "read") and clue.get("kind")}
    for name in required:
        if len(suggestions) >= 8:
            break
        used = {row.get("feature", "").casefold() for row in complete.get(name, [])}
        # IDs count for their named clue kind too. This is an explicit ledger
        # comparison, not a claim to understand arbitrary feature descriptions.
        for feature in list(used):
            for key, clue in board.get("clues", {}).items():
                if key.casefold() == feature and clue.get("kind"):
                    used.add(str(clue["kind"]).casefold())
        untried = sorted(kind for kind in observed if kind.casefold() not in used)
        if untried:
            suggestions.append(f"{name}: observed clue kinds not yet named by completed searches: {', '.join(untried[:6])}; choose a discriminating feature, not just another place name")
    if not observed and required:
        suggestions.append("Record observed features, then choose query dimensions from them; changing region names alone does not test a different feature")

    return {"schema_version": 1, "ready": not blockers, "blockers": blockers, "warnings": warnings,
            "scope": "Declared context and initial-check completeness only; not exhaustive coverage, independent evidence, or geographic accuracy.",
            "regions": regions, "queries": summaries[-20:], "queries_omitted": max(0, len(summaries) - 20),
            "suggestions": suggestions, "other_unsearched_count": len(other_unsearched),
            "history_counts": {"contexts": len(board.get("context_reviews", [])), "queries": len(summaries)}}
