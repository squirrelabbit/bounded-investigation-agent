"""The Controller owns the loop. A provider cannot widen its own authority."""
from __future__ import annotations

import os
import unittest

from bia import controller as controller_mod
from bia.controller import investigate
from bia.decision import DeterministicHeuristicSelector, ScriptedSelector
from bia.evidence import MAX_DECISION_CALLS, MAX_RETRIEVALS
from bia.store import OracleAccessError, guard_not_oracle, load_metric_rows
from bia.types import DEFER

from . import support


def run(provider, tickets=None):
    return investigate(
        support.intent(),
        support.rows(),
        support.tickets() if tickets is None else tickets,
        provider,
    )


class HeuristicBaselineTests(unittest.TestCase):
    def test_baseline_finds_the_spike_cell_within_budget(self):
        result = run(DeterministicHeuristicSelector())
        state = result.state
        self.assertEqual(state.finish_reason, controller_mod.FINISH_SUFFICIENT)
        self.assertEqual(state.decision_calls, 1)
        self.assertEqual(state.retrievals, 1)
        self.assertEqual(state.violations, [])
        self.assertTrue(state.found_ticket_ids)
        for admitted in state.found_tickets:
            self.assertEqual(admitted.product, support.SPIKE_PRODUCT)
            self.assertEqual(admitted.complaint_type, support.SPIKE_TYPE)

    def test_run_is_reproducible(self):
        first = run(DeterministicHeuristicSelector()).answer.render()
        second = run(DeterministicHeuristicSelector()).answer.render()
        self.assertEqual(first, second)


class FailClosedTests(unittest.TestCase):
    def test_id_outside_the_offered_set_is_downgraded_to_defer(self):
        result = run(ScriptedSelector(["R1-C99"]))
        state = result.state
        self.assertEqual(state.finish_reason, controller_mod.FINISH_DEFERRED)
        self.assertEqual(state.retrievals, 0)
        self.assertEqual(state.found_ticket_ids, [])
        self.assertEqual(len(state.violations), 1)
        self.assertIn(controller_mod.VIOLATION_UNKNOWN_ID, state.violations[0])

    def test_a_non_string_selection_is_downgraded_to_defer(self):
        result = run(ScriptedSelector([42]))
        state = result.state
        self.assertEqual(state.finish_reason, controller_mod.FINISH_DEFERRED)
        self.assertEqual(state.retrievals, 0)
        self.assertIn(controller_mod.VIOLATION_MALFORMED, state.violations[0])

    def test_an_empty_selection_is_downgraded_to_defer(self):
        result = run(ScriptedSelector([""]))
        self.assertEqual(result.state.finish_reason, controller_mod.FINISH_DEFERRED)
        self.assertIn(controller_mod.VIOLATION_UNKNOWN_ID, result.state.violations[0])

    def test_a_free_text_search_condition_is_not_honoured(self):
        """A provider trying to smuggle a query string gets nothing executed."""
        result = run(ScriptedSelector(["SELECT * FROM tickets WHERE product='P-Beta'"]))
        state = result.state
        self.assertEqual(state.retrievals, 0)
        self.assertEqual(state.found_ticket_ids, [])
        self.assertIn(controller_mod.VIOLATION_UNKNOWN_ID, state.violations[0])

    def test_explicit_defer_is_honoured_without_being_a_violation(self):
        result = run(ScriptedSelector([DEFER]))
        state = result.state
        self.assertEqual(state.finish_reason, controller_mod.FINISH_DEFERRED)
        self.assertEqual(state.violations, [])
        self.assertEqual(state.retrievals, 0)

    def test_repeat_selection_guard_rejects_an_already_investigated_id(self):
        """Candidate ids are re-minted per round, so this guard is defence in depth;
        it is exercised directly rather than through a reachable provider path."""
        from bia.evidence import build_candidates

        state = run(DeterministicHeuristicSelector()).state
        candidates = build_candidates(state, support.tickets(), round_index=1)
        self.assertTrue(candidates)
        state.investigated_candidates.append(candidates[0].candidate_id)
        chosen, violation = controller_mod._resolve_selection(
            candidates[0].candidate_id, candidates, state
        )
        self.assertIsNone(chosen)
        self.assertEqual(violation, controller_mod.VIOLATION_REPEAT)


class BudgetTests(unittest.TestCase):
    def test_a_provider_cannot_exceed_the_call_budget(self):
        greedy = ScriptedSelector(["R1-C1", "R2-C1", "R3-C1", "R4-C1"], on_exhausted="R5-C1")
        thin = support.tickets(count=3)
        state = run(greedy, tickets=thin).state
        self.assertLessEqual(state.decision_calls, MAX_DECISION_CALLS)
        self.assertLessEqual(state.retrievals, MAX_RETRIEVALS)
        self.assertLessEqual(len(greedy.calls), MAX_DECISION_CALLS)

    def test_provider_cannot_end_the_run_by_itself(self):
        """FINISH is the Controller's; the provider's vocabulary has no such token."""
        state = run(ScriptedSelector(["FINISH"])).state
        self.assertEqual(state.finish_reason, controller_mod.FINISH_DEFERRED)
        self.assertIn(controller_mod.VIOLATION_UNKNOWN_ID, state.violations[0])


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORACLE = os.path.join(REPO_ROOT, "data", "oracle", "oracle.json")


class OracleIsolationTests(unittest.TestCase):
    def test_runtime_refuses_to_open_the_oracle_manifest(self):
        with self.assertRaises(OracleAccessError):
            load_metric_rows(os.path.join("data", "oracle", "oracle.json"))

    def test_the_guard_survives_a_symlink_with_an_innocent_name(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            link = os.path.join(tmp, "facts.json")
            os.symlink(ORACLE, link)
            with self.assertRaises(OracleAccessError):
                guard_not_oracle(link)

    def test_the_guard_survives_a_differently_cased_parent_directory(self):
        import shutil
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(tmp, "Oracle")
            os.makedirs(folder)
            copied = os.path.join(folder, "truth.json")
            shutil.copyfile(ORACLE, copied)
            with self.assertRaises(OracleAccessError):
                guard_not_oracle(copied)

    def test_the_guard_survives_a_parent_traversal(self):
        import shutil
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(tmp, "Oracle")
            os.makedirs(folder)
            copied = os.path.join(folder, "truth.json")
            shutil.copyfile(ORACLE, copied)
            traversed = os.path.join(folder, "..", "Oracle", "truth.json")
            with self.assertRaises(OracleAccessError):
                guard_not_oracle(traversed)

    def test_a_normal_scenario_file_is_not_blocked(self):
        guard_not_oracle(os.path.join(REPO_ROOT, "data", "scenarios", "S01", "metrics.csv"))

    def test_no_runtime_module_mentions_the_oracle_file(self):
        package = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bia")
        mentions = []
        for name in sorted(os.listdir(package)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(package, name), encoding="utf-8") as handle:
                if "oracle" in handle.read().lower():
                    mentions.append(name)
        # store.py holds the guard; datagen.py writes the manifest; scenarios.py
        # documents it. Any other module naming it is a leak.
        self.assertEqual(
            mentions,
            ["datagen.py", "scenarios.py", "store.py"],
            "unexpected module referencing the oracle: %s" % mentions,
        )


if __name__ == "__main__":
    unittest.main()
