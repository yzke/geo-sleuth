"""Revision-aware, explicitly declared reasoning for the local candidate ledger.

This module tracks dependencies supplied by the operator; it does not infer
scientific independence or certify the truth of a clue or a likelihood ratio.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import math
from pathlib import Path


def _revision(clue: dict) -> int:
    value = clue.get("revision", 1)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("Clue revision must be a positive integer")
    return value


def _snapshot(b: dict, kid: str, visiting: set | None = None) -> dict:
    clues = b.get("clues", {})
    if kid not in clues:
        raise ValueError(f"Unknown dependency clue {kid}")
    visiting = set(visiting or ())
    if kid in visiting:
        raise ValueError(f"Dependency cycle at {kid}")
    visiting.add(kid)
    clue = clues[kid]
    result = {kid: _revision(clue)}
    for upstream in clue.get("depends_on", []):
        result.update(_snapshot(b, upstream, visiting))
    return result


def clue_problems(b: dict, kid: str, visiting: set | None = None) -> list[str]:
    """Explain staleness, including upstream clues that have not been recomputed."""
    visiting = set(visiting or ())
    if kid in visiting:
        return [f"Dependency cycle at {kid}"]
    if kid not in b.get("clues", {}):
        return [f"Unknown dependency clue {kid}"]
    visiting.add(kid)
    clue = b["clues"][kid]
    try:
        _revision(clue)
        expected = {}
        for upstream in clue.get("depends_on", []):
            expected.update(_snapshot(b, upstream))
        if expected != clue.get("dependency_snapshot", {}):
            return [f"{kid}: declared inputs changed; recompute and revise this clue"]
    except (ValueError, TypeError) as error:
        return [str(error)]
    problems = []
    for upstream in clue.get("depends_on", []):
        problems.extend(clue_problems(b, upstream, visiting))
    return list(dict.fromkeys(problems))


def clue_fields(b: dict, depends_on: list | None = None, group: str | None = None) -> dict:
    depends_on = list(dict.fromkeys(depends_on or []))
    snapshot = {}
    for kid in depends_on:
        problems = clue_problems(b, kid)
        if problems:
            raise ValueError("Cannot use stale input: " + "; ".join(problems))
        snapshot.update(_snapshot(b, kid))
    fields = {"revision": 1, "depends_on": depends_on, "dependency_snapshot": snapshot}
    if group is not None:
        if not isinstance(group, str):
            raise ValueError("Evidence group must be text")
        if group.strip():
            fields["group"] = group.strip()
    return fields


def revise_clue(b: dict, kid: str, text: str | None = None,
                file: str | None = None, why: str | None = None, *,
                depends_on: list | None = None, group: str | None = None,
                status: str | None = None, kind: str | None = None,
                source: str | None = None) -> dict:
    """Explicitly reconfirm/recompute a clue. Omitted fields keep current values.

    Empty depends_on/group explicitly clears that declaration. Every revision,
    including a return to earlier text, invalidates older recorded uses.
    """
    if kid not in b.get("clues", {}):
        raise ValueError(f"Unknown clue {kid}")
    if not why or not why.strip():
        raise ValueError("Revision needs --why explaining the correction or recomputation")
    old = b["clues"][kid]
    dependencies = old.get("depends_on", []) if depends_on is None else depends_on
    for upstream in dependencies:
        if kid in _snapshot(b, upstream):
            raise ValueError(f"Dependency cycle: {kid} cannot depend on {upstream}")
    fields = clue_fields(b, dependencies, old.get("group") if group is None else group)
    updated = deepcopy(old)
    previous = {key: deepcopy(value) for key, value in old.items()
                if key not in ("revision_history", "reasoning_stale")}
    updated.setdefault("revision_history", []).append({
        "previous": previous, "why": why.strip(),
        "revised_at": datetime.now().isoformat(timespec="seconds"),
    })
    updated.pop("group", None)
    updated.pop("reasoning_stale", None)
    updated.update(fields)
    updated["revision"] = _revision(old) + 1
    for key, value in (("text", text), ("file", file), ("status", status),
                       ("kind", kind), ("source", source)):
        if value is not None:
            updated[key] = value
    b["clues"][kid] = updated
    refresh(b)
    return updated


def evidence_fields(b: dict, kid: str) -> dict:
    problems = clue_problems(b, kid)
    if problems:
        raise ValueError("Cannot record evidence from stale inputs: " + "; ".join(problems))
    return {"reasoning_snapshot": _snapshot(b, kid)}


def evidence_problems(b: dict, record: dict) -> list[str]:
    kid = record.get("clue")
    if not kid:
        return ["Evidence has no clue"]
    problems = clue_problems(b, kid)
    if problems:
        return problems
    try:
        current = _snapshot(b, kid)
    except (ValueError, TypeError) as error:
        return [str(error)]
    saved = record.get("reasoning_snapshot")
    if saved is None:
        # Original boards did not declare dependencies or revisions. They remain
        # readable, but an old row cannot silently attach to a corrected clue.
        clue = b["clues"][kid]
        if current != {kid: 1} or clue.get("depends_on"):
            return [f"{kid}: legacy evidence predates the current revision/inputs"]
    elif saved != current:
        return [f"{kid}: evidence was recorded against superseded inputs"]
    if "lr" in record:
        try:
            lr = float(record["lr"])
            if not math.isfinite(lr) or lr <= 0:
                raise ValueError()
        except (TypeError, ValueError, OverflowError):
            return [f"{kid}: likelihood ratio must be finite and positive"]
    return []


def _latest(entries: list) -> list:
    latest = {}
    for entry in entries:
        latest[(entry.get("candidate"), entry.get("clue"))] = entry
    return list(latest.values())


def _source_tokens(b: dict, kid: str) -> set:
    """A descendant shares a source with its declared roots and explicit groups."""
    tokens = set()
    for key in _snapshot(b, kid):
        clue = b["clues"][key]
        if not clue.get("depends_on"):
            tokens.add("clue:" + key)
        if clue.get("group"):
            tokens.add("group:" + clue["group"])
    return tokens


def _grouped(b: dict, entries: list) -> tuple[list, list]:
    groups = []
    for entry in entries:
        tokens = _source_tokens(b, entry["clue"])
        combined = [entry]
        separate = []
        for group_tokens, members in groups:
            if tokens & group_tokens:
                tokens |= group_tokens
                combined += members
            else:
                separate.append((group_tokens, members))
        # Overlap can be transitive when one entry joins two prior groups.
        changed = True
        while changed:
            changed = False
            remaining = []
            for group_tokens, members in separate:
                if tokens & group_tokens:
                    tokens |= group_tokens
                    combined += members
                    changed = True
                else:
                    remaining.append((group_tokens, members))
            separate = remaining
        groups = separate + [(tokens, combined)]
    result, conflicts = [], []
    for tokens, members in groups:
        positive = any(float(entry["lr"]) > 1 for entry in members)
        negative = any(float(entry["lr"]) < 1 for entry in members)
        ids = [entry.get("id") or entry["clue"] for entry in members]
        if positive and negative:
            conflicts.append({"sources": sorted(tokens), "entries": ids,
                              "clues": sorted({entry["clue"] for entry in members})})
            continue
        selected = max(members, key=lambda entry: abs(math.log(float(entry["lr"]))))
        if len(members) > 1:
            selected = {**selected, "reasoning_group_members": ids}
        result.append(selected)
    return result, conflicts


def group_evidence(b: dict, entries: list) -> list:
    """Collapse a single candidate's combined own/inherited current evidence.

    Inherited entries retain their original candidate name; this operation
    intentionally ignores that name when identifying shared sources.
    """
    valid = [entry for entry in _latest(entries) if not evidence_problems(b, entry)]
    return _grouped(b, valid)[0]


def current_evidence(b: dict, entries: list | None = None) -> list:
    """Latest valid uses without collapsing source groups before inheritance."""
    return [entry for entry in _latest(b.get("evidence", []) if entries is None else entries)
            if not evidence_problems(b, entry)]


def active_evidence(b: dict, entries: list | None = None) -> list:
    """Latest use per candidate/clue, then one contribution per shared source."""
    valid = current_evidence(b, entries)
    candidates = {}
    for entry in valid:
        candidates.setdefault(entry.get("candidate"), []).append(entry)
    result = []
    for values in candidates.values():
        result.extend(_grouped(b, values)[0])
    return result


def _inherited_for_audit(b: dict, name: str, by_candidate: dict) -> list:
    """Mirror board support inheritance when checking shared-source conflicts."""
    own = by_candidate.get(name, [])
    own_clues = {entry["clue"] for entry in own}
    children = {}
    for child, candidate in b.get("candidates", {}).items():
        if candidate.get("parent") and candidate.get("status") != "excluded":
            children.setdefault(candidate["parent"], []).append(child)
    todo, seen, best = list(children.get(name, [])), {name}, {}
    while todo:
        child = todo.pop()
        if child in seen:
            continue
        seen.add(child)
        todo.extend(children.get(child, []))
        for entry in by_candidate.get(child, []):
            kid = entry["clue"]
            if kid not in own_clues and float(entry["lr"]) > best.get(kid, {}).get("lr", 1):
                best[kid] = {**entry, "via": child}
    return own + list(best.values())


def exclusion_candidate_snapshot(b: dict, name: str) -> dict:
    """Stable spatial scope and ancestry, excluding notes and score bookkeeping."""
    chain, seen, current = [], set(), name
    while current:
        if current in seen:
            raise ValueError(f"Invalid candidate ancestry for {name}: {current}")
        seen.add(current)
        candidate = b.get("candidates", {}).get(current)
        if candidate is None:
            chain.append({"name": current, "missing": True})
            break
        chain.append({"name": current, "inputs": {
            key: deepcopy(candidate[key]) for key in
            ("level", "bbox", "center", "scan_bbox", "geometry", "polyline") if key in candidate
        }})
        current = candidate.get("parent")
    return {"chain": chain}


def _exclusion_artifact_problems(record: dict) -> list:
    saved = record.get("computed_artifact")
    if not saved:
        return []  # Legacy rows remain readable; strict coverage audit flags them.
    try:
        from _infra import artifact
        if not isinstance(saved, dict) or not saved.get("path"):
            raise ValueError("Missing recorded artifact path")
        path = Path(saved["path"])
        if not path.is_absolute():
            raise ValueError("Recorded artifact path is not absolute")
        declared = Path(record.get("computed", ""))
        if declared.is_absolute() and declared.resolve() != path.resolve():
            return ["Exclusion computed artifact path changed"]
        if artifact(path) != saved:
            return ["Exclusion computed artifact changed"]
    except (OSError, TypeError, ValueError) as error:
        return [f"Exclusion computed artifact unavailable: {error}"]
    return []


def _exclusion_candidate_problems(b: dict, name: str, record: dict) -> list:
    saved = record.get("candidate_snapshot")
    if saved is None:
        return []
    try:
        if saved != exclusion_candidate_snapshot(b, name):
            return ["Exclusion candidate scope or ancestry changed"]
    except (TypeError, ValueError) as error:
        return [f"Exclusion candidate scope unavailable: {error}"]
    return []


def exclusion_problems(b: dict, board_path: Path, candidate_name: str) -> list:
    """Strictly validate an exclusion used to discharge a search obligation."""
    candidate = b.get("candidates", {}).get(candidate_name, {})
    if candidate.get("status") != "excluded":
        return ["Candidate is not currently excluded"]
    record = candidate.get("excluded_by", {})
    problems = evidence_problems(b, record)
    if not record.get("reasoning_snapshot"):
        problems.append("Exclusion has no recorded revision/input snapshot")
    if not record.get("candidate_snapshot"):
        problems.append("Exclusion has no recorded candidate scope snapshot")
    else:
        problems.extend(_exclusion_candidate_problems(b, candidate_name, record))
    if not str(record.get("why", "")).strip():
        problems.append("Exclusion reason is missing")
    kid = record.get("clue")
    if b.get("clues", {}).get(kid, {}).get("status") not in ("read", "computed"):
        problems.append("Exclusion clue must be read or computed")
    saved = record.get("computed_artifact")
    if not saved:
        problems.append("Exclusion has no recorded computed artifact fingerprint")
    else:
        try:
            from _infra import artifact
            path = Path(record.get("computed", ""))
            if not path.is_absolute():
                path = Path(board_path).resolve().parent / path
            if artifact(path) != saved:
                problems.append("Exclusion computed artifact changed")
        except (OSError, TypeError, ValueError) as error:
            problems.append(f"Exclusion computed artifact unavailable: {error}")
    return problems


def refresh(b: dict) -> None:
    """Refresh derived stale flags and reopen invalidated exclusions in memory.

    Flags are deterministic and only exist on stale clues. Infrastructure's
    existing clue snapshot consequently rejects comparisons made before a
    declared dependency changed, even when the infrastructure clue text did not.
    """
    for kid, clue in b.get("clues", {}).items():
        problems = clue_problems(b, kid)
        if problems:
            clue["reasoning_stale"] = problems
        else:
            clue.pop("reasoning_stale", None)
    latest = {(entry.get("candidate"), entry.get("clue")): entry
              for entry in b.get("evidence", [])}
    for name, candidate in b.get("candidates", {}).items():
        exclusion = candidate.get("excluded_by")
        if candidate.get("status") == "excluded" and exclusion:
            problems = evidence_problems(b, exclusion)
            problems.extend(_exclusion_artifact_problems(exclusion))
            problems.extend(_exclusion_candidate_problems(b, name, exclusion))
            if problems:
                history = deepcopy(exclusion)
                history["invalidated_because"] = problems
                candidate.setdefault("exclusion_history", []).append(history)
                candidate["exclusion_review_required"] = deepcopy(history)
                candidate["exclusion_review_required"]["evidence_when_reopened"] = deepcopy(
                    latest.get((name, exclusion.get("clue"))))
                candidate["status"] = "open"
                candidate.pop("excluded_by", None)
            else:
                candidate.pop("exclusion_review_required", None)
        pending = candidate.get("exclusion_review_required")
        if pending:
            renewed = latest.get((name, pending.get("clue")))
            if (renewed is not None and renewed != pending.get("evidence_when_reopened")
                    and not evidence_problems(b, renewed)):
                candidate.pop("exclusion_review_required", None)


def audit(b: dict) -> dict:
    refresh(b)
    blockers, warnings = [], []
    latest = _latest(b.get("evidence", []))
    referenced = {entry.get("clue") for entry in latest}
    for candidate in b.get("candidates", {}).values():
        for key in ("excluded_by", "exclusion_review_required"):
            referenced.add(candidate.get(key, {}).get("clue"))
    stale_clues = {}
    for kid, clue in b.get("clues", {}).items():
        problems = clue_problems(b, kid)
        if problems:
            stale_clues[kid] = problems
            message = f"{kid}: stale declared inputs; recompute and revise"
            (blockers if clue.get("used") or kid in referenced else warnings).append(message)
    stale_evidence = []
    valid_by_candidate = {}
    for entry in latest:
        problems = evidence_problems(b, entry)
        if problems:
            stale_evidence.append({"id": entry.get("id"), "candidate": entry.get("candidate"),
                                   "clue": entry.get("clue"), "issues": problems})
            blockers.append(f"{entry.get('candidate')}/{entry.get('clue')}: latest evidence needs review")
        else:
            valid_by_candidate.setdefault(entry.get("candidate"), []).append(entry)
    conflicts = []
    names = set(valid_by_candidate) | set(b.get("candidates", {}))
    for name in sorted(names):
        values = _inherited_for_audit(b, name, valid_by_candidate)
        for conflict in _grouped(b, values)[1]:
            conflicts.append({"candidate": name, **conflict})
            blockers.append(f"{name}: shared-source evidence has opposing signs ({', '.join(conflict['clues'])})")
    for name, candidate in b.get("candidates", {}).items():
        if candidate.get("exclusion_review_required"):
            blockers.append(f"{name}: stale exclusion reopened; record a renewed comparison")
        elif candidate.get("status") == "excluded":
            record = candidate.get("excluded_by", {})
            if any(not record.get(key) for key in
                   ("reasoning_snapshot", "computed_artifact", "candidate_snapshot")):
                warnings.append(f"{name}: legacy exclusion lacks complete revision/artifact/scope snapshots")
    return {"ready": not blockers, "blockers": list(dict.fromkeys(blockers)),
            "warnings": warnings, "stale_clues": stale_clues,
            "stale_evidence": stale_evidence, "group_conflicts": conflicts,
            "scope": "Explicit revisions and declared dependencies only; source independence is not inferred."}
