"""Offline workflow tests: no external requests, models or credentials required."""
import contextlib
import copy
import io
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import _street_sources as sources
import street
from _workflow import InputError


def scene():
    image = Image.new("RGB", (160, 120), "skyblue")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 40, 100, 119), fill="brown")
    draw.line((0, 0, 159, 119), fill="black", width=7)
    return image


def encoded(image=None):
    stream = io.BytesIO()
    (image or scene()).save(stream, format="PNG")
    return stream.getvalue()


def request():
    return {"candidates": [{"id": "A", "lat": 35, "lon": 139}],
            "providers": ["gsv", "baidu"], "view": {"heading_deg": 0, "hfov_deg": 90, "pitch_deg": 0},
            "radius_m": 50, "width": 640, "height": 480}


def metadata(provider="gsv"):
    return {"provider": provider, "provider_id": "test-pano", "projection": "perspective",
            "heading_deg": 0, "capture_date": "2020-04", "wgs84_lat_lon": [35, 139]}


class StreetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.request = street.prepare_request(request(), self.base, "fetch")

    def run_fetch(self, fetch):
        return street.run_workflow(self.request, self.base / "out", "fetch", fetch)

    def test_gray_placeholder_falls_back_and_preserves_failure(self):
        def fetch(provider, *_):
            yield (Image.new("RGB", (640, 480), (128, 128, 128)) if provider == "gsv" else scene()), metadata(provider)
        result = self.run_fetch(fetch)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"]["attempts"][0]["reason"], "constant_or_near_constant_image")
        accepted = result["result"]["images"][0]
        self.assertEqual(accepted["provider"], "baidu")
        self.assertEqual(accepted["heading_deg"], 0)
        self.assertEqual(accepted["capture_date"], "2020-04")
        self.assertEqual(len(accepted["sha256"]), 64)

    def test_network_failure_is_distinct_from_no_coverage_and_sanitized(self):
        def fetch(provider, *_):
            if provider == "gsv":
                raise RuntimeError("secret=do-not-persist https://service/?token=secret")
            raise sources.ProviderError("no_coverage")
            yield
        result = self.run_fetch(fetch)
        reasons = [a["reason"] for a in result["result"]["attempts"]]
        self.assertEqual(reasons, ["service_error", "no_coverage"])
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["result"]["match_handoff"])
        self.assertNotIn("do-not-persist", json.dumps(result))
        self.assertEqual(result["result"]["candidates"][0]["geographic_conclusion"], "not_tested")

    def test_empty_corrupt_tiny_and_constant_images_rejected(self):
        for value, reason in [(b"", "empty_image"), (b"bad jpg", "corrupt_or_unreadable_image"),
                              (Image.new("RGB", (10, 10)), "image_too_small"),
                              (Image.new("RGB", (100, 100), "white"), "constant_or_near_constant_image")]:
            with self.subTest(reason=reason), self.assertRaisesRegex(street.ImageRejected, reason):
                street.check_image(value)

    def test_partial_batch_and_current_run_only_handoff(self):
        query = self.base / "query.png"
        scene().save(query)
        self.request["query_image"] = str(query)
        self.request["candidates"].append({"id": "B", "lat": 36, "lon": 139})
        def fetch(provider, candidate, *_):
            if candidate["id"] == "A":
                yield scene(), metadata(provider)
            else:
                raise sources.ProviderError("no_coverage")
        first = self.run_fetch(fetch)
        second = self.run_fetch(fetch)
        self.assertEqual(first["status"], "partial")
        one, two = first["result"], second["result"]
        self.assertNotEqual(one["valid_image_dir"], two["valid_image_dir"])
        self.assertEqual(len(list(Path(two["valid_image_dir"]).glob("*.png"))), 1)
        self.assertIn(two["valid_image_dir"], two["match_handoff"]["argv"])
        self.assertNotIn(one["valid_image_dir"], two["match_handoff"]["argv"])
        self.assertFalse(two["match_handoff"]["executed"])

    def test_partial_write_never_enters_matcher_directory(self):
        query = self.base / "query.png"
        scene().save(query)
        self.request["query_image"] = str(query)
        self.request["candidates"].append({"id": "B", "lat": 36, "lon": 139})
        save = Image.Image.save
        writes = []
        calls = []
        def partial_save(image, path, *args, **kwargs):
            writes.append(Path(path))
            if len(writes) == 2:
                Path(path).write_bytes(b"partial PNG")
                raise OSError("simulated disk full")
            return save(image, path, *args, **kwargs)
        def fetch(provider, candidate, *_):
            calls.append((candidate["id"], provider))
            yield scene(), metadata(provider)
        with patch.object(Image.Image, "save", partial_save):
            result = self.run_fetch(fetch)
        self.assertEqual(result["status"], "partial")
        directory = Path(result["result"]["valid_image_dir"])
        self.assertEqual([p.name for p in directory.iterdir()], ["000.png"])
        street.check_image(directory / "000.png")
        self.assertEqual(result["result"]["attempts"][-1]["reason"], "artifact_write_failed")
        self.assertEqual(result["result"]["attempts"][-1]["error_type"], "OSError")
        self.assertEqual(calls, [("A", "gsv"), ("B", "gsv")])
        self.assertTrue(all(path.parent != directory for path in writes))
        self.assertTrue(all(not path.exists() for path in writes))
        self.assertIn(str(directory), result["result"]["match_handoff"]["argv"])

    def test_all_failure_rerun_cannot_reuse_previous_images(self):
        query = self.base / "query.png"
        scene().save(query)
        self.request["query_image"] = str(query)
        self.run_fetch(lambda *args: iter([(scene(), metadata())]))
        def unavailable(*_):
            raise sources.ProviderError("no_coverage")
        result = self.run_fetch(unavailable)
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["result"]["match_handoff"])
        self.assertEqual(list(Path(result["result"]["valid_image_dir"]).iterdir()), [])

    def test_input_rejected_before_network(self):
        invalid = []
        for mutation in [lambda r: r.update(candidates=[]), lambda r: r["candidates"][0].update(lat=True),
                         lambda r: r["candidates"][0].update(lon=181), lambda r: r.update(providers=["gsv", "gsv"]),
                         lambda r: r["view"].update(hfov_deg=180), lambda r: r.update(width=2),
                         lambda r: r.update(radius_m=float("nan")), lambda r: r.update(width=100.5), lambda r: r.update(radius_m=10 ** 400),
                         lambda r: r.update(candidates=r["candidates"] * 11)]:
            value = request()
            mutation(value)
            invalid.append(value)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(InputError):
                street.prepare_request(value, self.base, "fetch")

    def test_missing_view_needs_input_and_relative_query_resolves(self):
        value = request()
        del value["view"]
        with self.assertRaises(InputError) as ctx:
            street.prepare_request(value, self.base, "fetch")
        self.assertEqual(ctx.exception.status, "needs_input")
        scene().save(self.base / "q.png")
        value = {**request(), "query_image": "q.png"}
        self.assertEqual(street.prepare_request(value, self.base, "fetch")["query_image"], str(self.base / "q.png"))

    def test_cli_invalid_json_emits_nonzero_structured_manifest(self):
        path = self.base / "input.json"
        path.write_text('{"candidates": NaN}')
        output = self.base / "out"
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture):
            code = street.main(["fetch", "--input", str(path), "--out-dir", str(output)])
        self.assertEqual(code, 2)
        self.assertEqual((output / "manifest.json").read_text(), capture.getvalue())
        self.assertEqual(json.loads(capture.getvalue())["status"], "invalid")

    def test_cli_rerun_retains_immutable_manifest_and_request_fingerprint(self):
        path = self.base / "input.json"
        output = self.base / "out"
        manifests = []
        def fetch(provider, *_):
            yield scene(), metadata(provider)
        for provider in ("gsv", "baidu"):
            data = request()
            data["providers"] = [provider]
            path.write_text(json.dumps(data))
            capture = io.StringIO()
            with patch.object(street, "fetch_provider", fetch), contextlib.redirect_stdout(capture):
                code = street.main(["fetch", "--input", str(path), "--out-dir", str(output)])
            self.assertEqual(code, 0)
            latest = json.loads(capture.getvalue())  # exactly one JSON document on stdout
            immutable = Path(latest["result"]["run_manifest"])
            self.assertEqual(immutable.read_text(), capture.getvalue())
            self.assertEqual((output / "manifest.json").read_text(), capture.getvalue())
            manifests.append(immutable)
        first, second = [json.loads(p.read_text()) for p in manifests]
        self.assertNotEqual(manifests[0], manifests[1])
        self.assertEqual(first["result"]["images"][0]["provider"], "gsv")
        self.assertEqual(second["result"]["images"][0]["provider"], "baidu")
        self.assertNotEqual(first["evidence"][0]["sha256"], second["evidence"][0]["sha256"])

    def test_validate_requires_perspective_and_tracks_original_hash(self):
        scene().save(self.base / "good.png")
        raw = {"images": [{"id": "native", "path": "good.png", "projection": "perspective"},
                          {"id": "pano", "path": "good.png", "projection": "equirectangular"}]}
        prepared = street.prepare_request(raw, self.base, "validate")
        result = street.run_workflow(prepared, self.base / "out", "validate")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["result"]["images"][0]["source_file"]["path"], str(self.base / "good.png"))
        self.assertEqual(result["result"]["attempts"][1]["reason"], "unsupported_or_unknown_projection")

    def test_provider_specific_fov_conversion_and_zero_heading(self):
        found = {"id": "test-pano", "wgs": [35, 139], "date": "2021-01"}
        with patch("gsv.near", return_value=found), patch("gsv.render", return_value=scene()) as render:
            pair = list(sources.fetch_provider("gsv", self.request["candidates"][0], self.request, self.base, sources.DownloadBudget()))[0]
            self.assertEqual(render.call_args.args[1:4], (0, 0, 90))
            self.assertEqual(pair[1]["renderer_fov_axis"], "horizontal")
            self.assertEqual(pair[1]["capture_date"], "2021-01")
        with patch("baidu_pano.near", return_value=found), patch("baidu_pano.render", return_value=scene()) as render:
            pair = list(sources.fetch_provider("baidu", self.request["candidates"][0], self.request, self.base, sources.DownloadBudget()))[0]
            self.assertAlmostEqual(render.call_args.args[3], 73.73979529168804)
            self.assertEqual(pair[1]["hfov_deg"], 90)
            self.assertEqual(pair[1]["renderer_fov_axis"], "vertical")

    def test_legacy_ambiguous_none_is_not_asserted_no_coverage(self):
        with patch("baidu_pano.near", return_value=None):
            with self.assertRaisesRegex(sources.ProviderError, "lookup_empty_or_failed"):
                list(sources.fetch_provider("baidu", self.request["candidates"][0], self.request, self.base, sources.DownloadBudget()))
        with patch("gsv.near", return_value=None):
            with self.assertRaisesRegex(sources.ProviderError, "lookup_empty_or_failed"):
                list(sources.fetch_provider("gsv", self.request["candidates"][0], self.request, self.base, sources.DownloadBudget()))

    def test_gsv_unexpected_legacy_schema_is_not_reported_no_coverage(self):
        self.request["providers"] = ["gsv"]
        # Exercise the real legacy parser, which returns None for this layout.
        with patch("gsv._curl", return_value=b'[[0], []]'):
            result = self.run_fetch(sources.fetch_provider)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["result"]["attempts"][0]["reason"], "lookup_empty_or_failed")
        self.assertEqual(result["result"]["candidates"][0]["geographic_conclusion"], "not_tested")
        self.assertIsNone(result["result"]["match_handoff"])

    def test_mapillary_missing_token_never_calls_network(self):
        self.request["providers"] = ["mapillary"]
        with patch.dict(os.environ, {"MAPILLARY_TOKEN": ""}), patch.object(sources, "fetch_bytes") as network:
            result = self.run_fetch(sources.fetch_provider)
        network.assert_not_called()
        self.assertEqual(result["result"]["attempts"][0]["reason"], "missing_token")

    def mapillary_row(self, **kwargs):
        return {"id": "123", "geometry": {"coordinates": [139, 35]}, "camera_type": "perspective",
                "compass_angle": 180, "computed_compass_angle": 0, "captured_at": 1577836800000,
                "thumb_1024_url": "https://images.example/native.png", **kwargs}

    def test_mapillary_preserves_zero_date_projection_and_unknown_fov(self):
        rows = [self.mapillary_row(id="bad", camera_type="spherical"),
                self.mapillary_row(id="wrong", computed_compass_angle=180), self.mapillary_row()]
        self.request["providers"] = ["mapillary"]
        with patch.dict(os.environ, {"MAPILLARY_TOKEN": "private-token"}), patch.object(sources, "fetch_bytes", side_effect=[json.dumps({"data": rows}).encode(), encoded()]) as fetch:
            result = self.run_fetch(sources.fetch_provider)
        self.assertEqual(fetch.call_count, 2)
        self.assertNotIn("private-token", fetch.call_args_list[0].args[0])
        self.assertEqual(fetch.call_args_list[0].kwargs["headers"]["Authorization"], "OAuth private-token")
        self.assertNotIn("private-token", json.dumps(result))
        image = result["result"]["images"][0]
        self.assertEqual(image["provider_id"], "123")
        self.assertEqual(image["heading_deg"], 0)
        self.assertIsNone(image["hfov_deg"])
        self.assertIsNone(image["pitch_deg"])
        self.assertEqual(image["capture_date"], "2020-01-01T00:00:00+00:00")
        self.assertEqual(image["projection"], "perspective")

    def test_mapillary_toward_filters_actual_camera_heading(self):
        self.request["view"] = {"toward": {"lat": 35, "lon": 139.001}, "hfov_deg": 90,
                                "pitch_deg": 0, "heading_tolerance_deg": 20}
        rows = [self.mapillary_row(id="north", computed_compass_angle=0),
                self.mapillary_row(id="east", computed_compass_angle=90)]
        self.request["providers"] = ["mapillary"]
        with patch.dict(os.environ, {"MAPILLARY_TOKEN": "private-token"}), patch.object(sources, "fetch_bytes", side_effect=[json.dumps({"data": rows}).encode(), encoded()]):
            result = self.run_fetch(sources.fetch_provider)
        image = result["result"]["images"][0]
        self.assertEqual(image["provider_id"], "east")
        self.assertEqual(image["heading_deg"], 90)

    def test_mapillary_bounded_downloads_and_retry_after_corrupt_pixels(self):
        rows = [self.mapillary_row(id=str(i)) for i in range(25)]
        self.request["providers"] = ["mapillary"]
        def response(url, **kwargs):
            return json.dumps({"data": rows, "paging": {"next": "do-not-fetch"}}).encode() if "graph.mapillary.com" in url else b"corrupt"
        with patch.dict(os.environ, {"MAPILLARY_TOKEN": "private-token"}), patch.object(sources, "fetch_bytes", side_effect=response) as fetch:
            result = self.run_fetch(sources.fetch_provider)
        self.assertEqual(fetch.call_count, 4)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(len(result["result"]["attempts"]), 3)

    def test_shared_budget_caps_image_downloads_across_providers(self):
        found = {"id": "test-pano", "wgs": [35, 139]}
        rows = [self.mapillary_row(id=str(i)) for i in range(3)]
        self.request["providers"] = ["gsv", "baidu", "mapillary"]
        gray = Image.new("RGB", (640, 480), "gray")
        with patch("gsv.near", return_value=found), patch("gsv.render", return_value=gray), patch("baidu_pano.near", return_value=found), patch("baidu_pano.render", return_value=gray), patch.dict(os.environ, {"MAPILLARY_TOKEN": "private-token"}), patch.object(sources, "fetch_bytes", side_effect=[json.dumps({"data": rows}).encode(), encoded(gray)]) as fetch:
            result = self.run_fetch(sources.fetch_provider)
        self.assertEqual(fetch.call_count, 2)  # one metadata query and only one image remains in the shared budget
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["result"]["attempts"][-1]["reason"], "download_budget_exhausted")


if __name__ == "__main__":
    unittest.main()
