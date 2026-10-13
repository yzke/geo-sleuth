#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Offline intersection geometry/provenance regressions; no Overpass requests."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import osm


def way(ident, points, **tags):
    return {"type": "way", "id": ident, "tags": tags,
            "geometry": [{"lat": lat, "lon": lon} for lat, lon in points]}


class IntersectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def query(self, elements, **overrides):
        args = SimpleNamespace(
            a='["highway"="motorway"]', b='["power"="line"]',
            area=None, bbox=(-1, -1, 1, 1), proxy=None, cache=self.root / "cache",
            bend_min=0, bend_within="100:1000", bend_filter=False,
            rank_near=None, ring=None, cluster=100, out=self.root / "points.json",
            report=self.root / "report.json",
        )
        for name, value in overrides.items():
            setattr(args, name, value)
        with patch.object(osm, "run", return_value={"elements": elements}), redirect_stdout(io.StringIO()):
            points = osm.cmd_intersect(args)
        report = json.loads(args.report.read_text(encoding="utf-8")) if args.report else None
        return points, report

    def test_long_segment_interiors_intersect_without_shared_endpoint_cells(self):
        points, report = self.query([
            way(1, [(0, -0.03), (0, 0.03)], highway="motorway"),
            way(2, [(-0.03, 0), (0.03, 0)], power="line"),
        ])
        self.assertEqual(len(points), 1)
        crossing = report["crossings"][0]
        self.assertEqual(crossing["ll"], [0.0, 0.0])
        self.assertAlmostEqual(crossing["a"]["bearing_deg"], 90)
        self.assertAlmostEqual(crossing["b"]["bearing_deg"], 0)
        self.assertAlmostEqual(crossing["angle_deg"], 90)

    def test_dual_carriageways_keep_both_real_crossings_in_one_cluster(self):
        points, report = self.query([
            way(1, [(-0.00015, -0.02), (-0.00015, 0.02)], highway="motorway"),
            way(2, [(0.00015, -0.02), (0.00015, 0.02)], highway="motorway"),
            way(3, [(-0.02, 0), (0.02, 0)], power="line", voltage="500000", wires="quad"),
        ])
        self.assertEqual(len(points), 1)
        self.assertEqual(len(report["crossings"]), 2)
        cluster = report["clusters"][0]
        self.assertEqual(len(cluster["crossing_ids"]), 2)
        self.assertEqual(cluster["representative_ll"], [0.0, 0.0])
        self.assertNotIn(cluster["representative_ll"], [c["ll"] for c in report["crossings"]])
        self.assertEqual(report["representative_role"], "search_cluster_center_not_camera")
        self.assertEqual(set(points), {cluster["name"]})

    def test_neighboring_power_lines_keep_ids_and_all_tags_without_voltage_inference(self):
        points, report = self.query([
            way(1, [(0, -0.02), (0, 0.02)], highway="motorway", ref="G-test"),
            way(2, [(-0.02, -0.0002), (0.02, -0.0002)], power="line", name="Known", voltage="500000;220000", wires="quad", circuits="2"),
            way(3, [(-0.02, 0.0002), (0.02, 0.0002)], power="line", name="Unknown", operator="Fixture Grid"),
        ])
        self.assertEqual(len(points), 1)
        by_id = {c["b"]["id"]: c["b"]["tags"] for c in report["crossings"]}
        self.assertEqual(set(by_id), {2, 3})
        self.assertEqual(by_id[2]["voltage"], "500000;220000")
        self.assertEqual(by_id[2]["wires"], "quad")
        self.assertEqual(by_id[2]["circuits"], "2")
        self.assertEqual(by_id[3]["operator"], "Fixture Grid")
        self.assertNotIn("voltage", by_id[3])
        self.assertNotIn("wires", by_id[3])

    def test_repeated_elements_and_vertex_hits_do_not_duplicate_crossings(self):
        road = way(1, [(0, -0.03), (0, 0), (0, 0.03)], highway="motorway")
        power = way(2, [(-0.03, 0), (0, 0), (0.03, 0)], power="line")
        _, report = self.query([road, power, road, power])
        self.assertEqual(len(report["crossings"]), 1)
        crossing = report["crossings"][0]
        self.assertEqual(len(crossing["segment_pairs"]), 4)
        self.assertEqual(report["clusters"][0]["crossing_ids"], [crossing["id"]])

    def test_nearby_nonintersecting_segments_do_not_become_hits(self):
        points, report = self.query([
            way(1, [(0, -0.02), (0, 0.02)], highway="motorway"),
            way(2, [(0.0001, 0), (0.02, 0)], power="line"),
        ])
        self.assertEqual(points, {})
        self.assertEqual(report["crossings"], [])
        self.assertEqual(report["clusters"], [])

    def test_empty_source_writes_fresh_report(self):
        (self.root / "report.json").write_text('{"stale": true}', encoding="utf-8")
        points, report = self.query([])
        self.assertEqual(points, {})
        self.assertEqual(report["crossings"], [])
        self.assertNotIn("stale", report)

    def test_ring_filtered_clusters_keep_members_and_reason_in_report(self):
        points, report = self.query([
            way(1, [(0, -0.02), (0, 0.02)], highway="motorway"),
            way(2, [(-0.02, 0), (0.02, 0)], power="line"),
        ], ring="0,0:100:200")
        self.assertEqual(points, {})
        self.assertEqual(len(report["crossings"]), 1)
        self.assertFalse(report["clusters"][0]["included"])
        self.assertEqual(report["clusters"][0]["filtered_by"], "ring")

    def test_legacy_point_output_does_not_require_report(self):
        points, report = self.query([
            way(1, [(0, -0.03), (0, 0.03)], highway="motorway"),
            way(2, [(-0.03, 0), (0.03, 0)], power="line"),
        ], report=None)
        self.assertEqual(list(points.values()), [[0.0, 0.0]])
        self.assertIsNone(report)

    def test_highway_regex_does_not_misclassify_power_lines_as_roads(self):
        points, report = self.query([
            way(1, [(0, -0.03), (0, 0.03)], highway="motorway"),
            way(2, [(-0.03, 0), (0.03, 0)], power="line"),
        ], a='["highway"~"^(motorway|trunk)$"]')
        self.assertEqual(len(points), 1)
        self.assertEqual(report["crossings"][0]["a"]["id"], 1)
        self.assertEqual(report["crossings"][0]["b"]["id"], 2)

    def test_grid_cover_stays_linear_in_segment_length(self):
        cells = osm._segment_cells((0, 0), (1_000_000, 1_000_000), pad=True)
        self.assertLess(len(cells), 25_000)  # A full bbox fill would need >4 million.
        self.assertIn((1000, 1000), cells)
        with self.assertRaises(SystemExit):
            osm._segment_cells((0, 0), (3_000_000, 0), pad=True)

    def test_first_vertex_bend_distance_starts_at_actual_crossing(self):
        points, report = self.query([
            way(1, [(0, -0.02), (0, 0.02)], highway="motorway"),
            way(2, [(-0.01, 0), (0.002, 0), (0.002, 0.005)], power="line"),
        ], bend_min=25)
        self.assertEqual(len(points), 1)
        self.assertEqual(report["crossings"][0]["bend"], {"distance_m": 221, "turn_deg": 90})
        self.assertEqual(report["clusters"][0]["bend"], {"distance_m": 221, "turn_deg": 90})

    def test_unsupported_filters_fail_before_query(self):
        for invalid in ('["highway"!="service"]', '[~"^name"~"foo"]', '["highway"]garbage'):
            with self.subTest(filter=invalid), patch.object(osm, "_scope") as scope:
                with self.assertRaisesRegex(SystemExit, "Unsupported intersect filter"):
                    self.query([], a=invalid)
                scope.assert_not_called()

    def test_escaped_regex_and_case_flag_are_respected(self):
        filt = '["name"~"^grid\\\\.line$",i]'
        osm._validate_intersect_filter(filt)
        self.assertTrue(osm._match({"tags": {"name": "GRID.LINE"}}, filt))
        self.assertFalse(osm._match({"tags": {"name": "GRIDxLINE"}}, filt))

    def test_report_and_points_cannot_overwrite_each_other(self):
        with self.assertRaisesRegex(SystemExit, "different files"):
            self.query([], report=self.root / "points.json")


if __name__ == "__main__":
    unittest.main()
