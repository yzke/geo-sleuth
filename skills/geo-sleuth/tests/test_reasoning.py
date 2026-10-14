#!/usr/bin/env python3
"""Synthetic offline regressions for revised premises and shared-source scoring."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import _infra
import _reasoning as reasoning


class ReasoningTests(unittest.TestCase):
    def setUp(self):
        self.b = {"clues": {}, "evidence": [], "candidates": {
            "Region A": {"level": "city", "status": "open"},
            "Region B": {"level": "city", "status": "open"},
        }}

    def clue(self, kid, *, depends=None, group=None, status="computed"):
        self.b["clues"][kid] = {
            "text": f"Synthetic clue {kid}", "status": status, "used": False,
            **reasoning.clue_fields(self.b, depends, group),
        }

    def evidence(self, kid, lr, candidate="Region A"):
        row = {"id": f"E{len(self.b['evidence']) + 1}", "clue": kid,
               "candidate": candidate, "lr": lr,
               **reasoning.evidence_fields(self.b, kid)}
        self.b["evidence"].append(row)
        self.b["clues"][kid]["used"] = True
        return row

    def test_declared_transitive_snapshots_and_revision_history(self):
        self.clue("K1")
        self.clue("K2", depends=["K1"])
        self.clue("K3", depends=["K2"])
        entry = self.evidence("K3", 4)
        self.assertEqual(entry["reasoning_snapshot"], {"K1": 1, "K2": 1, "K3": 1})
        reasoning.revise_clue(self.b, "K1", text="Corrected date", why="Source corrected the date")
        self.assertEqual(self.b["clues"]["K1"]["revision"], 2)
        self.assertEqual(self.b["clues"]["K1"]["revision_history"][0]["previous"]["text"],
                         "Synthetic clue K1")
        audit = reasoning.audit(self.b)
        self.assertEqual(set(audit["stale_clues"]), {"K2", "K3"})
        self.assertFalse(audit["ready"])
        self.assertEqual(reasoning.active_evidence(self.b), [])

    def test_recomputation_does_not_reactivate_old_evidence(self):
        self.clue("K1")
        self.clue("K2", depends=["K1"])
        self.clue("K3", depends=["K2"])
        self.evidence("K3", 4)
        reasoning.revise_clue(self.b, "K1", why="Corrected timestamp")
        with self.assertRaisesRegex(ValueError, "stale input"):
            reasoning.revise_clue(self.b, "K3", why="Cannot skip stale parent")
        reasoning.revise_clue(self.b, "K2", file="new-calculation.json", why="Recomputed from corrected timestamp")
        reasoning.revise_clue(self.b, "K3", why="Recomputed downstream comparison")
        self.assertFalse(reasoning.audit(self.b)["ready"])
        self.assertEqual(reasoning.active_evidence(self.b), [])
        self.evidence("K3", 2)
        self.assertTrue(reasoning.audit(self.b)["ready"])
        self.assertEqual([entry["lr"] for entry in reasoning.active_evidence(self.b)], [2])
        self.assertEqual(len(self.b["evidence"]), 2)

    def test_return_to_old_text_is_still_a_new_revision(self):
        self.clue("K1")
        self.evidence("K1", 3)
        reasoning.revise_clue(self.b, "K1", text="Other value", why="Correction")
        reasoning.revise_clue(self.b, "K1", text="Synthetic clue K1", why="Correction reviewed again")
        self.assertEqual(reasoning.active_evidence(self.b), [])
        self.assertEqual(self.b["clues"]["K1"]["revision"], 3)

    def test_duplicate_use_replaces_instead_of_multiplying(self):
        self.clue("K1")
        self.evidence("K1", 5)
        latest = self.evidence("K1", 2)
        self.assertEqual(reasoning.active_evidence(self.b), [latest])
        self.assertEqual(len(self.b["evidence"]), 2)

    def test_newest_stale_record_does_not_fall_back_to_older_valid_record(self):
        self.clue("K1")
        valid = self.evidence("K1", 2)
        self.b["evidence"].append({**valid, "id": "E2", "reasoning_snapshot": {"K1": 999}})
        self.assertEqual(reasoning.active_evidence(self.b), [])
        self.assertFalse(reasoning.audit(self.b)["ready"])

    def test_declared_same_source_counts_once_and_independent_source_remains(self):
        self.clue("K1", group="source-a")
        self.clue("K2", group="source-a")
        self.clue("K3")
        self.evidence("K1", 3)
        self.evidence("K2", 5)
        self.evidence("K3", 2)
        self.assertEqual(sorted(entry["lr"] for entry in reasoning.active_evidence(self.b)), [2, 5])
        self.assertTrue(reasoning.audit(self.b)["ready"])

    def test_shared_root_inference_chain_counts_once(self):
        self.clue("K1", status="observed")
        self.clue("K2", depends=["K1"], status="inferred")
        self.clue("K3", depends=["K2"], status="inferred")
        self.evidence("K1", 2)
        self.evidence("K2", 3)
        self.evidence("K3", 3)
        self.assertEqual(len(reasoning.active_evidence(self.b)), 1)

    def test_group_membership_is_transitive_through_joined_dependencies(self):
        self.clue("K1")
        self.clue("K2")
        self.clue("K3", depends=["K1", "K2"])
        self.evidence("K1", 2)
        self.evidence("K2", 3)
        self.evidence("K3", 4)
        self.assertEqual([entry["lr"] for entry in reasoning.active_evidence(self.b)], [4])

    def test_conflicting_group_is_a_blocker_not_a_chosen_direction(self):
        self.clue("K1", group="shared")
        self.clue("K2", group="shared")
        self.evidence("K1", 5)
        self.evidence("K2", .5)
        self.assertEqual(reasoning.active_evidence(self.b), [])
        audit = reasoning.audit(self.b)
        self.assertFalse(audit["ready"])
        self.assertEqual(audit["group_conflicts"][0]["clues"], ["K1", "K2"])
        self.evidence("K2", 1)
        self.assertTrue(reasoning.audit(self.b)["ready"])
        self.assertEqual([entry["lr"] for entry in reasoning.active_evidence(self.b)], [5])

    def test_grouping_does_not_mix_different_candidate_scores(self):
        self.clue("K1")
        self.evidence("K1", 3, "Region A")
        self.evidence("K1", .3, "Region B")
        self.assertEqual(len(reasoning.active_evidence(self.b)), 2)
        self.assertTrue(reasoning.audit(self.b)["ready"])

    def test_parent_can_regroup_inherited_evidence_from_different_children(self):
        self.clue("K1", group="shared")
        self.clue("K2", group="shared")
        first = self.evidence("K1", 3, "Region A")
        second = self.evidence("K2", 5, "Region B")
        self.assertEqual(len(reasoning.active_evidence(self.b)), 2)
        regrouped = reasoning.group_evidence(self.b, [{**first, "via": "Region A"},
                                                     {**second, "via": "Region B"}])
        self.assertEqual(len(regrouped), 1)
        self.assertEqual(regrouped[0]["lr"], 5)

    def test_parent_own_opposition_and_inherited_shared_source_support_is_visible(self):
        self.clue("K1", group="shared")
        self.clue("K2", group="shared")
        self.b["candidates"]["Region B"]["parent"] = "Region A"
        own = self.evidence("K1", .5, "Region A")
        child = self.evidence("K2", 5, "Region B")
        self.assertEqual(reasoning.group_evidence(self.b, [own, {**child, "via": "Region B"}]), [])
        audit = reasoning.audit(self.b)
        self.assertFalse(audit["ready"])
        self.assertEqual(audit["group_conflicts"][0]["candidate"], "Region A")

    def test_stale_exclusion_reopens_and_requires_new_review(self):
        self.clue("K1")
        self.clue("K2", depends=["K1"])
        candidate = self.b["candidates"]["Region A"]
        old = {"clue": "K2", "why": "Synthetic computed mismatch",
               **reasoning.evidence_fields(self.b, "K2")}
        candidate.update(status="excluded", excluded_by=old)
        reasoning.revise_clue(self.b, "K1", why="Corrected time")
        self.assertEqual(candidate["status"], "open")
        self.assertNotIn("excluded_by", candidate)
        self.assertEqual(candidate["exclusion_history"][0]["reasoning_snapshot"], old["reasoning_snapshot"])
        self.assertFalse(reasoning.audit(self.b)["ready"])
        reasoning.revise_clue(self.b, "K2", why="Recomputed from corrected time")
        self.assertIn("exclusion_review_required", candidate)
        self.evidence("K2", 1)
        self.assertTrue(reasoning.audit(self.b)["ready"])
        self.assertNotIn("exclusion_review_required", candidate)
        self.assertEqual(len(candidate["exclusion_history"]), 1)

    def test_new_exclusion_resolves_reopened_review_and_keeps_history(self):
        self.clue("K1")
        candidate = self.b["candidates"]["Region A"]
        candidate.update(status="excluded", excluded_by={"clue": "K1", **reasoning.evidence_fields(self.b, "K1")})
        reasoning.revise_clue(self.b, "K1", why="Correction")
        candidate.update(status="excluded", excluded_by={"clue": "K1", **reasoning.evidence_fields(self.b, "K1")})
        reasoning.refresh(self.b)
        self.assertEqual(candidate["status"], "excluded")
        self.assertNotIn("exclusion_review_required", candidate)
        self.assertEqual(len(candidate["exclusion_history"]), 1)

    def test_legacy_board_is_compatible_until_its_clue_is_revised(self):
        self.b["clues"]["K1"] = {"text": "Original clue", "status": "computed", "used": True}
        old = {"clue": "K1", "candidate": "Region A", "lr": 3}
        self.b["evidence"].append(old)
        self.b["candidates"]["Region B"].update(status="excluded", excluded_by={"clue": "K1"})
        before = deepcopy(self.b)
        self.assertTrue(reasoning.audit(self.b)["ready"])
        self.assertEqual(self.b, before)
        self.assertEqual(reasoning.active_evidence(self.b), [old])
        reasoning.revise_clue(self.b, "K1", why="Correct legacy clue")
        self.assertEqual(reasoning.active_evidence(self.b), [])
        self.assertEqual(self.b["candidates"]["Region B"]["status"], "open")

    def test_unused_stale_clue_warns_without_blocking_conclusion(self):
        self.clue("K1")
        self.clue("K2", depends=["K1"])
        reasoning.revise_clue(self.b, "K1", why="Correction")
        audit = reasoning.audit(self.b)
        self.assertTrue(audit["ready"])
        self.assertTrue(audit["warnings"])

    def test_cycles_missing_dependencies_and_blank_revision_reason_rejected(self):
        self.clue("K1")
        self.clue("K2", depends=["K1"])
        before = deepcopy(self.b)
        with self.assertRaisesRegex(ValueError, "cycle"):
            reasoning.revise_clue(self.b, "K1", depends_on=["K2"], why="Invalid cycle")
        with self.assertRaisesRegex(ValueError, "Unknown"):
            reasoning.clue_fields(self.b, ["K99"])
        with self.assertRaisesRegex(ValueError, "why"):
            reasoning.revise_clue(self.b, "K1", why=" ")
        self.assertEqual(self.b, before)
        self.b["clues"]["K1"]["depends_on"] = ["K99"]
        self.b["clues"]["K1"]["used"] = True
        self.assertFalse(reasoning.audit(self.b)["ready"])

    def test_nonfinite_evidence_never_scores(self):
        self.clue("K1")
        for value in (float("nan"), float("inf"), 0, -1):
            self.evidence("K1", value)
            self.assertEqual(reasoning.active_evidence(self.b), [])
            self.assertFalse(reasoning.audit(self.b)["ready"])

    def test_infrastructure_snapshot_changes_when_upstream_premise_changes(self):
        self.clue("K1")
        self.clue("K2", depends=["K1"], status="observed")
        self.b["clues"]["K2"]["kind"] = "infra"
        previous = _infra.clue_snapshot(self.b["clues"]["K2"], Path("board.json"))
        reasoning.revise_clue(self.b, "K1", why="Corrected frame association")
        current = _infra.clue_snapshot(self.b["clues"]["K2"], Path("board.json"))
        self.assertNotEqual(previous, current)
        stable = deepcopy(current)
        reasoning.refresh(self.b)
        self.assertEqual(stable, _infra.clue_snapshot(self.b["clues"]["K2"], Path("board.json")))

    def test_strict_exclusion_needs_snapshot_and_unchanged_computed_file(self):
        self.clue("K1")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = root / "comparison.json"
            result.write_text('{"synthetic":true}', encoding="utf-8")
            record = {"clue": "K1", "why": "Computed incompatibility",
                      "computed": "comparison.json", "computed_artifact": _infra.artifact(result),
                      "candidate_snapshot": reasoning.exclusion_candidate_snapshot(self.b, "Region A"),
                      **reasoning.evidence_fields(self.b, "K1")}
            self.b["candidates"]["Region A"].update(status="excluded", excluded_by=record)
            self.assertEqual(reasoning.exclusion_problems(self.b, root / "board.json", "Region A"), [])
            result.write_text('{"synthetic":false}', encoding="utf-8")
            self.assertTrue(reasoning.exclusion_problems(self.b, root / "board.json", "Region A"))
            record.pop("reasoning_snapshot")
            self.assertIn("Exclusion has no recorded revision/input snapshot",
                          reasoning.exclusion_problems(self.b, root / "board.json", "Region A"))

    def test_changed_or_deleted_exclusion_file_reopens_without_context_obligation(self):
        self.clue("K1")
        self.evidence("K1", .5)
        with tempfile.TemporaryDirectory() as tmp:
            result = Path(tmp) / "comparison.json"
            candidate = self.b["candidates"]["Region A"]
            for mode in ("changed", "deleted"):
                with self.subTest(mode=mode):
                    result.write_text("original comparison", encoding="utf-8")
                    candidate.update(status="excluded", excluded_by={
                        "clue": "K1", "computed": str(result),
                        "computed_artifact": _infra.artifact(result),
                        "candidate_snapshot": reasoning.exclusion_candidate_snapshot(self.b, "Region A"),
                        **reasoning.evidence_fields(self.b, "K1"),
                    })
                    if mode == "changed":
                        result.write_text("different comparison", encoding="utf-8")
                    else:
                        result.unlink()
                    audit = reasoning.audit(self.b)
                    self.assertFalse(audit["ready"])
                    self.assertEqual(candidate["status"], "open")
                    # An evidence row created before the file changed cannot
                    # silently satisfy the renewed review requirement.
                    self.assertIn("exclusion_review_required", candidate)
                    reasoning.refresh(self.b)
                    self.assertIn("exclusion_review_required", candidate)
                    self.evidence("K1", 1)
                    self.assertTrue(reasoning.audit(self.b)["ready"])
            self.assertEqual(len(candidate["exclusion_history"]), 2)

    def test_exclusion_scope_and_parent_changes_reopen_but_notes_and_prior_do_not(self):
        self.clue("K1")
        candidate = self.b["candidates"]["Region A"]
        candidate["bbox"] = [10, 10, 11, 11]
        candidate.update(status="excluded", excluded_by={
            "clue": "K1", **reasoning.evidence_fields(self.b, "K1"),
            "candidate_snapshot": reasoning.exclusion_candidate_snapshot(self.b, "Region A"),
        })
        candidate.update(note="edited display note", prior=2)
        reasoning.refresh(self.b)
        self.assertEqual(candidate["status"], "excluded")
        candidate["bbox"] = [10, 10, 12, 12]
        reasoning.refresh(self.b)
        self.assertEqual(candidate["status"], "open")
        self.assertFalse(reasoning.audit(self.b)["ready"])
        candidate.update(status="excluded", excluded_by={
            "clue": "K1", **reasoning.evidence_fields(self.b, "K1"),
            "candidate_snapshot": reasoning.exclusion_candidate_snapshot(self.b, "Region A"),
        })
        reasoning.refresh(self.b)
        candidate["parent"] = "Region B"
        reasoning.refresh(self.b)
        self.assertEqual(candidate["status"], "open")
        self.assertEqual(len(candidate["exclusion_history"]), 2)


if __name__ == "__main__":
    unittest.main()
