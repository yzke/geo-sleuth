#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["Pillow"]
# ///
"""Conditional single-image range and calibrated shadow measurements."""
from __future__ import annotations

import argparse
import itertools
import math
from pathlib import Path
import random

from _workflow import InputError, emit, fingerprint, interval, load_input
from geo import range_from_size


def required(mapping, key, prefix=""):
    if key not in mapping or mapping[key] is None:
        raise InputError(f"Missing {prefix}{key}", "needs_input")
    return mapping[key]


def source(mapping, key, prefix=""):
    value = required(mapping, key, prefix)
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{prefix}{key} must describe the measurement source")
    return value.strip()


def section(request, key):
    result = required(request, key)
    if not isinstance(result, dict):
        raise InputError(f"{key} must be an object")
    return result


def scalar_interval(mapping, key, prefix="", positive=False):
    return interval(required(mapping, key, prefix), prefix + key, positive=positive)


def mid(bounds):
    return bounds[0] + (bounds[1] - bounds[0]) / 2


def ensure_finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise InputError("Input magnitude exceeds the stable numerical range")
    if isinstance(value, dict):
        for item in value.values():
            ensure_finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            ensure_finite(item)


def pixel(point, name, width, height):
    if not isinstance(point, (list, tuple)) or len(point) != 2:
        raise InputError(f"{name} must be [x, y], each scalar or [lower, upper]")
    x, y = (interval(value, f"{name}.{axis}") for axis, value in zip("xy", point))
    if x[0] < 0 or y[0] < 0 or x[1] > width or y[1] > height:
        raise InputError(f"{name} is outside the current image")
    return x, y


def image_context(request, base_dir):
    info, camera = section(request, "image"), section(request, "camera")
    dimensions = []
    for key in ("width", "height"):
        value = required(info, key, "image.")
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise InputError(f"image.{key} must be a positive integer")
        dimensions.append(value)
    width, height = dimensions
    source(info, "source", "image.")
    if required(info, "geometry", "image.") != "rectilinear_square_pixels":
        raise InputError("A rectilinear image with square pixels is required", "needs_input")
    if required(info, "pixel_frame", "image.") != "exif_transposed":
        raise InputError("Measure in the EXIF-transposed image coordinate frame", "needs_input")
    warnings, evidence = [], []
    path = info.get("path")
    if path is not None:
        if not isinstance(path, str) or not path:
            raise InputError("image.path must be a nonempty path")
        image_path = Path(path)
        if not image_path.is_absolute():
            image_path = base_dir / image_path
        try:
            from PIL import Image, ImageOps
            with Image.open(image_path) as original:
                actual = ImageOps.exif_transpose(original)
                actual.load()
                if actual.size != (width, height):
                    raise InputError("image dimensions differ from the EXIF-transposed source")
        except ImportError as exc:
            raise InputError("Pillow is unavailable; run with uv", "unavailable") from exc
        except InputError:
            raise
        except (OSError, ValueError) as exc:
            raise InputError("Cannot decode source image") from exc
        evidence.append({"kind": "source_image", **fingerprint(image_path)})
    else:
        warnings.append("image_not_supplied: source pixels and markings were not independently verified")
    if "focal_px" in camera or "equiv_focal_mm" in camera:
        source(camera, "focal_source", "camera.")
    if "focal_px" in camera and "equiv_focal_mm" in camera:
        raise InputError("Supply one focal calibration: focal_px or equiv_focal_mm")
    if "focal_px" in camera:
        if required(camera, "calibration_frame", "camera.") != "current_image":
            raise InputError("Pixel focal calibration must refer to the current image", "needs_input")
        focal = interval(camera["focal_px"], "camera.focal_px", positive=True)
    elif "equiv_focal_mm" in camera:
        if required(info, "crop", "image.") != "none":
            raise InputError("35 mm equivalent focal conversion needs an uncropped original frame; use calibrated current-image focal_px", "needs_input")
        mm = interval(camera["equiv_focal_mm"], "camera.equiv_focal_mm", positive=True)
        focal = tuple(value * math.hypot(width, height) / math.hypot(36, 24) for value in mm)
        warnings.append("equivalent_focal_conversion assumes the stated full-frame diagonal convention and original field of view")
    else:
        raise InputError("Missing camera.focal_px or camera.equiv_focal_mm", "needs_input")
    return info, camera, width, height, focal, warnings, evidence


def range_measure(request, base_dir):
    info, camera, width, height, focal, warnings, evidence = image_context(request, base_dir)
    target = section(request, "target")
    if required(target, "geometry", "target.") != "frontoparallel":
        raise InputError("Known-size range needs a dimension parallel to the image plane", "needs_input")
    source(target, "size_source", "target.")
    source(target, "pixel_source", "target.")
    size = scalar_interval(target, "size_m", "target.", positive=True)
    pixels = scalar_interval(target, "size_px", "target.", positive=True)
    if pixels[1] > math.hypot(width, height):
        raise InputError("target.size_px exceeds the image diagonal")
    def calculate(real, px, f):
        hfov = math.degrees(2 * math.atan(width / (2 * f)))
        return range_from_size(real, px, width, hfov)
    low, high = calculate(size[0], pixels[1], focal[0]), calculate(size[1], pixels[0], focal[1])
    return {"status": "partial" if warnings else "ok", "result": {
        "quantity": "optical_axis_depth_m", "depth_m": [low, high],
        "nominal_depth_m": calculate(mid(size), mid(pixels), mid(focal)),
        "focal_px": list(focal), "bounds_kind": "conditional_monotonic_input_bounds",
        "pixel_measurements_verified": False,
    }, "assumptions": ["Pinhole camera with square pixels and negligible residual distortion",
        "Known target dimension is fully visible and parallel to the image plane",
        "Input intervals cover the stated measurement uncertainty; model error is not included"],
        "warnings": warnings, "evidence": evidence}


def camera_ray(x, y, focal, cx, cy, pitch, roll):
    """Image ray in world right/up/forward axes, positive pitch up, roll clockwise."""
    rx, uy = (x - cx) / focal, (cy - y) / focal
    p, r = math.radians(pitch), math.radians(roll)
    x_level = math.cos(r) * rx + math.sin(r) * uy
    y_level = -math.sin(r) * rx + math.cos(r) * uy
    return (x_level, math.cos(p) * y_level + math.sin(p),
            -math.sin(p) * y_level + math.cos(p))


def intersect_ground(ray, camera_height):
    # Ground rays within one degree of the horizon amplify small pixel errors.
    depression = math.degrees(math.atan2(-ray[1], math.hypot(ray[0], ray[2])))
    if depression <= 1.0:
        raise InputError("Ground point is above or too close to the horizon (<= 1 degree depression)", "needs_input")
    scale = -camera_height / ray[1]
    return (scale * ray[0], 0.0, scale * ray[2])


def shadow_measure(request, base_dir):
    info, camera, width, height, focal, warnings, evidence = image_context(request, base_dir)
    obj, points = section(request, "object"), section(request, "points")
    if required(request, "ground") != "horizontal":
        raise InputError("Shadow inversion needs a locally horizontal ground plane", "needs_input")
    if required(obj, "geometry", "object.") != "vertical":
        raise InputError("The reference object must be vertical", "needs_input")
    for key in ("height_source", "pitch_source", "roll_source", "principal_point_source"):
        source(camera, key, "camera.")
    source(obj, "height_source", "object.")
    source(points, "source", "points.")
    bounds = {"focal": focal, "camera_height": scalar_interval(camera, "height_m", "camera.", True),
        "pitch": scalar_interval(camera, "pitch_deg", "camera."),
        "roll": scalar_interval(camera, "roll_deg", "camera."),
        "object_height": scalar_interval(obj, "height_m", "object.", True)}
    if bounds["pitch"][0] <= -90 or bounds["pitch"][1] >= 90:
        raise InputError("camera.pitch_deg must be strictly between -90 and 90")
    if bounds["roll"][0] < -180 or bounds["roll"][1] > 180:
        raise InputError("camera.roll_deg must be within [-180, 180]")
    for label, point in (("principal", required(camera, "principal_point_px", "camera.")),
                         ("foot", required(points, "foot", "points.")),
                         ("head", required(points, "head", "points.")),
                         ("tip", required(points, "shadow_tip", "points."))):
        xy = pixel(point, label, width, height)
        bounds[label + "_x"], bounds[label + "_y"] = xy
    tolerance = interval(request.get("head_tolerance_px", 3.0), "head_tolerance_px", positive=True)
    if tolerance[0] != tolerance[1] or tolerance[1] > 50:
        raise InputError("head_tolerance_px must be a scalar in (0, 50]")
    def evaluate(values, check_head=True):
        def ray(label):
            return camera_ray(values[label + "_x"], values[label + "_y"], values["focal"],
                values["principal_x"], values["principal_y"], values["pitch"], values["roll"])
        foot, tip = (intersect_ground(ray(label), values["camera_height"]) for label in ("foot", "tip"))
        distance = math.hypot(tip[0] - foot[0], tip[2] - foot[2])
        if distance <= 1e-6:
            raise InputError("Foot and shadow tip reconstruct to the same ground point", "needs_input")
        # Project the vertical top to compare the supplied head marking.
        p, r = math.radians(values["pitch"]), math.radians(values["roll"])
        x, y, z = foot[0], values["object_height"] - values["camera_height"], foot[2]
        level_y, depth = math.cos(p) * y - math.sin(p) * z, math.sin(p) * y + math.cos(p) * z
        if depth <= 0:
            raise InputError("Reconstructed object top lies behind the camera", "needs_input")
        local_x = math.cos(r) * x - math.sin(r) * level_y
        local_y = math.sin(r) * x + math.cos(r) * level_y
        head_x = values["principal_x"] + values["focal"] * local_x / depth
        head_y = values["principal_y"] - values["focal"] * local_y / depth
        residual = math.hypot(head_x - values["head_x"], head_y - values["head_y"])
        if check_head and residual > tolerance[0]:
            raise InputError(f"Object top/foot geometry is inconsistent (head residual {residual:.2f} px > {tolerance[0]:.2f} px); recheck calibration, points, height and ground", "needs_input")
        elevation = math.degrees(math.atan2(values["object_height"], distance))
        # Bearing is clockwise from the world-forward axis used by the calibrated camera.
        relative_bearing = math.degrees(math.atan2(foot[0] - tip[0], foot[2] - tip[2])) % 360
        return {"solar_elevation_deg": elevation, "shadow_length_m": distance,
            "head_residual_px": residual, "foot_ground_distance_m": math.hypot(foot[0], foot[2]),
            "solar_bearing_camera_forward_deg": relative_bearing}
    nominal = {key: mid(value) for key, value in bounds.items()}
    central = evaluate(nominal)
    variable = [key for key, value in bounds.items() if value[0] != value[1]]
    samples = [nominal]
    if len(variable) <= 10:
        for values in itertools.product(*(bounds[key] for key in variable)):
            samples.append({**nominal, **dict(zip(variable, values))})
        rng = random.Random(20261013)
        if variable:
            for _ in range(256):
                samples.append({key: rng.uniform(*value) for key, value in bounds.items()})
        sampling = "all_corners_plus_midpoint_and_seeded_uniform"
    else:
        rng = random.Random(20261013)
        for _ in range(1024):
            samples.append({key: rng.uniform(*value) for key, value in bounds.items()})
        # Force each individual bound so marginal horizon failures are visible.
        for key in variable:
            for value in bounds[key]:
                samples.append({**nominal, key: value})
        sampling = "seeded_uniform_plus_each_marginal_endpoint_and_midpoint"
    results, residuals = [], []
    for sample in samples:
        computed = evaluate(sample, check_head=False)
        residuals.append(computed["head_residual_px"])
        if computed["head_residual_px"] <= tolerance[0]:
            results.append(computed)
    if not results:
        raise InputError("No sampled geometry is consistent with the head marking", "needs_input")
    rejected = len(samples) - len(results)
    if rejected:
        warnings.append(f"{rejected}/{len(samples)} sensitivity samples failed head consistency; envelope includes only accepted samples")
    warnings.append("sampled_sensitivity_envelope is not a confidence interval or a guaranteed bound; model error is not included")
    if variable:
        warnings.append("Uncertain inputs are treated independently; correlations are not modeled")
    labels = {"focal": "camera.focal_px", "camera_height": "camera.height_m",
        "pitch": "camera.pitch_deg", "roll": "camera.roll_deg", "object_height": "object.height_m"}
    for key, name in (("principal", "camera.principal_point_px"), ("foot", "points.foot"),
                      ("head", "points.head"), ("tip", "points.shadow_tip")):
        for axis in "xy":
            labels[key + "_" + axis] = name + "." + axis
    sensitivities = []
    for key in variable:
        accepted = [central["solar_elevation_deg"]]
        rejected_endpoints = 0
        for endpoint in bounds[key]:
            varied = evaluate({**nominal, key: endpoint}, check_head=False)
            if varied["head_residual_px"] <= tolerance[0]:
                accepted.append(varied["solar_elevation_deg"])
            else:
                rejected_endpoints += 1
        sensitivities.append({"parameter": labels[key], "input_bounds": list(bounds[key]),
            "solar_elevation_span_deg": max(accepted) - min(accepted),
            "max_abs_change_deg": max(abs(value - central["solar_elevation_deg"]) for value in accepted),
            "accepted_endpoints": 2 - rejected_endpoints, "rejected_endpoints": rejected_endpoints})
    sensitivities.sort(key=lambda item: (-item["solar_elevation_span_deg"], item["parameter"]))
    if any(item["rejected_endpoints"] for item in sensitivities):
        warnings.append("Sensitivity ranking excludes head-inconsistent endpoints; a zero span with rejected endpoints is not evidence of insensitivity")
    result = {"quantity": "solar_elevation_deg", "nominal": central,
        "solar_elevation_deg": [min(x["solar_elevation_deg"] for x in results), max(x["solar_elevation_deg"] for x in results)],
        "shadow_length_m": [min(x["shadow_length_m"] for x in results), max(x["shadow_length_m"] for x in results)],
        "bounds_kind": "sampled_sensitivity_envelope", "sampling": {"method": sampling,
            "seed": 20261013, "evaluated": len(samples), "accepted": len(results),
            "head_residual_px": [min(residuals), max(residuals)]},
        "sensitivity": {"method": "one_at_a_time_endpoints_with_other_inputs_at_midpoints",
            "quantity": "solar_elevation_deg", "ranked_parameters": sensitivities,
            "interpretation": "Ranks conditional changes across the supplied input widths, not probabilities or universal parameter importance"},
        "pixel_measurements_verified": False}
    return {"status": "partial", "result": result,
        "assumptions": ["Rectilinear calibrated camera, square pixels, negligible residual distortion",
            "Locally horizontal ground, vertical object and identifiable point shadow",
            "Camera height, pitch, roll and principal point refer to the current pixel frame",
            "Camera yaw is not inferred; an independent bearing is required for absolute solar azimuth"],
        "warnings": warnings, "evidence": evidence}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("range", "shadow"))
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    evidence = []
    try:
        request = load_input(args.input)
        evidence.append({"kind": "measurement_request", **fingerprint(args.input)})
        payload = (range_measure if args.mode == "range" else shadow_measure)(request, args.input.resolve().parent)
        ensure_finite(payload)
        payload["evidence"] = evidence + payload.get("evidence", [])
    except InputError as exc:
        payload = {"status": exc.status, "result": {}, "warnings": [str(exc)], "evidence": evidence}
    except (OverflowError, ZeroDivisionError) as exc:
        payload = {"status": "invalid", "result": {},
                   "warnings": ["Input magnitude exceeds the stable numerical range"], "evidence": evidence}
    payload["workflow"] = "measure." + args.mode
    try:
        return emit(payload, args.out)
    except OSError as exc:
        payload["status"] = "unavailable"
        payload.setdefault("warnings", []).append(
            f"Cannot persist output artifact ({type(exc).__name__}); computed values are available only in stdout")
        return emit(payload)


if __name__ == "__main__":
    raise SystemExit(main())
