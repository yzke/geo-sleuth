#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["Pillow"]
# ///
"""Offline measurement regressions with independently projected synthetic scenes."""
from __future__ import annotations

import contextlib
import io
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def project_world(point, *, focal=1200.0, width=1600, height=1200,
                  camera_height=1.6, pitch=0.0, roll=0.0):
    """Independent world-to-image projection; world axes are right/up/forward.

    Build an orthonormal camera basis, then take dot products (the production
    implementation unprojects rays). Positive pitch looks upward; positive
    roll rotates camera right toward world down at zero pitch.
    """
    p, r = math.radians(pitch), math.radians(roll)
    right = (math.cos(r), -math.sin(r) * math.cos(p), math.sin(r) * math.sin(p))
    up = (math.sin(r), math.cos(r) * math.cos(p), -math.cos(r) * math.sin(p))
    forward = (0.0, math.sin(p), math.cos(p))
    q = (point[0], point[1] - camera_height, point[2])
    dot = lambda a, b: sum(x * y for x, y in zip(a, b))
    z = dot(q, forward)
    if z <= 0:
        raise ValueError("Synthetic point is behind the camera")
    return [width / 2 + focal * dot(q, right) / z,
            height / 2 - focal * dot(q, up) / z]


def run_measure(mode, request, cwd=None):
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "request.json"
        path.write_text(json.dumps(request), encoding="utf-8")
        result = subprocess.run([sys.executable, str(SCRIPTS / "measure.py"), mode,
                                 "--input", str(path)], text=True, capture_output=True,
                                cwd=cwd)
        return result.returncode, json.loads(result.stdout)


def range_request():
    return {
        "image": {"width": 1600, "height": 1200, "crop": "none",
                  "source": "Synthetic source", "geometry": "rectilinear_square_pixels",
                  "pixel_frame": "exif_transposed"},
        "camera": {"focal_px": 1200, "focal_source": "Synthetic calibration",
                   "calibration_frame": "current_image"},
        "target": {"size_m": 2.0, "size_px": 120, "geometry": "frontoparallel",
                   "size_source": "Synthetic dimension", "pixel_source": "Synthetic marking"},
    }


class InputContract(unittest.TestCase):
    def test_missing_focal_does_not_invent_calibration(self):
        request = range_request()
        request["camera"] = {}
        code, result = run_measure("range", request)
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "needs_input")

    def test_unknown_crop_does_not_turn_exif_into_current_calibration(self):
        request = range_request()
        request["image"]["crop"] = "unknown"
        request["camera"] = {"equiv_focal_mm": 35, "focal_source": "EXIF"}
        code, result = run_measure("range", request)
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "needs_input")

    def test_nonfinite_and_reversed_inputs_are_invalid(self):
        for size in (float("nan"), [3, 2], [-1, 1]):
            with self.subTest(size=size):
                request = range_request()
                request["target"]["size_m"] = size
                code, result = run_measure("range", request)
                self.assertEqual(code, 2)
                self.assertEqual(result["status"], "invalid")

    def test_extreme_finite_inputs_do_not_emit_infinity(self):
        request = range_request()
        request["target"]["size_m"] = 1e308
        request["camera"]["focal_px"] = 1e4
        code, result = run_measure("range", request)
        self.assertEqual((code, result["status"]), (2, "invalid"))

    def test_output_directory_is_structured_unavailable(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            request_path = directory / "request.json"
            request_path.write_text(json.dumps(range_request()))
            process = subprocess.run([sys.executable, str(SCRIPTS / "measure.py"), "range",
                                      "--input", str(request_path), "--out", str(directory)],
                                     capture_output=True, text=True)
            result = json.loads(process.stdout)
            self.assertEqual((process.returncode, result["status"]), (3, "unavailable"))
            self.assertEqual(process.stderr, "")
            self.assertAlmostEqual(result["result"]["nominal_depth_m"], 20.0)
            self.assertTrue(any("stdout" in w for w in result["warnings"]))

    def test_unwritable_output_parent_is_structured_unavailable(self):
        import measure
        with tempfile.TemporaryDirectory() as temporary:
            request_path = Path(temporary) / "request.json"
            request_path.write_text(json.dumps(range_request()))
            stream = io.StringIO()
            with mock.patch("pathlib.Path.mkdir", side_effect=PermissionError("private path omitted")):
                with contextlib.redirect_stdout(stream):
                    code = measure.main(["range", "--input", str(request_path), "--out", str(Path(temporary) / "new" / "result.json")])
            result = json.loads(stream.getvalue())
            self.assertEqual((code, result["status"]), (3, "unavailable"))
            self.assertNotIn("private path omitted", stream.getvalue())


def shadow_request(*, foot=(3.0, 0.0, 20.0), tip=(7.0, 0.0, 23.0),
                   object_height=2.0, pitch=-8.0, roll=12.0):
    request = range_request()
    request.pop("target")
    request["ground"] = "horizontal"
    request["camera"].update({
        "principal_point_px": [800, 600], "principal_point_source": "Synthetic calibration",
        "height_m": 1.6, "height_source": "Synthetic tripod position",
        "pitch_deg": pitch, "pitch_source": "Synthetic camera pose",
        "roll_deg": roll, "roll_source": "Synthetic camera pose",
    })
    request["object"] = {"height_m": object_height, "height_source": "Synthetic object",
                         "geometry": "vertical"}
    top = (foot[0], object_height, foot[2])
    request["points"] = {"source": "Independent synthetic scene projection",
                         **{name: project_world(point, pitch=pitch, roll=roll)
                            for name, point in (("foot", foot), ("head", top), ("shadow_tip", tip))}}
    return request


class Geometry(unittest.TestCase):
    def test_equivalent_focal_and_depth_are_invariant_under_frame_rotation(self):
        measurements = []
        for width, height in ((1600, 1200), (1200, 1600)):
            with self.subTest(width=width, height=height):
                request = range_request()
                request["image"].update(width=width, height=height)
                request["camera"] = {"equiv_focal_mm": [34, 36],
                                     "focal_source": "Synthetic original-frame EXIF equivalent focal length"}
                code, payload = run_measure("range", request)
                self.assertEqual(code, 0, payload)
                result = payload["result"]
                expected = [value * 2000 / math.sqrt(36 ** 2 + 24 ** 2) for value in (34, 36)]
                for actual, focal in zip(result["focal_px"], expected):
                    self.assertAlmostEqual(actual, focal)
                for actual, focal in zip(result["depth_m"], expected):
                    self.assertAlmostEqual(actual, 2 * focal / 120)
                measurements.append(result)
        self.assertEqual(measurements[0]["focal_px"], measurements[1]["focal_px"])
        for landscape, portrait in zip(measurements[0]["depth_m"], measurements[1]["depth_m"]):
            self.assertAlmostEqual(landscape, portrait)
        self.assertAlmostEqual(measurements[0]["nominal_depth_m"], measurements[1]["nominal_depth_m"])

    def test_range_exact_and_monotonic_bounds(self):
        request = range_request()
        code, result = run_measure("range", request)
        self.assertEqual(code, 0)
        self.assertAlmostEqual(result["result"]["nominal_depth_m"], 20.0)
        request["target"]["size_m"] = [1.98, 2.02]
        request["target"]["size_px"] = [118, 122]
        request["camera"]["focal_px"] = [1190, 1210]
        code, result = run_measure("range", request)
        self.assertEqual(code, 0)
        bounds = result["result"]["depth_m"]
        self.assertAlmostEqual(bounds[0], 1.98 * 1190 / 122)
        self.assertAlmostEqual(bounds[1], 2.02 * 1210 / 118)
        self.assertFalse(result["result"]["pixel_measurements_verified"])

    def test_off_axis_pitch_roll_and_toward_away_shadows(self):
        cases = [((7, 0, 23), -8, 12), ((3, 0, 25), 0, 0),
                 ((3, 0, 15), 0, 0), ((-1, 0, 17), 7, -18)]
        for tip, pitch, roll in cases:
            with self.subTest(tip=tip, pitch=pitch, roll=roll):
                request = shadow_request(tip=tip, pitch=pitch, roll=roll)
                code, result = run_measure("shadow", request)
                self.assertEqual(code, 0, result)
                expected_length = math.hypot(tip[0] - 3, tip[2] - 20)
                nominal = result["result"]["nominal"]
                self.assertAlmostEqual(nominal["shadow_length_m"], expected_length, places=9)
                self.assertAlmostEqual(nominal["solar_elevation_deg"], math.degrees(math.atan2(2, expected_length)), places=9)
                self.assertAlmostEqual(nominal["head_residual_px"], 0, places=8)

    def test_horizon_and_uncertain_crossing_require_input(self):
        request = shadow_request(foot=(0, 0, 100), tip=(5, 0, 100), pitch=0, roll=0)
        code, result = run_measure("shadow", request)
        self.assertEqual((code, result["status"]), (2, "needs_input"))
        request = shadow_request(pitch=0, roll=0)
        request["points"]["shadow_tip"][1] = [605, 761.9565]
        code, result = run_measure("shadow", request)
        self.assertEqual((code, result["status"]), (2, "needs_input"))

    def test_top_inconsistency_and_missing_attitude_require_input(self):
        request = shadow_request()
        request["points"]["head"][0] += 30
        code, result = run_measure("shadow", request)
        self.assertEqual((code, result["status"]), (2, "needs_input"))
        request = shadow_request()
        del request["camera"]["pitch_deg"]
        code, result = run_measure("shadow", request)
        self.assertEqual((code, result["status"]), (2, "needs_input"))

    def test_sensitivity_is_reproducible_and_explicitly_not_confidence(self):
        request = shadow_request()
        request["camera"]["height_m"] = [1.58, 1.62]
        request["object"]["height_m"] = [1.98, 2.02]
        first = run_measure("shadow", request)
        second = run_measure("shadow", request)
        self.assertEqual(first[0], 0, first[1])
        self.assertEqual(first[1]["result"], second[1]["result"])
        result = first[1]["result"]
        self.assertEqual(result["bounds_kind"], "sampled_sensitivity_envelope")
        self.assertLess(result["solar_elevation_deg"][0], result["solar_elevation_deg"][1])
        self.assertTrue(any("not a confidence interval" in w for w in first[1]["warnings"]))

    def test_sensitivity_ranks_uncertainty_that_changes_elevation_most(self):
        request = shadow_request()
        request["camera"]["height_m"] = [1.599, 1.601]
        request["object"]["height_m"] = [1.99, 2.01]
        code, payload = run_measure("shadow", request)
        self.assertEqual(code, 0, payload)
        ranking = payload["result"]["sensitivity"]["ranked_parameters"]
        self.assertEqual(ranking[0]["parameter"], "object.height_m")
        self.assertGreater(ranking[0]["solar_elevation_span_deg"], ranking[1]["solar_elevation_span_deg"])
        self.assertEqual(ranking[0]["accepted_endpoints"], 2)
        self.assertEqual(ranking[1]["accepted_endpoints"], 2)


class EvidenceImage(unittest.TestCase):
    def test_relative_image_exif_dimensions_and_fingerprint(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            image = Image.new("RGB", (1200, 1600), (1, 2, 3))
            exif = Image.Exif()
            exif[274] = 6
            image.save(directory / "source.jpg", exif=exif)
            request = range_request()
            request["image"]["path"] = "source.jpg"
            request_path = directory / "request.json"
            request_path.write_text(json.dumps(request))
            def call():
                process = subprocess.run([sys.executable, str(SCRIPTS / "measure.py"), "range", "--input", str(request_path)],
                                         capture_output=True, text=True, cwd="/")
                return process.returncode, json.loads(process.stdout)
            code, result = call()
            self.assertEqual(code, 0, result)
            evidence = [row for row in result["evidence"] if row["kind"] == "source_image"]
            self.assertEqual(len(evidence), 1)
            self.assertEqual(len(evidence[0]["sha256"]), 64)
            self.assertFalse(result["result"]["pixel_measurements_verified"])
            request["image"]["width"] = 1599
            request_path.write_text(json.dumps(request))
            code, result = call()
            self.assertEqual((code, result["status"]), (2, "invalid"))
            self.assertIn("dimensions", result["warnings"][0])


if __name__ == "__main__":
    unittest.main()
