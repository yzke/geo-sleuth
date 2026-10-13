"""Bounded street-image adapters. Service errors must never become location evidence."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
from urllib.parse import urlencode, urlsplit

from PIL import Image

from _net import fetch_bytes
import geo


class ProviderError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class DownloadBudget:
    def __init__(self, limit: int = 3):
        self.remaining = limit

    def consume(self):
        if not self.remaining:
            raise ProviderError("download_budget_exhausted")
        self.remaining -= 1


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _location(values):
    if (not isinstance(values, (list, tuple)) or len(values) != 2
            or not all(_number(v) for v in values)
            or not -90 <= values[0] <= 90 or not -180 <= values[1] <= 180):
        raise ProviderError("invalid_provider_coordinates")
    return list(values)


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value):
        raise ProviderError("invalid_provider_id")
    return value


def desired_heading(view: dict, wgs: list) -> float:
    if "toward" in view:
        target = view["toward"]
        if geo.distance(wgs, [target["lat"], target["lon"]]) < 1:
            raise ProviderError("view_target_at_camera")
        return geo.bearing(wgs, [target["lat"], target["lon"]])
    return view["heading_deg"]


def _metadata(provider, pid, wgs, heading, pitch, hfov, date=None, **extra):
    return {"provider": provider, "provider_id": pid, "wgs84_lat_lon": wgs,
            "heading_deg": heading, "pitch_deg": pitch, "hfov_deg": hfov,
            "projection": "perspective", "capture_date": date,
            "capture_date_status": "known" if date else "unknown", **extra}


def fetch_provider(provider: str, candidate: dict, request: dict, cache: Path,
                   budget: DownloadBudget):
    """Yield decoded image/metadata pairs; the caller checks pixels before accepting one."""
    if provider == "mapillary":
        yield from _mapillary(candidate, request, budget)
        return
    # Import lazily so offline validation does not load network clients.
    if provider == "gsv":
        import gsv
        found = gsv.near(candidate["lat"], candidate["lon"], request["radius_m"], None)
    else:
        import baidu_pano
        found = baidu_pano.near(candidate["lat"], candidate["lon"], proxy=None)
    if found is None:
        # Both legacy helpers collapse some failures into None: Baidu includes
        # transport errors, while Google includes unexpected panorama layouts.
        # The wrapper cannot establish coverage absence from this value alone.
        raise ProviderError("lookup_empty_or_failed")
    wgs = _location(found.get("wgs"))
    if geo.distance(wgs, [candidate["lat"], candidate["lon"]]) > request["radius_m"]:
        raise ProviderError("outside_requested_radius")
    pid = _identifier(found.get("id"))
    view = request["view"]
    heading = desired_heading(view, wgs)
    hfov, pitch = view["hfov_deg"], view["pitch_deg"]
    width, height = request["width"], request["height"]
    budget.consume()
    if provider == "gsv":
        im = gsv.render(pid, heading, pitch, hfov, width, height, None, cache)
        meta = _metadata(provider, pid, wgs, heading, pitch, hfov, found.get("date"),
                         renderer_fov_axis="horizontal", renderer_fov_deg=hfov)
    else:
        vfov = math.degrees(2 * math.atan(math.tan(math.radians(hfov / 2)) * height / width))
        im = baidu_pano.render(pid, heading, pitch, vfov, width, height, cache=cache, proxy=None)
        meta = _metadata(provider, pid, wgs, heading, pitch, hfov, found.get("date"),
                         renderer_fov_axis="vertical", renderer_fov_deg=vfov)
    yield im, meta


def _mapillary(candidate, request, budget):
    token = os.environ.get("MAPILLARY_TOKEN", "").strip()
    if not token:
        raise ProviderError("missing_token")
    lat, lon, radius = candidate["lat"], candidate["lon"], request["radius_m"]
    dy = radius / 111000
    dx = radius / (111000 * max(0.001, math.cos(math.radians(lat))))
    # A single bounded page does not establish global absence. Avoid silently
    # wrapping a bbox across the antimeridian or through a pole.
    if abs(lat) + dy >= 90 or abs(lon) + dx >= 180:
        raise ProviderError("bbox_crosses_coordinate_boundary")
    fields = "id,geometry,computed_geometry,compass_angle,computed_compass_angle,captured_at,camera_type,thumb_1024_url"
    query = urlencode({"bbox": f"{lon-dx},{lat-dy},{lon+dx},{lat+dy}", "fields": fields, "limit": 20})
    # Token goes in the header; URLs and response bodies are never recorded.
    raw = fetch_bytes("https://graph.mapillary.com/images?" + query,
                      headers={"Authorization": "OAuth " + token}, timeout=20)
    try:
        response = json.loads(raw)
        rows = response["data"]
        if not isinstance(rows, list):
            raise ValueError("data is not a list")
    except (ValueError, KeyError, TypeError):
        raise ProviderError("invalid_service_response") from None
    if not rows:
        raise ProviderError("no_coverage_in_bounded_search")
    eligible = []
    rejected = {"unsupported_projection": 0, "outside_requested_radius": 0,
                "unknown_heading": 0, "view_mismatch": 0, "invalid_metadata": 0}
    for row in rows[:20]:
        if not isinstance(row, dict):
            rejected["invalid_metadata"] += 1
            continue
        try:
            if row.get("camera_type") != "perspective":
                rejected["unsupported_projection"] += 1
                continue
            geometry = row.get("computed_geometry") or row.get("geometry") or {}
            coords = geometry["coordinates"]
            wgs = _location([coords[1], coords[0]])
            distance = geo.distance(wgs, [lat, lon])
            if distance > radius:
                rejected["outside_requested_radius"] += 1
                continue
            heading = row.get("computed_compass_angle")
            if heading is None:
                heading = row.get("compass_angle")
            if not _number(heading):
                rejected["unknown_heading"] += 1
                continue
            heading %= 360
            delta = abs((heading - desired_heading(request["view"], wgs) + 180) % 360 - 180)
            if delta > request["view"]["heading_tolerance_deg"]:
                rejected["view_mismatch"] += 1
                continue
            pid = _identifier(str(row["id"]))
            url = row["thumb_1024_url"]
            if not isinstance(url, str) or urlsplit(url).scheme != "https":
                raise ValueError("invalid download URL")
            date = None
            captured = row.get("captured_at")
            if _number(captured):
                try:
                    date = datetime.fromtimestamp(captured / 1000, tz=timezone.utc).isoformat()
                except (ValueError, OverflowError, OSError):
                    pass
            meta = _metadata("mapillary", pid, wgs, heading, None, None, date,
                             capture_timestamp_ms=captured if _number(captured) else None,
                             heading_difference_deg=delta, search_limited=True,
                             selected_from_at_most=20,
                             view_note="Native perspective image; pitch and FOV are unknown, not the requested render settings.")
            eligible.append((delta, distance, url, meta))
        except (KeyError, IndexError, TypeError, ValueError, ProviderError):
            rejected["invalid_metadata"] += 1
    if not eligible:
        reason = max(rejected, key=rejected.get)
        raise ProviderError(reason + "_in_bounded_search")
    for _, _, url, meta in sorted(eligible, key=lambda item: item[:2])[:3]:
        budget.consume()
        try:
            data = fetch_bytes(url, timeout=20)
            if not data:
                yield b"", meta
            else:
                yield data, meta
        except Exception:
            # Keep looking within this bounded page; an unreadable image is not
            # evidence against the candidate. No exception text may leak URLs.
            yield None, {**meta, "download_error": "service_error"}
