"""Small shared contracts for auditable, file-based agent workflows."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import tempfile


class InputError(ValueError):
    def __init__(self, message: str, status: str = "invalid"):
        super().__init__(message)
        self.status = status


def _reject_constant(value: str):
    raise InputError(f"Non-finite JSON number: {value}")


def load_input(path: str | Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=_reject_constant)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputError(f"Cannot read JSON input: {type(exc).__name__}") from exc
    if not isinstance(data, dict):
        raise InputError("Input must be a JSON object")
    def check_numbers(item):
        if isinstance(item, float) and not math.isfinite(item):
            raise InputError("Input contains a non-finite number")
        if isinstance(item, dict):
            for value in item.values():
                check_numbers(value)
        elif isinstance(item, list):
            for value in item:
                check_numbers(value)
    check_numbers(data)
    return data


def interval(value, name: str, positive: bool = False) -> tuple[float, float]:
    pair = value if isinstance(value, (list, tuple)) else (value, value)
    if len(pair) != 2 or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in pair):
        raise InputError(f"{name} must be a number or [lower, upper]")
    try:
        lo, hi = map(float, pair)
    except OverflowError as exc:
        raise InputError(f"{name} is outside the supported numeric range") from exc
    if not all(math.isfinite(v) for v in (lo, hi)) or lo > hi or (positive and lo <= 0):
        raise InputError(f"{name} has non-finite, reversed, or nonpositive bounds")
    return lo, hi


def fingerprint(path: str | Path) -> dict:
    p = Path(path).resolve()
    digest = hashlib.sha256()
    try:
        with p.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise InputError(f"Cannot read evidence file: {p.name}") from exc
    return {"path": str(p), "sha256": digest.hexdigest()}


def emit(payload: dict, out: str | Path | None = None) -> int:
    status = payload.get("status")
    codes = {"ok": 0, "partial": 0, "invalid": 2, "needs_input": 2, "unavailable": 3}
    if status not in codes:
        raise ValueError(f"Unknown workflow status: {status}")
    record = {"schema_version": 1, "result": {}, "assumptions": [], "warnings": [], "evidence": [], **payload}
    serialized = json.dumps(record, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    if out is not None:
        target = Path(out)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as stream:
                temporary = stream.name
                stream.write(serialized)
            os.replace(temporary, target)
        finally:
            if temporary is not None and os.path.exists(temporary):
                os.unlink(temporary)
    print(serialized, end="")
    return codes[status]
