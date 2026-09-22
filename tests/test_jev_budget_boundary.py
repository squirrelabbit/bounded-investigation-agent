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
    RehearsalTransport,
    RunGuard,
)
from bia.types import DEFER

from .test_jev_adapter import answer_response, first_round_inputs
from .test_jev_dryrun import _fresh_run, _scorer

FULL = DEFAULT_MAX_CALLS  # 16


class _DeferringTransport(RehearsalTransport):
    """A well-formed offline response whose `choice` is DEFER.

    This is the thing a forced DEFER must never be confused with: a call WAS
    sent and the model answered "hold back". DEFER is always the last option in
    `criteria`, so it is an offered option and the adapter accepts it.
    """

    def send(self, request):
        response = super().send(request)
        response.body["answers"][self.question_key]["choice"] = DEFER
        self.choices[-1] = DEFER
        return response


class _FailingTransport(RehearsalTransport):
    """Every request is sent and comes back a 429: a call that DID happen and
    produced nothing usable. Not a model holding back, and not a call that was
    never made either."""

    def send(self, request):
        self.requests.append(request)
        raise JevHttpError(429, None)


def _score_one_case(case_id, budget, guard, transport=RehearsalTransport):
    """One case through the scorer's real path: `run_case` builds the recording
    selector, the Controller runs, `observe` classifies, `score_case` writes the
    record. Nothing is hand-fed to a helper."""
    scorer = _scorer()

    def factory():
        return JevSelector(transport=transport(), budget=budget, guard=guard)

    obs, error = scorer.run_case(case_id, "jev", factory=factory)
    if obs is None:
        raise AssertionError("case %s crashed: %s" % (case_id, error))
    return scorer.score_case(case_id, scorer.load_oracle()[case_id], obs)


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


class StarvedCaseIsNotADeferTests(unittest.TestCase):
    """How an exhausted budget looks in the scored record, after the fix.

    Reaching this at all takes a budget that is already part spent, because the
    run's demand ceiling equals its budget exactly (see `MarginIsZeroTests`).
    That is the point: the margin is zero, so any call consumed elsewhere — a
    second run in the same process, an added case, any probe — starves a later
    case. The run below spends 9 of the 16 up front and then runs the real loop.

    A starved case is now recorded as what it is: `selector_deferred` stays
    false because no model ever held back, the forced decision is counted and
    named, the case carries `starved`, and the run as a whole is marked
    uncomparable, written to its OWN filename, and exits non-zero instead of
    passing quietly with a depressed macro average.

    A results file is planted in the output directory first, so "the starved run
    did not touch it" is asserted against a real file rather than an absence.
    """

    PLANTED = b'{"selector": "jev", "planted": "paid measurement"}\n'

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="bia-budget-")
        # Stand-ins for a measurement that cost money: the normal results file
        # for this selector, and the rehearsal's own results file.
        cls.planted_paths = [
            os.path.join(cls.tmpdir, "jev.json"),
            os.path.join(cls.tmpdir, "jev_dryrun.json"),
        ]
        for path in cls.planted_paths:
            with open(path, "wb") as handle:
                handle.write(cls.PLANTED)
        cls.planted_before = []
        for path in cls.planted_paths:
            with open(path, "rb") as handle:
                cls.planted_before.append((os.stat(path).st_mtime_ns, handle.read()))
        buffer = io.StringIO()
        with _fresh_run(cls.tmpdir) as scorer:
            scorer._JEV_PROCESS_BUDGET.used = 9  # 7 calls left for 8 cases
            with contextlib.redirect_stdout(buffer):
                cls.code = scorer.main(["--selector", "jev", "--dry-run"])
            cls.budget = scorer._JEV_PROCESS_BUDGET.as_dict()
            cls.halted = scorer._JEV_PROCESS_GUARD.halted
        cls.printed = buffer.getvalue()
        cls.out_path = os.path.join(cls.tmpdir, "jev_dryrun_STARVED.json")
        with open(cls.out_path, encoding="utf-8") as h:
            cls.document = json.load(h)
        cls.cases = {case["case_id"]: case for case in cls.document["cases"]}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_the_run_finishes_all_cases_and_never_exceeds_the_budget(self):
        """Every case is still scored and the ceiling still binds; what changed
        is the exit code, which is now non-zero because a starved run is not a
        comparison."""
        self.assertEqual(self.code, 2)
        self.assertEqual(len(self.cases), 8)
        self.assertEqual(self.budget["used"], FULL)
        self.assertEqual(self.budget["remaining"], 0)

    def test_running_out_of_calls_does_not_halt_or_mark_the_run_partial(self):
        """Starvation and a halt stay separate conditions: no halt was raised,
        no PARTIAL file was written, and the document says so."""
        self.assertFalse(self.halted)
        self.assertFalse(self.document["halted"])
        self.assertNotIn(
            "jev_dryrun_PARTIAL.json",
            os.listdir(self.tmpdir),
            "starvation is not a halt and must not produce a PARTIAL file",
        )

    def test_the_starved_run_writes_its_own_file_and_overwrites_nothing(self):
        """§7-E 4항. A paid measurement cannot be obtained again, so a starved
        re-run must not be able to land on the results filename."""
        self.assertEqual(
            sorted(os.listdir(self.tmpdir)),
            ["jev.json", "jev_dryrun.json", "jev_dryrun_STARVED.json"],
        )
        for path, (mtime, body) in zip(self.planted_paths, self.planted_before):
            with self.subTest(path=os.path.basename(path)):
                with open(path, "rb") as handle:
                    self.assertEqual(handle.read(), self.PLANTED)
                    handle.seek(0)
                    self.assertEqual(handle.read(), body)
                self.assertEqual(os.stat(path).st_mtime_ns, mtime)

    def test_the_console_names_the_separate_file_it_wrote(self):
        self.assertIn("jev_dryrun_STARVED.json", self.printed)
        self.assertIn("정식 결과 파일: 쓰지 않았다", self.printed)

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

    def test_a_starved_case_is_told_apart_from_a_deliberate_defer(self):
        """§7-D's 2026-09-22 표기 정정 makes `selector_deferred` the observation a
        "the model held back in time" claim is read off. A case that was never
        offered a call no longer carries that flag: its forced decision is
        counted and named instead, and the budget now leaves a trace in the
        record rather than only on the selector's own call record.
        """
        for case_id in ("C06", "C07", "C08"):
            case = self.cases[case_id]
            self.assertIs(case["selector_deferred"], False)
            self.assertEqual(case["defer_selections"], 0)
            self.assertEqual(case["forced_defers"], 1)
            self.assertEqual(case["forced_defer_reasons"], [REASON_BUDGET_EXHAUSTED])
            self.assertIs(case["starved"], True)
            self.assertNotIn("incomplete", case)
            blob = json.dumps(case, ensure_ascii=False).lower()
            self.assertIn("budget", blob)
            self.assertIn("exhaust", blob)

    def test_the_cases_that_kept_their_calls_are_untouched(self):
        for case_id in ("C01", "C02", "C03", "C04", "C05"):
            case = self.cases[case_id]
            self.assertNotIn("starved", case)
            self.assertNotIn("forced_defers", case)
            self.assertNotIn("forced_defer_reasons", case)

    def test_the_depressed_macro_average_is_no_longer_presentable(self):
        """The zero-yield cases still enter the macro mean — the fix does not
        rewrite the number — but the run carrying it is now marked uncomparable,
        names the starved cases, and cannot be read as a clean comparison."""
        self.assertEqual(self.document["summary"]["V-2_included_cases"], 7)
        self.assertEqual(self.document["summary"]["V-2_evidence_yield_macro"], 0.1015)
        self.assertIs(self.document["comparable"], False)
        self.assertEqual(self.document["starved_cases"], ["C06", "C07", "C08"])
        self.assertEqual(self.document["starved_case_count"], 3)
        self.assertIn("3개 사례", self.document["notice"])
        self.assertIn("§7 비교에 쓰지 않는다", self.document["notice"])

    def test_the_console_says_prominently_that_the_run_is_starved(self):
        self.assertIn("굶은 실행", self.printed)
        self.assertIn("예산 소진으로 굶은 사례: 3개", self.printed)
        self.assertIn("C06, C07, C08", self.printed)
        self.assertIn("STARVED", self.printed)


class ForcedDeferIsNotAModelDeferTests(unittest.TestCase):
    """Both directions of the distinction, in one place."""

    def test_a_forced_defer_and_a_genuine_defer_are_recorded_differently(self):
        starved = _score_one_case("C06", CallBudget(0), RunGuard())
        genuine = _score_one_case(
            "C06", CallBudget(FULL), RunGuard(), transport=_DeferringTransport
        )

        # The Controller cannot tell them apart — both reach it as the string
        # DEFER and both finish the same way. That is exactly why the scorer has
        # to, and this pair is what proves it does.
        self.assertEqual(starved["finish_reason"], "provider_deferred")
        self.assertEqual(genuine["finish_reason"], "provider_deferred")
        self.assertEqual(starved["retrievals"], 0)
        self.assertEqual(genuine["retrievals"], 0)

        self.assertIs(starved["selector_deferred"], False)
        self.assertEqual(starved["defer_selections"], 0)
        self.assertEqual(starved["forced_defers"], 1)
        self.assertIs(starved["starved"], True)

        self.assertIs(genuine["selector_deferred"], True)
        self.assertEqual(genuine["defer_selections"], 1)
        self.assertNotIn("forced_defers", genuine)
        self.assertNotIn("forced_defer_reasons", genuine)
        self.assertNotIn("starved", genuine)


class FailedCallIsNotAModelDeferTests(unittest.TestCase):
    """§7-E 3항. A request that went out and came back unusable is the fourth
    class, not the second. Both directions asserted together."""

    def test_a_failed_call_and_a_genuine_defer_are_recorded_differently(self):
        budget = CallBudget(FULL)
        failed = _score_one_case("C06", budget, RunGuard(), transport=_FailingTransport)
        genuine = _score_one_case(
            "C06", CallBudget(FULL), RunGuard(), transport=_DeferringTransport
        )

        self.assertEqual(failed["finish_reason"], "provider_deferred")
        self.assertEqual(genuine["finish_reason"], "provider_deferred")

        self.assertIs(failed["selector_deferred"], False)
        self.assertEqual(failed["defer_selections"], 0)
        self.assertEqual(failed["failed_decisions"], 1)
        self.assertEqual(failed["failed_decision_reasons"], ["http_status:429"])

        self.assertIs(genuine["selector_deferred"], True)
        self.assertEqual(genuine["defer_selections"], 1)
        self.assertNotIn("failed_decisions", genuine)
        self.assertNotIn("failed_decision_reasons", genuine)

    def test_a_failed_call_is_not_a_forced_one_and_did_spend_a_call(self):
        """The difference from `forced`: a request was actually sent, so the
        budget moved. That is why the two are separate counters."""
        budget = CallBudget(FULL)
        failed = _score_one_case("C06", budget, RunGuard(), transport=_FailingTransport)
        self.assertEqual(budget.used, 1)
        self.assertNotIn("forced_defers", failed)
        self.assertNotIn("starved", failed)

        starved_budget = CallBudget(0)
        starved = _score_one_case("C06", starved_budget, RunGuard())
        self.assertEqual(starved_budget.used, 0)
        self.assertNotIn("failed_decisions", starved)
        self.assertEqual(starved["forced_defers"], 1)


class ResultsFilenameTests(unittest.TestCase):
    """Each condition owns a filename, and the halt wins when both hold."""

    def test_each_condition_has_its_own_filename(self):
        scorer = _scorer()
        for dry_run, base, starved, halted in (
            (False, "jev.json", "jev_STARVED.json", "jev_PARTIAL.json"),
            (
                True,
                "jev_dryrun.json",
                "jev_dryrun_STARVED.json",
                "jev_dryrun_PARTIAL.json",
            ),
        ):
            with self.subTest(dry_run=dry_run):
                self.assertEqual(
                    os.path.basename(scorer.results_path_for("jev", dry_run=dry_run)),
                    base,
                )
                self.assertEqual(
                    os.path.basename(
                        scorer.results_path_for("jev", dry_run=dry_run, starved=True)
                    ),
                    starved,
                )
                self.assertEqual(
                    os.path.basename(
                        scorer.results_path_for("jev", dry_run=dry_run, halted=True)
                    ),
                    halted,
                )

    def test_a_run_that_is_both_halted_and_starved_takes_the_halt_filename(self):
        """The documented precedence. The combination cannot be produced by the
        loop — starvation means no request is ever sent again, and a halt needs
        a request that was sent — so it is pinned here rather than left to a
        future reader to rediscover."""
        scorer = _scorer()
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run):
                both = scorer.results_path_for(
                    "jev", dry_run=dry_run, halted=True, starved=True
                )
                self.assertEqual(
                    both, scorer.results_path_for("jev", dry_run=dry_run, halted=True)
                )
                self.assertNotEqual(
                    both, scorer.results_path_for("jev", dry_run=dry_run)
                )
                self.assertNotEqual(
                    both, scorer.results_path_for("jev", dry_run=dry_run, starved=True)
                )

    def test_neither_condition_can_ever_land_on_the_results_filename(self):
        scorer = _scorer()
        for selector in ("jev", "heuristic", "greedy"):
            plain = scorer.results_path_for(selector)
            for kwargs in ({"starved": True}, {"halted": True}, {"halted": True, "starved": True}):
                with self.subTest(selector=selector, **kwargs):
                    self.assertNotEqual(scorer.results_path_for(selector, **kwargs), plain)


class ForcedDeferReasonsTests(unittest.TestCase):
    """The reason is recorded, and the two ways a decision can be forced are
    named apart — only one of them is starvation."""

    def test_an_exhausted_budget_is_named_and_counts_as_starvation(self):
        record = _score_one_case("C06", CallBudget(0), RunGuard())
        self.assertEqual(record["forced_defers"], 1)
        self.assertEqual(record["forced_defer_reasons"], [REASON_BUDGET_EXHAUSTED])
        self.assertIs(record["starved"], True)

    def test_a_halted_run_is_named_and_is_not_starvation(self):
        guard = RunGuard()
        guard.halt("http_status:429")
        record = _score_one_case("C06", CallBudget(FULL), guard)
        self.assertEqual(record["forced_defers"], 1)
        self.assertEqual(
            record["forced_defer_reasons"],
            ["%s:http_status:429" % REASON_RUN_HALTED],
        )
        self.assertNotIn("starved", record)
        self.assertIs(record["selector_deferred"], False)

    def test_a_halted_run_spends_no_budget_on_the_decisions_it_skips(self):
        guard = RunGuard()
        guard.halt("http_status:429")
        budget = CallBudget(FULL)
        _score_one_case("C06", budget, guard)
        self.assertEqual(budget.used, 0)


class StarvedAndIncompleteAreIndependentTests(unittest.TestCase):
    """`starved` says no call was ever offered; `incomplete` says a call was
    attempted and the run stopped during this case. Neither implies the other."""

    @classmethod
    def setUpClass(cls):
        cls.starved_only = _score_one_case("C06", CallBudget(0), RunGuard())
        cls.neither = _score_one_case("C06", CallBudget(FULL), RunGuard())
        cls.incomplete_only = _halted_during_case()

    def test_a_starved_case_is_not_marked_incomplete(self):
        self.assertIs(self.starved_only["starved"], True)
        self.assertNotIn("incomplete", self.starved_only)

    def test_an_incomplete_case_is_not_marked_starved(self):
        self.assertIs(self.incomplete_only["incomplete"], True)
        self.assertNotIn("starved", self.incomplete_only)
        self.assertEqual(self.incomplete_only["decision_calls"], 2)

    def test_a_case_that_got_its_calls_carries_neither_mark(self):
        self.assertNotIn("starved", self.neither)
        self.assertNotIn("incomplete", self.neither)
        self.assertNotIn("forced_defers", self.neither)
        self.assertIs(self.neither["selector_deferred"], False)


def _halted_during_case():
    """The scored record of the case a §7-E halt was raised during, produced by
    a real `main` run whose 5th case fails its second decision."""
    from .test_jev_dryrun import _FailingSecondRoundTransport

    scorer = _scorer()
    tmpdir = tempfile.mkdtemp(prefix="bia-budget-halt-")
    seen = {"cases": 0}

    def factory():
        seen["cases"] += 1
        return JevSelector(
            transport=_FailingSecondRoundTransport(seen["cases"] == 5),
            budget=scorer._JEV_PROCESS_BUDGET,
            guard=scorer._JEV_PROCESS_GUARD,
        )

    real_factories = dict(scorer.DRY_RUN_FACTORIES)
    with _fresh_run(tmpdir):
        scorer.DRY_RUN_FACTORIES["jev"] = factory
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                scorer.main(["--selector", "jev", "--dry-run"])
        finally:
            scorer.DRY_RUN_FACTORIES.clear()
            scorer.DRY_RUN_FACTORIES.update(real_factories)
    with open(os.path.join(tmpdir, "jev_dryrun_PARTIAL.json"), encoding="utf-8") as h:
        document = json.load(h)
    shutil.rmtree(tmpdir, ignore_errors=True)
    return document["cases"][-1]


class CleanRunIsUnchangedTests(unittest.TestCase):
    """A run where nothing starved must look exactly as it did before."""

    def _run(self, argv):
        tmpdir = tempfile.mkdtemp(prefix="bia-clean-")
        buffer = io.StringIO()
        with _fresh_run(tmpdir) as scorer:
            with contextlib.redirect_stdout(buffer):
                code = scorer.main(argv)
        listing = sorted(os.listdir(tmpdir))
        with open(os.path.join(tmpdir, listing[0]), encoding="utf-8") as handle:
            document = json.load(handle)
        shutil.rmtree(tmpdir, ignore_errors=True)
        return code, document, buffer.getvalue(), listing

    def test_a_code_baseline_run_carries_no_starvation_marking_at_all(self):
        code, document, printed, listing = self._run(["--selector", "heuristic"])
        # 1 is the baseline's own verdict (it does not clear the V-2 floor), not
        # a starvation exit. The point is that it is unchanged and is not 2.
        self.assertEqual(code, 1)
        self.assertEqual(listing, ["heuristic.json"])
        self.assertNotIn("comparable", document)
        self.assertNotIn("starved_cases", document)
        self.assertNotIn("notice", document)
        self.assertNotIn("굶은 실행", printed)
        for case in document["cases"]:
            self.assertNotIn("starved", case)
            self.assertNotIn("forced_defers", case)
            self.assertNotIn("forced_defer_reasons", case)
            self.assertNotIn("failed_decisions", case)
            self.assertNotIn("failed_decision_reasons", case)

    def test_the_unstarved_rehearsal_keeps_its_own_markings_and_exit_code(self):
        code, document, printed, listing = self._run(["--selector", "jev", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertEqual(listing, ["jev_dryrun.json"])
        self.assertIs(document["simulated"], True)
        self.assertIs(document["comparable"], False)
        self.assertNotIn("starved_cases", document)
        self.assertNotIn("굶은 실행", printed)
        self.assertEqual(document["calls_spent"], 11)
        self.assertEqual(document["cases_completed"], 8)


if __name__ == "__main__":
    unittest.main()
