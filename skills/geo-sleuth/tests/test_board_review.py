"""Offline CLI regressions for candidate recall and revisable reasoning."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


class BoardReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "board.json"
        for name in ("photo.jpg", "query.json", "calculation.json", "evidence.jpg"):
            (self.root / name).write_text("synthetic local artifact", encoding="utf-8")
        self.run_board("init", "--photo", "photo.jpg")

    def run_board(self, *args, ok=True):
        result = subprocess.run([sys.executable, str(SCRIPTS / "board.py"), "--board", str(self.path), *args],
                                cwd=self.root, capture_output=True, text=True, encoding="utf-8")
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def board(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def point_candidates(self):
        self.run_board("add", "A", "B", "--level", "point")
        self.run_board("inspect", "--infra", "absent", "--file", "photo.jpg", "--why", "Synthetic frame review")

    def report(self, *args):
        self.run_board("report", "--merge", "result.json", *args)
        return json.loads((self.root / "result.json").read_text())["board"]

    def clue(self, text="visible shape", *extra):
        self.run_board("clue", text, "--kind", "terrain", "--status", "inferred", *extra)

    def test_context_region_never_added_blocks_strict_and_is_next_action(self):
        self.point_candidates()
        self.run_board("context", "--region", "Region C", "--source", "user-provided puzzle context", "--why", "Applies to this photo")
        self.assertIn("Region C", self.run_board("next").stdout)
        result = self.run_board("report", "--require-verified", ok=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Region C", result.stdout)

    def test_strict_requires_explicit_context_review_and_preserves_old_output(self):
        self.point_candidates()
        old = b'{"previous": true}'
        (self.root / "result.json").write_bytes(old)
        self.run_board("report", "--require-verified", "--merge", "result.json", ok=False)
        self.assertEqual((self.root / "result.json").read_bytes(), old)
        self.run_board("context", "--none", "--why", "No associated puzzle information was supplied")
        self.run_board("report", "--require-verified")

    def test_query_other_region_does_not_clear_initial_check(self):
        self.point_candidates()
        self.run_board("context", "--region", "A", "--source", "puzzle", "--why", "Initial region to check")
        common = ("--method", "image", "--feature", "field layout", "--query", "region field", "--outcome", "no-match", "--file", "query.json", "--why", "Initial scene query only")
        self.run_board("search", "--candidate", "B", *common)
        self.run_board("report", "--require-verified", ok=False)
        self.run_board("search", "--candidate", "A", *common)
        self.run_board("report", "--require-verified")
        self.assertEqual(self.board()["candidates"]["A"]["status"], "open")
        (self.root / "query.json").write_text("modified artifact")
        self.run_board("report", "--require-verified", ok=False)

    def test_rural_and_unknown_urban_request_fails_before_subprocess_or_mutation(self):
        self.run_board("add", "Region A", "--level", "district", "--bbox", "10,20,11,21")
        for kind in ("unknown", "rural", "coastal", "mountain"):
            self.run_board("scene", "--kind", kind, "--why", "Scene review of synthetic fixture")
            before = self.path.read_bytes()
            result = self.run_board("urban", "Region A", ok=False)
            self.assertIn("scene", result.stderr.lower())
            self.assertEqual(self.path.read_bytes(), before)

    def test_farmland_scope_preserves_admin_bbox_and_scan_cost_label(self):
        self.run_board("scene", "--kind", "rural", "--why", "Farmland and drainage channels")
        self.run_board("add", "Region A", "--level", "district", "--bbox", "10,20,11,21")
        self.run_board("scan-bbox", "Region A", "--bbox", "10.1,20.1,10.2,20.2", "--scope", "farmland", "--why", "First field block seen in overview")
        candidate = self.board()["candidates"]["Region A"]
        self.assertEqual(candidate["bbox"], [10, 20, 11, 21])
        self.assertEqual(candidate["scan_scope"], "farmland")
        self.assertIn("farmland", self.run_board("rank").stdout)
        self.assertNotIn("built-up area", self.run_board("rank").stdout)

    def test_invalid_or_unexplained_scope_cannot_mutate(self):
        self.run_board("add", "Region A", "--level", "district", "--bbox", "10,20,11,21")
        before = self.path.read_bytes()
        for bbox in ("nan,20,11,21", "11,20,10,21", "10,20,11", "50,50,51,51"):
            self.run_board("scan-bbox", "Region A", "--bbox", bbox, "--scope", "farmland", "--why", "test", ok=False)
            self.assertEqual(self.path.read_bytes(), before)
        self.run_board("scan-bbox", "Region A", "--bbox", "10.1,20.1,10.2,20.2", ok=False)
        self.assertEqual(self.path.read_bytes(), before)

    def test_scene_correction_makes_old_scan_scope_pending(self):
        self.point_candidates()
        self.run_board("context", "--none", "--why", "No context")
        self.run_board("scene", "--kind", "urban", "--why", "Initial scene interpretation")
        self.run_board("scan-bbox", "A", "--bbox", "10,20,10.1,20.1", "--scope", "urban", "--why", "Initial overview")
        self.run_board("scene", "--kind", "rural", "--why", "Reinspection shows open fields")
        self.run_board("report", "--require-verified", ok=False)
        self.assertIsNone(self.board()["candidates"]["A"].get("scan_bbox"))

    def test_repeated_evidence_does_not_compound(self):
        self.point_candidates()
        self.clue()
        self.run_board("evidence", "--clue", "K1", "--for", "A:3")
        first = self.report()["main"]["share"]
        self.run_board("evidence", "--clue", "K1", "--for", "A:3")
        self.assertEqual(first, self.report()["main"]["share"])
        self.assertEqual(len(self.board()["evidence"]), 2)
        self.run_board("evidence", "--clue", "K1", "--for", "A:1")
        self.assertEqual(self.report()["main"]["share"], 0.5)

    def test_same_source_derivations_do_not_multiply(self):
        self.point_candidates()
        self.clue("base observation")
        self.clue("first interpretation", "--depends-on", "K1")
        self.clue("second interpretation", "--depends-on", "K1")
        for kid in ("K1", "K2", "K3"):
            self.run_board("evidence", "--clue", kid, "--for", "A:3")
        self.assertEqual(self.report()["main"]["share"], 0.75)

    def test_shared_root_bridge_survives_inherited_evidence_grouping(self):
        self.run_board("add", "A", "B", "--level", "city")
        self.run_board("add", "Child", "--level", "point", "--parent", "A")
        for text in ("first source", "second source"):
            self.run_board("clue", text, "--kind", "text", "--status", "read")
        self.run_board("clue", "joint derivation", "--kind", "geometry", "--status", "computed", "--depends-on", "K1", "--depends-on", "K2")
        self.run_board("evidence", "--clue", "K1", "--for", "A:10")
        self.run_board("evidence", "--clue", "K3", "--for", "A:5")
        self.run_board("evidence", "--clue", "K2", "--for", "Child:4")
        rep = self.report("--candidate", "A")
        self.assertEqual(rep["main"]["share"], 0.909)
        self.assertEqual(len(rep["main"]["evidence"]), 1)

    def test_revised_premise_reopens_exclusion_and_removes_derived_score(self):
        self.point_candidates()
        self.clue("capture date supplied by user")
        self.run_board("clue", "solar constraint", "--kind", "sun", "--status", "computed", "--depends-on", "K1", "--file", "calculation.json")
        self.run_board("evidence", "--clue", "K2", "--for", "A:20")
        self.run_board("exclude", "B", "--clue", "K2", "--computed", "calculation.json", "--why", "Synthetic angular conflict")
        self.run_board("revise", "K1", "--text", "corrected date", "--why", "Original date was corrected")
        self.assertEqual(self.board()["candidates"]["B"]["status"], "open")
        self.assertEqual(self.report()["main"]["share"], 0.5)
        self.assertIn("K2", self.run_board("next").stdout)

    def test_nonfinite_likelihood_rejected_without_mutation(self):
        self.point_candidates()
        self.clue()
        before = self.path.read_bytes()
        for value in ("nan", "inf", "-inf"):
            self.run_board("evidence", "--clue", "K1", "--for", "A:" + value, ok=False)
            self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
