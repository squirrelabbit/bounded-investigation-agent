"""The 16th call. Fakes only — nothing here opens a socket or reads a key.

§7-D keeps §7-C's budget: 16 attempted calls for the whole run, no retries. The
scorer's 8 cases may each spend at most `MAX_DECISION_CALLS` decisions, so the
demand ceiling is 8 x 2 = 16 — **exactly** the budget. The margin is zero, and
the last call of a run is therefore the one where "budget spent" and "call
failed" can arrive together. That coincidence had never been exercised: the
existing budget tests cap the budget at 1 or 2 to reach exhaustion cheaply, and
the existing halt tests halt on the FIRST call with 15 left over. This file
covers the boundary itself, before a paid run gets there first.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from bia.evidence import MAX_DECISION_CALLS
from bia.jev import (
    DEFAULT_MAX_CALLS,
    REASON_BUDGET_EXHAUSTED,
    REASON_RUN_HALTED,
    CallBudget,
    FakeTransport,
    JevHttpError,
    JevSelector,
)
from bia.types import DEFER

from .test_jev_adapter import answer_response, first_round_inputs
from .test_jev_dryrun import _fresh_run, _scorer

FULL = DEFAULT_MAX_CALLS  # 16


class MarginIsZeroTests(unittest.TestCase):
    """Why this file exists, asserted rather than left in a comment."""

    def test_the_demand_ceiling_equals_the_budget_exactly(self):
        scorer = _scorer()
        case_count = len(scorer.load_oracle())
        self.assertEqual(case_count, 8)
        self.assertEqual(MAX_DECISION_CALLS, 2)
        self.assertEqual(CallBudget().maximum, FULL)
        self.assertEqual(
            case_count * MAX_DECISION_CALLS,
            CallBudget().maximum,
            "the run's maximum demand and its budget are the same number: there "
            "is no spare call, so any extra consumption starves a later case",
        )


class SixteenthCallSucceedsTests(unittest.TestCase):
    """The clean boundary: the budget lands on zero and nothing else changes."""

    def setUp(self):
        self.state, self.candidates = first_round_inputs()
        self.wanted = self.candidates[0].candidate_id
        self.transport = FakeTransport([answer_response(self.wanted)])
        self.selector = JevSelector(
            transport=self.transport, budget=CallBudget(FULL)
        )

    def _spend_the_whole_budget(self):
        return [
            self.selector.select_next_evidence(self.state, self.candidates)
            for _ in range(FULL)
        ]

    def test_all_sixteen_calls_are_sent_and_the_budget_lands_on_zero(self):
        returned = self._spend_the_whole_budget()
        self.assertEqual(returned, [self.wanted] * FULL)
        self.assertEqual(self.transport.call_count, FULL)
        self.assertEqual(self.selector.call_count, FULL)
        self.assertEqual(self.selector.budget.used, FULL)
        self.assertEqual(self.selector.budget.remaining, 0)

    def test_a_full_budget_is_not_a_halt(self):
        """Spending every call says nothing about whether the endpoint works."""
        self._spend_the_whole_budget()
        self.assertFalse(self.selector.guard.halted)
        self.assertEqual(self.selector.guard.halt_reason, "")
        self.assertIsNone(self.selector.records[-1].failure_reason)

    def test_the_seventeenth_decision_sends_nothing_and_says_why(self):
        self._spend_the_whole_budget()
        self.assertEqual(
            self.selector.select_next_evidence(self.state, self.candidates), DEFER
        )
        record = self.selector.records[-1]
        self.assertFalse(record.called)
        self.assertEqual(record.failure_reason, REASON_BUDGET_EXHAUSTED)
        self.assertEqual(record.returned, DEFER)
        self.assertEqual(
            self.transport.call_count, FULL, "the 17th decision must not send"
        )
        self.assertEqual(self.selector.budget.used, FULL)
        self.assertFalse(self.selector.guard.halted)


class SixteenthCallFailsTests(unittest.TestCase):
    """The coincidence: the last call of the budget is also a failed call."""

    def setUp(self):
        self.state, self.candidates = first_round_inputs()
        self.wanted = self.candidates[0].candidate_id
        script = [answer_response(self.wanted)] * (FULL - 1)
        script.append(JevHttpError(429, None))
        self.transport = FakeTransport(script, repeat_last=True)
        self.selector = JevSelector(
            transport=self.transport, budget=CallBudget(FULL)
        )
        self.returned = [
            self.selector.select_next_evidence(self.state, self.candidates)
            for _ in range(FULL)
        ]

    def test_the_halt_and_the_exhaustion_arrive_together(self):
        self.assertEqual(self.returned[:-1], [self.wanted] * (FULL - 1))
        self.assertEqual(self.returned[-1], DEFER)
        self.assertTrue(self.selector.guard.halted)
        self.assertEqual(self.selector.budget.used, FULL)
        self.assertEqual(self.selector.budget.remaining, 0)
        self.assertEqual(self.transport.call_count, FULL)

    def test_the_halt_reason_is_the_failure_not_the_exhaustion(self):
        """A failed 16th call spent the budget AND broke the run. The reported
        reason must be the failure: that is what a resume decision is made
        against, and §7-E keeps the FIRST reason."""
        self.assertEqual(self.selector.guard.halt_reason, "http_status:429")
        self.assertEqual(self.selector.records[-1].failure_reason, "http_status:429")
        self.assertTrue(self.selector.records[-1].called)

    def test_a_later_decision_reports_the_halt_rather_than_the_spent_budget(self):
        """Both refusals apply at once. `select_next_evidence` checks the halt
        FIRST, so the halt is what is written down — asserted here as the
        observed behaviour, because a run that stopped and a run that merely ran
        out of calls are not the same report."""
        self.assertEqual(
            self.selector.select_next_evidence(self.state, self.candidates), DEFER
        )
        record = self.selector.records[-1]
        self.assertEqual(
            record.failure_reason, "%s:http_status:429" % REASON_RUN_HALTED
        )
        self.assertNotEqual(record.failure_reason, REASON_BUDGET_EXHAUSTED)
        self.assertFalse(record.called)

    def test_no_further_decision_ever_pushes_the_send_count_past_sixteen(self):
        for _ in range(5):
            self.assertEqual(
                self.selector.select_next_evidence(self.state, self.candidates), DEFER
            )
        self.assertLessEqual(self.transport.call_count, FULL)
        self.assertEqual(self.transport.call_count, FULL)
        self.assertEqual(self.selector.budget.used, FULL)


class StarvedCaseLooksLikeADeferTests(unittest.TestCase):
    """How an exhausted budget looks in the scored record — a characterization.

    Reaching this at all takes a budget that is already part spent, because the
    run's demand ceiling equals its budget exactly (see `MarginIsZeroTests`).
    That is the point: the margin is zero, so any call consumed elsewhere — a
    second run in the same process, an added case, any probe — starves a later
    case. The run below spends 9 of the 16 up front and then runs the real loop.

    These assertions record what the scorer CURRENTLY writes, including a
    misreading surface reported to the coordinator rather than fixed here
    (changing the record is a contract change). If that recording is ever
    changed deliberately, this test is meant to fail and say so.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="bia-budget-")
        buffer = io.StringIO()
        with _fresh_run(cls.tmpdir) as scorer:
            scorer._JEV_PROCESS_BUDGET.used = 9  # 7 calls left for 8 cases
            with contextlib.redirect_stdout(buffer):
                cls.code = scorer.main(["--selector", "jev", "--dry-run"])
            cls.budget = scorer._JEV_PROCESS_BUDGET.as_dict()
            cls.halted = scorer._JEV_PROCESS_GUARD.halted
        cls.printed = buffer.getvalue()
        with open(os.path.join(cls.tmpdir, "jev_dryrun.json"), encoding="utf-8") as h:
            cls.document = json.load(h)
        cls.cases = {case["case_id"]: case for case in cls.document["cases"]}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_the_run_finishes_all_cases_and_never_exceeds_the_budget(self):
        self.assertEqual(self.code, 0)
        self.assertEqual(len(self.cases), 8)
        self.assertEqual(self.budget["used"], FULL)
        self.assertEqual(self.budget["remaining"], 0)

    def test_running_out_of_calls_does_not_halt_or_mark_the_run_partial(self):
        self.assertFalse(self.halted)
        self.assertFalse(self.document["halted"])
        self.assertEqual(sorted(os.listdir(self.tmpdir)), ["jev_dryrun.json"])

    def test_the_starved_cases_are_the_late_ones_and_they_retrieve_nothing(self):
        starved = [
            case_id
            for case_id, case in self.cases.items()
            if case["retrievals"] == 0 and case["decision_calls"] == 1
        ]
        self.assertEqual(sorted(starved), ["C06", "C07", "C08"])
        for case_id in starved:
            case = self.cases[case_id]
            self.assertEqual(case["admitted_count"], 0)
            self.assertEqual(case["finish_reason"], "provider_deferred")
            self.assertEqual(case["yield"], 0.0)

    def test_a_starved_case_is_indistinguishable_from_a_deliberate_defer(self):
        """The misreading surface, asserted so it cannot be forgotten.

        §7-D's 2026-09-22 표기 정정 makes `selector_deferred` the observation a
        "the model held back in time" claim is read off. A case that was never
        offered a call carries exactly that flag, passes with no failure, and
        carries no trace of the budget anywhere in its record — the only trace
        lives on the selector's own call record, which the scorer does not
        persist.
        """
        for case_id in ("C06", "C07", "C08"):
            case = self.cases[case_id]
            self.assertIs(case["selector_deferred"], True)
            self.assertEqual(case["defer_selections"], 1)
            self.assertEqual(case["failures"], [])
            self.assertNotIn("incomplete", case)
            blob = json.dumps(case, ensure_ascii=False).lower()
            self.assertNotIn("budget", blob)
            self.assertNotIn("exhaust", blob)

    def test_the_starved_cases_silently_drag_the_comparison_metric_down(self):
        """Three zero-yield cases enter the macro mean with no marking at all."""
        self.assertEqual(self.document["summary"]["V-2_included_cases"], 7)
        self.assertLess(self.document["summary"]["V-2_evidence_yield_macro"], 0.3718)
        self.assertEqual(self.document["summary"]["V-2_evidence_yield_macro"], 0.1015)


if __name__ == "__main__":
    unittest.main()
