#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow"]
# ///
"""Fetch, validate and record street images before handing them to match.py."""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import io
import math
import os
from pathlib import Path
import shlex
import tempfile
import uuid

from PIL import Image, ImageOps, ImageStat

from _street_sources import DownloadBudget, ProviderError, fetch_provider
from _workflow import InputError, emit, fingerprint, load_input


class ImageRejected(ValueError):
    pass


class ArtifactWriteError(OSError):
    """A local output failure, kept separate from provider availability."""


def check_image(value):
    """Reject placeholders and unusable files, not semantic mismatches."""
    try:
        if value is None:
            raise ImageRejected("download_failed")
        if isinstance(value, bytes):
            if not value:
                raise ImageRejected("empty_image")
            source = Image.open(io.BytesIO(value))
        elif isinstance(value, Image.Image):
            source = value
        else:
            if Path(value).stat().st_size == 0:
                raise ImageRejected("empty_image")
            source = Image.open(value)
        if min(source.size) < 64:
            raise ImageRejected("image_too_small")
        if max(source.size) > 20000 or source.width * source.height > 40000000:
            raise ImageRejected("image_too_large")
        source.load()
        image = ImageOps.exif_transpose(source).convert("RGB")
        sample = image.copy()
        sample.thumbnail((256, 256))
        stddev = ImageStat.Stat(sample).stddev
        if max(stddev) < 1:
            raise ImageRejected("constant_or_near_constant_image")
        return image, {"width": image.width, "height": image.height,
                       "channel_stddev": [round(v, 4) for v in stddev],
                       "pixel_gate": "passed", "semantic_match": "not_tested"}
    except ImageRejected:
        raise
    except Exception:
        raise ImageRejected("corrupt_or_unreadable_image") from None


def _number(value, name, lower, upper):
    try:
        valid = (not isinstance(value, bool) and isinstance(value, (int, float))
                 and math.isfinite(value) and lower <= value <= upper)
    except OverflowError:
        valid = False
    if not valid:
        raise InputError(f"{name} must be a finite number in [{lower}, {upper}]")
    return value


def _point(value, name):
    if not isinstance(value, dict):
        raise InputError(f"{name} must contain lat and lon")
    return {"lat": _number(value.get("lat"), name + ".lat", -90, 90),
            "lon": _number(value.get("lon"), name + ".lon", -180, 180)}


def _path(value, base, name):
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{name} must be a nonempty file path")
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def prepare_request(data, base, mode):
    result = dict(data)
    if "query_image" in data:
        path = _path(data["query_image"], base, "query_image")
        try:
            check_image(path)
        except ImageRejected as exc:
            raise InputError("query_image: " + str(exc), "needs_input") from None
        result["query_image"] = str(path)
    if mode == "validate":
        items = data.get("images")
        if not isinstance(items, list) or not 1 <= len(items) <= 30:
            raise InputError("images must contain 1 to 30 local image objects")
        result["images"] = []
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                raise InputError("Each images entry must be an object")
            result["images"].append({**item, "path": str(_path(item.get("path"), base, f"images[{i}].path"))})
        return result
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 10:
        raise InputError("candidates must contain 1 to 10 objects")
    result["candidates"] = []
    seen = set()
    for i, candidate in enumerate(candidates):
        point = _point(candidate, f"candidates[{i}]")
        cid = candidate.get("id")
        if not isinstance(cid, str) or not cid.strip() or len(cid) > 120 or cid in seen:
            raise InputError("Candidate ids must be distinct nonempty strings of at most 120 characters")
        seen.add(cid)
        result["candidates"].append({"id": cid, **point})
    providers = data.get("providers", ["gsv", "baidu", "mapillary"])
    if (not isinstance(providers, list) or not 1 <= len(providers) <= 3
            or any(p not in ("gsv", "baidu", "mapillary") for p in providers)
            or len(set(providers)) != len(providers)):
        raise InputError("providers must be a distinct ordered list of gsv, baidu, mapillary")
    result["providers"] = providers
    view = data.get("view")
    if not isinstance(view, dict) or ("heading_deg" in view) == ("toward" in view):
        raise InputError("view requires exactly one of heading_deg or toward {lat, lon}", "needs_input")
    view = dict(view)
    if "toward" in view:
        view["toward"] = _point(view["toward"], "view.toward")
    else:
        view["heading_deg"] = _number(view["heading_deg"], "view.heading_deg", 0, 360) % 360
    view["hfov_deg"] = _number(view.get("hfov_deg", 90), "view.hfov_deg", 10, 120)
    view["pitch_deg"] = _number(view.get("pitch_deg", 0), "view.pitch_deg", -60, 60)
    view["heading_tolerance_deg"] = _number(view.get("heading_tolerance_deg", 30), "view.heading_tolerance_deg", 1, 90)
    result["view"] = view
    result["radius_m"] = _number(data.get("radius_m", 50), "radius_m", 1, 500)
    for key, default in (("width", 640), ("height", 480)):
        value = _number(data.get(key, default), key, 64, 1024)
        if int(value) != value:
            raise InputError(f"{key} must be an integer")
        result[key] = int(value)
    return result


def run_workflow(request, out_dir, mode, provider_fetch=None):
    fetch = provider_fetch or fetch_provider
    # Each invocation has an immutable image set. Old files never enter a new rank.
    run_dir = Path(out_dir).resolve() / "runs" / uuid.uuid4().hex
    valid_dir = run_dir / "images"
    valid_dir.mkdir(parents=True)
    images, attempts, candidates = [], [], []

    def accept(value, metadata, cid):
        image, quality = check_image(value)
        path = valid_dir / f"{len(images):03d}.png"
        temporary = None
        try:
            # Encode and hash outside the matcher's directory. Partial writes
            # must never appear in the otherwise valid image set.
            with tempfile.NamedTemporaryFile(dir=run_dir, suffix=".png", delete=False) as stream:
                temporary = Path(stream.name)
            image.save(temporary, format="PNG")
            digest = fingerprint(temporary)
            record = {"candidate_id": cid, **metadata, **quality,
                      "path": str(path), "sha256": digest["sha256"]}
            os.replace(temporary, path)
            temporary = None
        except Exception as exc:
            raise ArtifactWriteError(type(exc).__name__) from None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass  # Any unremovable temporary remains outside valid_dir.
        images.append(record)
        return record

    if mode == "fetch":
        for candidate in request["candidates"]:
            cid = candidate["id"]
            accepted = False
            budget = DownloadBudget(3)
            for provider in request["providers"]:
                cache = run_dir / "provider-cache" / f"{len(candidates):03d}" / provider
                try:
                    returned = False
                    for value, metadata in fetch(provider, candidate, request, cache, budget):
                        returned = True
                        try:
                            if metadata.get("projection") != "perspective":
                                raise ImageRejected("unsupported_projection")
                            accepted_record = accept(value, metadata, cid)
                            attempts.append({"candidate_id": cid, "provider": provider, "status": "accepted",
                                             "provider_id": accepted_record.get("provider_id")})
                            accepted = True
                            break
                        except ImageRejected as exc:
                            attempts.append({"candidate_id": cid, "provider": provider,
                                             "status": "rejected", "reason": str(exc)})
                    if not returned:
                        raise ProviderError("no_image_returned")
                except ArtifactWriteError as exc:
                    attempts.append({"candidate_id": cid, "provider": provider, "status": "unavailable",
                                     "reason": "artifact_write_failed", "error_type": str(exc)})
                    break  # Retrying a network provider cannot fix local storage.
                except ProviderError as exc:
                    attempts.append({"candidate_id": cid, "provider": provider, "status": "unavailable", "reason": exc.reason})
                except Exception as exc:
                    # Provider exceptions can contain credential-bearing URLs or response bodies.
                    attempts.append({"candidate_id": cid, "provider": provider, "status": "unavailable",
                                     "reason": "service_error", "error_type": type(exc).__name__})
                if accepted:
                    break
            candidates.append({**candidate, "status": "image_available" if accepted else "unavailable",
                               "geographic_conclusion": "not_tested"})
    else:
        for i, item in enumerate(request["images"]):
            cid = str(item.get("id", i))
            try:
                if item.get("projection") != "perspective":
                    raise ImageRejected("unsupported_or_unknown_projection")
                evidence = fingerprint(item["path"])
                metadata = {"provider": "local", "provider_id": cid, "projection": "perspective",
                            "capture_date": item.get("capture_date"),
                            "capture_date_status": "known" if item.get("capture_date") else "unknown",
                            "source_file": evidence, "heading_deg": item.get("heading_deg"),
                            "pitch_deg": item.get("pitch_deg"), "hfov_deg": item.get("hfov_deg"),
                            "wgs84_lat_lon": item.get("wgs84_lat_lon")}
                accept(item["path"], metadata, cid)
                attempts.append({"candidate_id": cid, "provider": "local", "status": "accepted"})
                candidates.append({"id": cid, "status": "image_available", "geographic_conclusion": "not_tested"})
            except ArtifactWriteError as exc:
                attempts.append({"candidate_id": cid, "provider": "local", "status": "unavailable",
                                 "reason": "artifact_write_failed", "error_type": str(exc)})
                candidates.append({"id": cid, "status": "unavailable", "geographic_conclusion": "not_tested"})
            except (ImageRejected, InputError) as exc:
                attempts.append({"candidate_id": cid, "provider": "local", "status": "rejected", "reason": str(exc)})
                candidates.append({"id": cid, "status": "unavailable", "geographic_conclusion": "not_tested"})
    count = len(images)
    status = "ok" if count == len(candidates) else "partial" if count else "unavailable"
    result = {"run_dir": str(run_dir), "run_manifest": str(run_dir / "manifest.json"),
              "valid_image_dir": str(valid_dir), "images": images,
              "candidates": candidates, "attempts": attempts,
              "valid_count": count, "requested_count": len(candidates), "match_handoff": None}
    if mode == "fetch":
        result["requested_view"] = request["view"]
        result["limits"] = {"max_candidates": 10, "max_image_downloads_per_candidate": 3,
                            "mapillary_pages": 1, "mapillary_page_size": 20}
    if count and request.get("query_image"):
        argv = ["uv", "run", str(Path(__file__).with_name("match.py").resolve()), "rank",
                "--query", request["query_image"], "--images", str(valid_dir),
                "--out", str(run_dir / "ranking.json"), "--sheet", str(run_dir / "comparison.jpg")]
        result["match_handoff"] = {"argv": argv, "command": shlex.join(argv), "executed": False}
    warnings = ["Image validity is not a visual or geographic match. Inspect capture date, camera position and structure before using ranking.",
                "Network failure, absent coverage, invalid pixels and low visual rank do not exclude a location."]
    if mode == "fetch" and "mapillary" in request["providers"]:
        warnings.append("Mapillary is a bounded one-page search. Native perspective images may have unknown pitch/FOV; heading agreement does not prove target visibility.")
    evidence = [fingerprint(request["query_image"])] if request.get("query_image") else []
    return {"workflow": "street." + mode, "status": status, "result": result,
            "assumptions": ["WGS84 latitude/longitude; heading clockwise from true north; positive pitch looks up; request FOV is horizontal."],
            "warnings": warnings, "evidence": evidence,
            "created_at": datetime.now(timezone.utc).isoformat()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    for mode in ("fetch", "validate"):
        command = sub.add_parser(mode)
        command.add_argument("--input", required=True, type=Path)
        command.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        request = prepare_request(load_input(args.input), args.input.resolve().parent, args.mode)
        payload = run_workflow(request, args.out_dir, args.mode)
        payload["evidence"].insert(0, fingerprint(args.input))
    except InputError as exc:
        payload = {"workflow": "street." + args.mode, "status": exc.status,
                   "result": {"message": str(exc), "match_handoff": None},
                   "warnings": ["Fix the request or supply missing input before retrying."]}
    except OSError as exc:
        payload = {"workflow": "street." + args.mode, "status": "unavailable",
                   "result": {"message": "Cannot write run artifacts", "error_type": type(exc).__name__, "match_handoff": None}}
    try:
        run_manifest = payload.get("result", {}).get("run_manifest")
        if run_manifest:
            # Retain the complete audit record next to this immutable image set;
            # the root manifest is only the pointer/content for the latest run.
            with contextlib.redirect_stdout(io.StringIO()):
                emit(payload, run_manifest)
        return emit(payload, args.out_dir / "manifest.json")
    except OSError:
        return emit({"workflow": "street." + args.mode, "status": "unavailable",
                     "result": {"message": "Cannot write manifest; check the output directory", "match_handoff": None}})


if __name__ == "__main__":
    raise SystemExit(main())
