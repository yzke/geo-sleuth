#!/usr/bin/env python3
"""Offline regressions for missing candidate regions and misleading coverage."""
from __future__ import annotations

import argparse
import copy
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import _search_audit as review  # noqa: E402


class SearchAuditTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "board.json"
        self.result = self.root / "results.json"
        self.result.write_text('{"matches": []}', encoding="utf-8")
        self.board = {"case": "Synthetic survey", "photo": "frame.jpg", "clues": {},
                      "candidates": {}, "evidence": []}
        self.parser = argparse.ArgumentParser()
        review.register_parsers(self.parser.add_subparsers(dest="cmd", required=True))

    def run_command(self, *args):
        return review.handle(self.parser.parse_args(args), self.board, self.path)

    def add(self, name, level="district", parent=None):
        self.board["candidates"][name] = {"level": level, "parent": parent, "bbox": [0, 0, 2, 2],
                                          "status": "open", "prior": 1.0}

    def context(self, *regions, relation="same-case", supersedes=()):
        args = ["context", "--source", "Explicit case note", "--why", "Applies to the current synthetic case",
                "--relation", relation]
        for name in regions:
            args += ["--region", name]
        for key in supersedes:
            args += ["--supersedes", key]
        return self.run_command(*args)

    def search(self, name, outcome="no-match", feature="network", **extra):
        args = ["search", "--candidate", name, "--method", "map", "--feature", feature,
                "--query", "Compare the road and water layout", "--outcome", outcome,
                "--why", "Only an initial layout check, retained locally"]
        if outcome in review.COMPLETED:
            args += ["--file", str(self.result)]
        for key, value in extra.items():
            args += ["--" + key.replace("_", "-"), value]
        return self.run_command(*args)

    def audit(self):
        return review.audit(self.board, self.path)

    def test_old_board_is_readable_but_has_no_context_review(self):
        before = copy.deepcopy(self.board)
        audit = self.audit()
        self.assertFalse(audit["ready"])
        self.assertIn("not been reviewed", " ".join(audit["blockers"]))
        self.assertEqual(self.board, before)
        self.run_command("context", "--none", "--why", "No case context is available")
        self.assertTrue(self.audit()["ready"])

    def test_missing_context_region_and_querying_another_region_do_not_pass(self):
        self.add("Region B")
        self.context("Region A")
        self.search("Region B")
        self.assertIn("Region A: context region is missing", " ".join(self.audit()["blockers"]))
        self.add("Region A", level="admin1")
        audit = self.audit()
        self.assertFalse(audit["ready"])
        self.assertIn("Region A: no completed", " ".join(audit["blockers"]))
        self.assertEqual(audit["regions"][0]["level"], "admin1")
        self.search("Region A")
        self.assertTrue(self.audit()["ready"])

    def test_no_match_is_an_initial_check_not_an_exclusion_or_likelihood_ratio(self):
        self.add("Region A")
        self.context("Region A")
        original_candidates = copy.deepcopy(self.board["candidates"])
        self.search("Region A", bbox="0,0,0.1,0.1")
        self.assertTrue(self.audit()["ready"])
        self.assertEqual(self.board["candidates"], original_candidates)
        self.assertEqual(self.board["evidence"], [])
        self.assertIn("not exhaustive coverage", self.audit()["scope"])

    def test_unconfirmed_relationship_is_not_inherited(self):
        self.context("Region A", relation="unconfirmed")
        audit = self.audit()
        self.assertTrue(audit["ready"])
        self.assertEqual(audit["regions"], [])
        self.assertIn("not inherited", " ".join(audit["warnings"]))

    def test_explicit_same_photographer_prior_is_soft_and_requires_a_check(self):
        self.add("Region A")
        self.context("Region A", relation="same-photographer")
        self.assertFalse(self.audit()["ready"])
        self.assertEqual(self.board["candidates"]["Region A"]["prior"], 1.0)
        self.search("Region A")
        self.assertTrue(self.audit()["ready"])

    def test_none_cannot_erase_active_context_without_explicit_supersession(self):
        self.context("Region A")
        before = copy.deepcopy(self.board)
        with self.assertRaisesRegex(ValueError, "explicitly --supersedes"):
            self.run_command("context", "--none", "--why", "The earlier source was incorrect")
        self.assertEqual(self.board, before)
        self.run_command("context", "--none", "--why", "The earlier source was incorrect", "--supersedes", "C1")
        self.assertTrue(self.audit()["ready"])
        self.assertEqual(len(self.board["context_reviews"]), 2)
        self.assertEqual(self.board["context_reviews"][0], before["context_reviews"][0])

    def test_contexts_add_until_explicitly_superseded(self):
        self.add("Region A")
        self.add("Region B")
        self.context("Region A")
        self.context("Region B")
        self.search("Region B")
        self.assertFalse(self.audit()["ready"])
        self.context("Region B", supersedes=("C1", "C2"))
        self.assertTrue(self.audit()["ready"])
        self.assertEqual([row["name"] for row in self.audit()["regions"]], ["Region B"])
        before = copy.deepcopy(self.board)
        with self.assertRaisesRegex(ValueError, "active context"):
            self.context("Region B", supersedes=("C1",))
        self.assertEqual(self.board, before)

    def test_other_generated_candidates_warn_but_do_not_all_require_search(self):
        self.add("Region A")
        self.add("Region B")
        self.context("Region A")
        self.search("Region A")
        audit = self.audit()
        self.assertTrue(audit["ready"])
        self.assertEqual(audit["other_unsearched_count"], 1)
        self.assertIn("Region B", " ".join(audit["warnings"]))

    def test_failed_and_deferred_queries_do_not_complete_initial_check(self):
        self.add("Region A")
        self.context("Region A")
        self.search("Region A", outcome="unavailable")
        self.search("Region A", outcome="deferred")
        audit = self.audit()
        self.assertFalse(audit["ready"])
        self.assertEqual({row["state"] for row in audit["queries"]}, {"incomplete"})
        self.assertEqual(self.board["evidence"], [])

    def test_replacing_photo_requires_context_review_and_fresh_search(self):
        self.add("Region A")
        self.context("Region A")
        self.search("Region A")
        self.board["photo"] = "other-frame.jpg"
        self.assertFalse(self.audit()["ready"])
        self.assertIn("different case/photo", " ".join(self.audit()["blockers"]))
        self.context("Region A", supersedes=("C1",))
        self.assertFalse(self.audit()["ready"])
        self.search("Region A")
        self.assertTrue(self.audit()["ready"])
        self.assertEqual([row["state"] for row in self.audit()["queries"]], ["stale", "complete"])

    def test_replacing_photo_bytes_at_same_path_invalidates_context_and_search(self):
        photo = self.root / "frame.jpg"
        photo.write_text("synthetic original frame", encoding="utf-8")
        self.add("Region A")
        self.context("Region A")
        self.search("Region A")
        self.assertTrue(self.audit()["ready"])
        photo.write_text("synthetic replacement frame", encoding="utf-8")
        audit = self.audit()
        self.assertFalse(audit["ready"])
        self.assertIn("different case/photo", " ".join(audit["blockers"]))
        self.assertEqual(audit["queries"][0]["state"], "stale")
        photo.write_text("synthetic original frame", encoding="utf-8")
        self.assertTrue(self.audit()["ready"])

    def test_changed_or_deleted_retained_results_cannot_complete_check(self):
        self.add("Region A")
        self.context("Region A")
        self.search("Region A")
        self.result.write_text("different result", encoding="utf-8")
        self.assertFalse(self.audit()["ready"])
        self.assertIn("retained result changed", " ".join(self.audit()["warnings"]))
        self.search("Region A")
        self.assertTrue(self.audit()["ready"])
        self.result.unlink()
        self.assertFalse(self.audit()["ready"])

    def test_candidate_geometry_snapshot_survives_inplace_mutation(self):
        self.add("Region A")
        self.context("Region A")
        self.search("Region A")
        self.board["candidates"]["Region A"]["bbox"][2] = 3
        self.assertFalse(self.audit()["ready"])
        saved = self.board["search_checks"][0]["candidate_snapshot"][0]["inputs"]["bbox"]
        self.assertEqual(saved, [0, 0, 2, 2])

    def test_ancestry_change_stales_a_query_but_ranking_updates_do_not(self):
        self.add("Parent A", level="country")
        self.add("Region A", parent="Parent A")
        self.context("Region A")
        self.search("Region A")
        self.board["candidates"]["Region A"]["prior"] = 2
        self.board["candidates"]["Region A"]["note"] = "Bookkeeping only"
        self.assertTrue(self.audit()["ready"])
        self.board["candidates"]["Parent A"]["bbox"] = [0, 0, 3, 3]
        self.assertFalse(self.audit()["ready"])

    def test_disjoint_nonfinite_reversed_and_empty_bboxes_are_rejected_atomically(self):
        self.add("Region A")
        for box in ("10,10,11,11", "0,0,nan,1", "0,0,91,1", "2,0,1,1", "0,0,0,1", "0,0,1"):
            with self.subTest(box=box):
                before = copy.deepcopy(self.board)
                with self.assertRaises(ValueError):
                    self.search("Region A", bbox=box)
                self.assertEqual(self.board, before)

    def test_completed_search_requires_real_nonempty_file_and_exact_candidate(self):
        self.add("Region A")
        for file in (self.root / "missing.json", self.root / "empty.json"):
            if file.name == "empty.json":
                file.touch()
            before = copy.deepcopy(self.board)
            with self.assertRaises(ValueError):
                self.search("Region A", file=str(file))
            self.assertEqual(self.board, before)
        with self.assertRaisesRegex(ValueError, "exact candidate"):
            self.search("Region")

    def test_empty_source_or_reason_cannot_create_context(self):
        for extra in (("--source", " ", "--why", "Known source"), ("--source", "Case note", "--why", " ")):
            before = copy.deepcopy(self.board)
            with self.assertRaises(ValueError):
                self.run_command("context", "--region", "Region A", *extra)
            self.assertEqual(self.board, before)

    def test_suggestions_follow_observed_clue_kinds_and_logged_feature_ids(self):
        self.add("Region A")
        self.context("Region A")
        self.board["clues"] = {"K1": {"kind": "network", "status": "observed"},
                               "K2": {"kind": "terrain", "status": "observed"},
                               "K3": {"kind": "industry", "status": "inferred"}}
        self.search("Region A", feature="K1")
        suggestions = " ".join(self.audit()["suggestions"])
        self.assertIn("terrain", suggestions)
        self.assertNotIn("network", suggestions)
        self.assertNotIn("industry", suggestions)

    def test_compact_query_output_does_not_drop_old_valid_checks(self):
        self.add("Region A")
        self.add("Region B")
        self.context("Region A")
        self.search("Region A")
        for _ in range(25):
            self.search("Region B", outcome="unavailable")
        audit = self.audit()
        self.assertTrue(audit["ready"])
        self.assertEqual(len(audit["queries"]), 20)
        self.assertEqual(audit["queries_omitted"], 6)
        self.assertEqual(audit["history_counts"]["queries"], 26)

    def test_legacy_excluded_status_alone_cannot_bypass_initial_check(self):
        self.add("Region A")
        self.context("Region A")
        self.board["candidates"]["Region A"]["status"] = "excluded"
        self.board["candidates"]["Region A"]["excluded_by"] = {"clue": "K1", "computed": str(self.result)}
        audit = self.audit()
        self.assertFalse(audit["ready"])
        self.assertIn("exclusion is not current", " ".join(audit["blockers"]))

    def test_current_explicit_exclusion_can_discharge_check_until_inputs_change(self):
        import _reasoning
        from _infra import artifact

        self.add("Region A")
        self.context("Region A")
        self.board["clues"]["K1"] = {"text": "Synthetic geometry contradiction", "kind": "network",
                                      "status": "computed", "revision": 1}
        self.board["candidates"]["Region A"]["status"] = "excluded"
        self.board["candidates"]["Region A"]["excluded_by"] = {
            "clue": "K1", "computed": str(self.result), "computed_artifact": artifact(self.result),
            "candidate_snapshot": _reasoning.exclusion_candidate_snapshot(self.board, "Region A"),
            "why": "Synthetic computed contradiction over the candidate scope",
            **_reasoning.evidence_fields(self.board, "K1"),
        }
        self.assertTrue(self.audit()["ready"])
        self.assertIn("discharges", " ".join(self.audit()["warnings"]))
        self.result.write_text("changed computation", encoding="utf-8")
        self.assertFalse(self.audit()["ready"])

    def test_bounded_search_supports_point_and_partial_road_geometry(self):
        self.add("Point A", level="point")
        self.board["candidates"]["Point A"].pop("bbox")
        self.board["candidates"]["Point A"]["center"] = [1, 1]
        self.context("Point A")
        self.search("Point A", bbox="0.9,0.9,1.1,1.1")
        self.assertTrue(self.audit()["ready"])
        self.add("Road A", level="road")
        self.board["candidates"]["Road A"]["bbox"] = [0, 1, 2, 1]
        self.search("Road A", bbox="0.5,0.5,1.5,1.5")


if __name__ == "__main__":
    unittest.main()
