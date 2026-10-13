"""Auditable infrastructure checks. Records attest to review, not location accuracy."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

FINE_LEVELS = {"area", "road", "point"}
OUTCOMES = ("matched", "contradicted", "inconclusive", "not-applicable")


def observed_clues(board: dict) -> dict:
    return {key: clue for key, clue in board.get("clues", {}).items()
            if str(clue.get("kind", "")).strip().lower() in ("infra", "infrastructure")
            and clue.get("status") in ("observed", "read")}


def artifact(path: str | Path) -> dict:
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError(f"Artifact must be an existing file: {path}")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return {"path": str(path), "sha256": digest.hexdigest(), "bytes": size}


def _digest(value: dict) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _stored_path(value: str, board_path: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else board_path.resolve().parent / path


def photo_snapshot(board: dict, board_path: Path) -> dict:
    value = board.get("photo", "")
    # The optional origin preserves init's cwd. If photo is later replaced, never
    # accidentally keep fingerprinting the old frame through its cached path.
    origin = board.get("photo_path") if board.get("photo_path_for") == value else None
    path = _stored_path(origin or value, board_path)
    return {"photo": value, "path": str(path.resolve()),
            "artifact": artifact(path) if value and path.is_file() else None}


def clue_snapshot(clue: dict, board_path: Path) -> dict:
    # used is bookkeeping: scoring a clue must neither satisfy nor invalidate a check.
    inputs = {k: v for k, v in clue.items() if k != "used"}
    source = clue.get("file")
    source_artifact = artifact(_stored_path(source, board_path)) if source else None
    return {"inputs": inputs, "artifact": source_artifact}


def candidate_snapshot(board: dict, name: str) -> dict:
    # Prior/status are ranking bookkeeping. Geometry, identity, scope and ancestry
    # matter to the comparison; moving a point under another road invalidates it.
    names, seen, current = [], set(), name
    while current:
        if current in seen:
            raise ValueError(f"Invalid candidate ancestry for {name}: {current}")
        if current not in board["candidates"]:
            names.append({"name": current, "missing": True})
            break
        seen.add(current)
        candidate = board["candidates"][current]
        names.append({"name": current, "inputs": {k: v for k, v in candidate.items()
                                                 if k not in ("prior", "status", "excluded_by")}})
        current = candidate.get("parent")
    return {"chain": names}


def _reason(why: str) -> str:
    if not why or not why.strip():
        raise ValueError("--why must describe the inspection or comparison")
    return why.strip()


def inspection_record(board: dict, board_path: Path, infra: str, file: str, why: str) -> dict:
    why = _reason(why)
    if infra not in ("present", "absent", "unclear"):
        raise ValueError("--infra must be present, absent or unclear")
    if infra == "absent" and observed_clues(board):
        raise ValueError("Cannot record absent: observed/read infrastructure clues are already on the board")
    return {"id": f"I{len(board.get('infrastructure_inspections', [])) + 1}",
            "infra": infra, "why": why, "artifact": artifact(file),
            "photo_snapshot": photo_snapshot(board, board_path)}


def verification_record(board: dict, board_path: Path, candidate: str,
                        clue_id: str, outcome: str, file: str, why: str) -> dict:
    why = _reason(why)
    if candidate not in board["candidates"] or board["candidates"][candidate].get("status") == "excluded":
        raise ValueError("Verification needs an active candidate")
    if board["candidates"][candidate].get("level") not in FINE_LEVELS:
        raise ValueError("Verification needs an area/road/point candidate")
    if clue_id not in observed_clues(board):
        raise ValueError("--clue must name an observed/read infrastructure clue, not an inference or computation")
    if outcome not in OUTCOMES:
        raise ValueError(f"--outcome must be one of {OUTCOMES}")
    inspections = board.get("infrastructure_inspections", [])
    inspection = inspections[-1] if inspections else None
    if inspection is None or inspection.get("infra") != "present":
        raise ValueError("Run inspect --infra present before comparing visible infrastructure")
    problems = inspection_problems(board, board_path, inspection)
    if problems:
        raise ValueError("Reinspect first: " + "; ".join(problems))
    return {"id": f"V{len(board.get('infrastructure_checks', [])) + 1}",
            "candidate": candidate, "clue": clue_id, "outcome": outcome, "why": why,
            "artifact": artifact(file), "inspection_sha256": _digest(inspection),
            "clue_snapshot": clue_snapshot(board["clues"][clue_id], board_path),
            "candidate_snapshot": candidate_snapshot(board, candidate)}


def _artifact_problems(saved: dict | None, label: str) -> list[str]:
    if not isinstance(saved, dict) or not saved.get("path") or not saved.get("sha256"):
        return [f"{label} has no recorded artifact fingerprint"]
    try:
        if artifact(saved["path"]) != saved:
            return [f"{label} artifact changed: {saved['path']}"]
    except (OSError, ValueError) as error:
        return [f"{label} artifact unavailable: {error}"]
    return []


def inspection_problems(board: dict, board_path: Path, inspection: dict) -> list[str]:
    problems = _artifact_problems(inspection.get("artifact"), "inspection")
    try:
        if inspection.get("photo_snapshot") != photo_snapshot(board, board_path):
            problems.append("board photo or its content changed since inspection")
    except (OSError, ValueError) as error:
        problems.append(f"board photo unavailable: {error}")
    if not str(inspection.get("why", "")).strip():
        problems.append("inspection reason is missing")
    return problems


def _check_problems(board: dict, board_path: Path, record: dict, inspection: dict | None) -> list[str]:
    problems = _artifact_problems(record.get("artifact"), "comparison")
    if not inspection or record.get("inspection_sha256") != _digest(inspection):
        problems.append("inspection changed since comparison")
    try:
        if record.get("clue_snapshot") != clue_snapshot(board["clues"][record["clue"]], board_path):
            problems.append("clue inputs or source artifact changed since comparison")
        if record.get("candidate_snapshot") != candidate_snapshot(board, record["candidate"]):
            problems.append("candidate inputs changed since comparison")
    except (OSError, ValueError, KeyError) as error:
        problems.append(f"comparison inputs unavailable: {error}")
    if not str(record.get("why", "")).strip():
        problems.append("comparison reason is missing")
    return problems


def audit(board: dict, board_path: Path, target: str | None) -> dict:
    """Audit every active fine candidate; readiness applies only to the selected main."""
    inspections = board.get("infrastructure_inspections", [])
    inspection = inspections[-1] if inspections else None
    clues = observed_clues(board)
    inspection_issues = []
    if inspection is None:
        inspection_issues.append("infrastructure inspection is missing; missing does not mean absent")
    else:
        inspection_issues.extend(inspection_problems(board, board_path, inspection))
        state = inspection.get("infra")
        if state == "unclear":
            inspection_issues.append("infrastructure visibility is unclear; inspect a usable crop or keep a broad conclusion")
        elif state == "absent" and clues:
            inspection_issues.append("absent inspection conflicts with observed/read infrastructure clues")
        elif state == "present" and not clues:
            inspection_issues.append("infrastructure is present but no observed/read infrastructure clue was recorded")
        elif state not in ("absent", "present"):
            inspection_issues.append("inspection has no valid visibility outcome")
    latest = {(record.get("candidate"), record.get("clue")): record
              for record in board.get("infrastructure_checks", [])}
    candidates = []
    for name, candidate in board.get("candidates", {}).items():
        if candidate.get("level") not in FINE_LEVELS or candidate.get("status") == "excluded":
            continue
        blockers, checks = list(inspection_issues), []
        for clue_id in clues:
            record = latest.get((name, clue_id))
            issues = _check_problems(board, board_path, record, inspection) if record else []
            state = "pending" if record is None else "stale" if issues else record.get("outcome", "invalid")
            checks.append({"clue": clue_id, "state": state, "issues": issues, "record": record})
            if state != "matched":
                detail = "; ".join(issues) if issues else {
                    "pending": "no candidate-specific comparison recorded",
                    "contradicted": "comparison contradicts this candidate; resolve it or choose another candidate",
                    "inconclusive": "comparison is inconclusive",
                    "not-applicable": "explicitly not applicable; no positive infrastructure verification",
                }.get(state, "invalid comparison outcome")
                blockers.append(f"{clue_id}: {detail}")
        candidates.append({"name": name, "level": candidate["level"], "infrastructure_ready": not blockers,
                           "blockers": blockers, "checks": checks})
    selected = next((candidate for candidate in candidates if candidate["name"] == target), None)
    blockers = ([f"{target}: {message}" for message in selected["blockers"]] if selected else
                ["strict infrastructure verification needs an active area/road/point main candidate"])
    return {"schema_version": 1, "verification_target": target,
            "infrastructure_ready": not blockers,
            "scope": "Infrastructure review completeness only; not proof of location, voltage, independent evidence, or metric accuracy.",
            "inspection": {"record": inspection, "issues": inspection_issues},
            "observed_clues": list(clues), "candidates": candidates, "blockers": blockers,
            "history": {"inspections": inspections, "checks": board.get("infrastructure_checks", [])}}
