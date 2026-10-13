#!/usr/bin/env python3
"""Offline CLI regressions for infrastructure omitted from precise conclusions."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


class InfrastructureGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "board.json"
        # Artifacts stand in for locally reviewed images/results; the board audits
        # file identity and workflow, and deliberately makes no vision claims.
        for name, text in (("photo.jpg", "original frame"), ("crop.jpg", "tower crop"),
                           ("comparison.json", '{"tower_order": "same"}'),
                           ("evidence.jpg", "comparison panels")):
            (self.root / name).write_text(text, encoding="utf-8")
        self.run_board("init", "--photo", "photo.jpg")
        self.run_board("add", "A", "B", "--level", "point")

    def run_board(self, *args, ok=True, cwd=None):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "board.py"), "--board", str(self.path), *args],
            cwd=cwd or self.root, capture_output=True, text=True, encoding="utf-8",
        )
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def board(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def change(self, edit):
        data = self.board()
        edit(data)
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def inspect(self, state="present", **kwargs):
        return self.run_board("inspect", "--infra", state, "--file", "photo.jpg",
                              "--why", "Reviewed the complete frame and tower crop", **kwargs)

    def clue(self, status="observed", kind="infra"):
        self.run_board("clue", "Two towers and bundled conductors crossing the road", "--kind", kind,
                       "--status", status, "--file", "crop.jpg")

    def verify(self, outcome="matched", candidate="A", clue="K1", **kwargs):
        return self.run_board("verify", "--candidate", candidate, "--clue", clue,
                              "--outcome", outcome, "--file", "comparison.json",
                              "--why", "Compared tower ordering, road crossing and viewing direction", **kwargs)

    def audit(self, *args):
        result = self.root / "result.json"
        self.run_board("report", "--merge", str(result), *args)
        return json.loads(result.read_text(encoding="utf-8"))["board"]["infrastructure_verification"]

    def prepare(self):
        self.inspect()
        self.clue()

    def test_missing_unclear_present_without_clue_and_absent(self):
        self.assertIn("inspection is missing", self.run_board("report", "--require-verified", ok=False).stdout)
        self.inspect("unclear")
        self.assertIn("visibility is unclear", self.run_board("report", "--require-verified", ok=False).stdout)
        self.inspect()
        self.assertIn("no observed/read", self.run_board("report", "--require-verified", ok=False).stdout)
        self.inspect("absent")
        self.run_board("report", "--require-verified")
        self.assertTrue(self.audit()["infrastructure_ready"])
        self.clue()
        self.assertIn("conflicts", self.run_board("report", "--require-verified", ok=False).stdout)
        before = self.path.read_bytes()
        self.inspect("absent", ok=False)
        self.assertEqual(self.path.read_bytes(), before)

    def test_scoring_and_resolves_never_complete_the_gate(self):
        self.prepare()
        self.run_board("evidence", "--clue", "K1", "--for", "A:5", "--why", "tower looks plausible")
        self.run_board("clue", "Voltage and route calculation", "--kind", "infra", "--status", "computed",
                       "--resolves", "K1", "--file", "comparison.json")
        self.assertTrue(self.board()["clues"]["K1"]["used"])
        audit = self.audit()
        self.assertFalse(audit["infrastructure_ready"])
        self.assertEqual(audit["candidates"][0]["checks"][0]["state"], "pending")
        self.run_board("report", "--require-verified", ok=False)
        # Default check must not print pass for a known, ignored tower either.
        # Add computed support to satisfy the old independent checks first.
        self.run_board("evidence", "--clue", "K2", "--for", "A:5")
        check = self.run_board("check")
        self.assertIn("FAIL infrastructure verification", check.stdout)
        self.assertIn("Result: has FAIL", check.stdout)

    def test_ready_main_does_not_require_alternatives_but_cannot_transfer(self):
        self.prepare()
        before = self.board()
        self.verify()
        after = self.board()
        self.assertEqual(after["evidence"], before["evidence"])
        self.assertEqual(after["candidates"], before["candidates"])
        self.assertEqual(after["clues"], before["clues"])
        audit = self.audit("--require-verified")
        self.assertTrue(audit["infrastructure_ready"])
        self.assertFalse(audit["candidates"][1]["infrastructure_ready"])
        # Make B first place: A's completed comparison cannot license the new main.
        self.run_board("evidence", "--clue", "K1", "--for", "B:5")
        self.run_board("report", "--require-verified", ok=False)
        self.assertIn("not first place", self.run_board("report", "--require-verified", "--candidate", "A", ok=False).stderr)

    def test_outcomes_are_latest_candidate_specific_and_never_exclude(self):
        self.prepare()
        self.verify()
        for outcome in ("contradicted", "inconclusive", "not-applicable"):
            with self.subTest(outcome=outcome):
                self.verify(outcome)
                audit = self.audit()
                self.assertFalse(audit["infrastructure_ready"])
                self.assertEqual(audit["candidates"][0]["checks"][0]["state"], outcome)
                self.run_board("report", "--require-verified", ok=False)
                self.assertEqual(self.board()["candidates"]["A"]["status"], "open")
        self.verify()
        self.run_board("report", "--require-verified")

    def test_invalid_records_never_mutate_board(self):
        self.prepare()
        cases = [
            ("inspect", "--infra", "present", "--file", "missing.jpg", "--why", "reviewed"),
            ("inspect", "--infra", "present", "--file", ".", "--why", "reviewed"),
            ("inspect", "--infra", "present", "--file", "photo.jpg", "--why", "  "),
            ("verify", "--candidate", "Missing", "--clue", "K1", "--outcome", "matched", "--file", "comparison.json", "--why", "reviewed"),
            ("verify", "--candidate", "A", "--clue", "K99", "--outcome", "matched", "--file", "comparison.json", "--why", "reviewed"),
            ("verify", "--candidate", "A", "--clue", "K1", "--outcome", "matched", "--file", "missing.json", "--why", "reviewed"),
            ("verify", "--candidate", "A", "--clue", "K1", "--outcome", "matched", "--file", "comparison.json", "--why", " "),
        ]
        before = self.path.read_bytes()
        for args in cases:
            with self.subTest(args=args):
                self.run_board(*args, ok=False)
                self.assertEqual(self.path.read_bytes(), before)
        for status in ("inferred", "computed"):
            self.clue(status)
            before = self.path.read_bytes()
            self.verify(clue=f"K{len(self.board()['clues'])}", ok=False)
            self.assertEqual(self.path.read_bytes(), before)
        self.clue("read", "terrain")
        before = self.path.read_bytes()
        self.verify(clue=f"K{len(self.board()['clues'])}", ok=False)
        self.assertEqual(self.path.read_bytes(), before)

    def test_changed_or_deleted_comparison_is_stale_without_old_record_fallback(self):
        self.prepare()
        self.verify()
        self.verify()
        file = self.root / "comparison.json"
        file.write_text("different result", encoding="utf-8")
        self.assertEqual(self.audit()["candidates"][0]["checks"][0]["state"], "stale")
        self.run_board("report", "--require-verified", ok=False)
        file.unlink()
        self.assertIn("artifact unavailable", self.run_board("report", "--require-verified", ok=False).stdout)

    def test_photo_clue_source_and_candidate_changes_require_new_review(self):
        self.prepare()
        self.verify()
        for path in ("photo.jpg", "crop.jpg"):
            with self.subTest(path=path):
                file = self.root / path
                saved = file.read_bytes()
                file.write_bytes(saved + b"edited")
                self.run_board("report", "--require-verified", ok=False)
                file.write_bytes(saved)
        self.change(lambda b: b["clues"]["K1"].update(text="Three towers, not two"))
        self.assertIn("clue inputs", self.run_board("report", "--require-verified", ok=False).stdout)
        self.verify()
        self.change(lambda b: b["candidates"]["A"].update(center=[40, 110]))
        self.assertIn("candidate inputs", self.run_board("report", "--require-verified", ok=False).stdout)
        self.verify()
        self.inspect()
        self.assertIn("inspection changed", self.run_board("report", "--require-verified", ok=False).stdout)

    def test_strict_check_honors_other_failures_even_when_infrastructure_ready(self):
        self.prepare()
        self.verify()
        self.run_board("report", "--require-verified")
        failed = self.run_board("check", "--require-verified", ok=False)
        self.assertIn("no read/computed evidence", failed.stdout)
        # The old default exits zero while reporting FAIL; retain this compatibility.
        self.run_board("check")
        self.run_board("clue", "Independent geometry test", "--kind", "terrain", "--status", "computed", "--file", "comparison.json")
        self.run_board("evidence", "--clue", "K2", "--for", "A:5")
        self.run_board("check", "--require-verified")

    def test_strict_report_does_not_write_blocked_merge_and_prints_all_blockers(self):
        self.prepare()
        self.change(lambda b: b["clues"]["K1"].update(text="many details " * 1000))
        output = self.root / "result.json"
        output.write_text('{"original": true}', encoding="utf-8")
        before = output.read_bytes()
        result = self.run_board("report", "--require-verified", "--merge", str(output), ok=False)
        self.assertEqual(output.read_bytes(), before)
        self.assertIn("INFRA BLOCKER A: K1: no candidate-specific comparison", result.stdout)
        self.assertIn('"history"', result.stdout)

    def test_legacy_board_and_no_fine_target_do_not_silently_pass_strict(self):
        self.change(lambda b: b.pop("photo_path"))
        before = self.path.read_bytes()
        self.run_board("report")
        self.run_board("report", "--require-verified", ok=False)
        self.assertEqual(self.path.read_bytes(), before)
        self.change(lambda b: b.update(candidates={}))
        self.inspect("absent")
        self.assertIn("needs an active area/road/point", self.run_board("report", "--require-verified", ok=False).stdout)

    def test_road_verification_cannot_license_a_point_and_explicit_target_sets_main(self):
        self.run_board("add", "Road", "OtherRoad", "--level", "road")
        self.run_board("move", "A", "--parent", "Road")
        self.run_board("move", "B", "--parent", "OtherRoad")
        self.prepare()
        self.verify(candidate="Road")
        self.run_board("report", "--candidate", "Road", "--require-verified")
        self.run_board("report", "--candidate", "A", "--require-verified", ok=False)
        self.verify(candidate="A")
        self.audit("--candidate", "A", "--require-verified")
        result = json.loads((self.root / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["board"]["main"]["name"], "A")
        self.assertEqual(result["board"]["infrastructure_verification"]["verification_target"], "A")
        self.change(lambda b: b["candidates"]["Road"].update(bbox=[1, 2, 3, 4]))
        self.assertIn("candidate inputs changed", self.run_board("report", "--candidate", "A", "--require-verified", ok=False).stdout)

    def test_paths_and_fingerprints_survive_another_working_directory(self):
        self.prepare()
        self.verify()
        record = self.board()["infrastructure_checks"][0]
        self.assertTrue(Path(record["artifact"]["path"]).is_absolute())
        self.assertEqual(len(record["artifact"]["sha256"]), 64)
        self.run_board("report", "--require-verified", cwd=self.root.parent)

    def test_large_board_stdout_is_bounded_and_last_target_blocker_is_first(self):
        self.run_board("add", *(f"Alternative-{i}" for i in range(98)), "--level", "point")
        self.prepare()
        self.verify()
        # Populate 100 valid records without paying for 99 identical CLI launches.
        # Every record has the same artifact but its own candidate identity.
        data = self.board()
        template = data["infrastructure_checks"][0]
        for name in list(data["candidates"])[1:]:
            record = json.loads(json.dumps(template))
            record["id"] = f"V{len(data['infrastructure_checks']) + 1}"
            record["candidate"] = name
            record["candidate_snapshot"]["chain"][0]["name"] = name
            data["infrastructure_checks"].append(record)
        self.path.write_text(json.dumps(data), encoding="utf-8")
        self.clue()  # Last target check remains pending and must be visible first.
        result = self.run_board("report", "--require-verified", ok=False)
        self.assertLess(len(result.stdout.encode("utf-8")), 20000)
        self.assertTrue(result.stdout.startswith("INFRA BLOCKER A: K2: no candidate-specific comparison"))
        self.assertIn('"checks": 100', result.stdout)
        self.assertIn('"other_candidates_total": 99', result.stdout)
        self.assertNotIn('"candidate_snapshot"', result.stdout)

    def test_strict_merge_rejects_coordinates_for_another_candidate_before_writing(self):
        self.change(lambda b: b["candidates"]["A"].update(bbox=[10, 20, 10.0001, 20.0001]))
        self.change(lambda b: b["candidates"]["B"].update(bbox=[30, 40, 30.0001, 40.0001]))
        self.prepare()
        self.verify()
        output = self.root / "precise.json"
        output.write_text(json.dumps({"lat": 30.00005, "lon": 40.00005, "radius_m": 5}), encoding="utf-8")
        before = output.read_bytes()
        result = self.run_board("report", "--candidate", "A", "--require-verified", "--merge", str(output), ok=False)
        self.assertEqual(output.read_bytes(), before)
        self.assertTrue(result.stdout.startswith("REPORT BLOCKER result.json lat/lon is outside"))
        default = self.run_board("report", "--candidate", "A", "--merge", str(output))
        self.assertIn("WARN result.json lat/lon is outside", default.stdout)
        output.write_text(json.dumps({"lat": 10.00005, "lon": 20.00005, "radius_m": 5}), encoding="utf-8")
        self.run_board("report", "--candidate", "A", "--require-verified", "--merge", str(output))

    def test_precise_merge_needs_point_scope_and_checkable_geometry(self):
        self.prepare()
        self.verify()
        output = self.root / "precise.json"
        output.write_text('{"lat": 10, "lon": 20, "radius_m": 5}', encoding="utf-8")
        before = output.read_bytes()
        result = self.run_board("report", "--candidate", "A", "--require-verified", "--merge", str(output), ok=False)
        self.assertIn("needs a valid bbox or center", result.stdout)
        self.assertEqual(output.read_bytes(), before)
        self.change(lambda b: b["candidates"]["A"].update(center=[10, 20]))
        self.verify()
        self.run_board("report", "--candidate", "A", "--require-verified", "--merge", str(output))
        self.run_board("add", "Road", "--level", "road", "--bbox", "9,19,11,21")
        self.verify(candidate="Road")
        output.write_bytes(before)
        result = self.run_board("report", "--candidate", "Road", "--require-verified", "--merge", str(output), ok=False)
        self.assertIn("requires a point candidate", result.stdout)
        self.assertEqual(output.read_bytes(), before)
        # This rule concerns an explicit precise coordinate claim, not a pure audit.
        self.run_board("report", "--candidate", "Road", "--require-verified")

    def test_strict_report_rejects_known_level_gaps(self):
        self.run_board("add", "City", "--level", "district")
        self.run_board("add", "Road", "--level", "road", "--parent", "City")
        self.run_board("move", "A", "--parent", "City")
        self.prepare()
        self.verify()
        self.run_board("evidence", "--clue", "K1", "--for", "A:5")
        output = self.root / "result.json"
        output.write_text('{"original": true}', encoding="utf-8")
        before = output.read_bytes()
        result = self.run_board("report", "--candidate", "A", "--require-verified", "--merge", str(output), ok=False)
        self.assertIn("REPORT BLOCKER A (point) sits directly under City", result.stdout)
        self.assertEqual(output.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
