"""The offline rehearsal (§7-E 실행 전 오프라인 리허설). Fakes only.

Nothing here opens a socket, reads an API key, or constructs an HttpTransport.
The rehearsal exists precisely so that the 8-case loop can be run to completion
for free: the locked default transport fails on its first call, the run halts at
C01, and the loop had therefore never executed end to end. Discovering a wiring
defect that way would cost paid calls.

The rehearsal is not a measurement and these tests assert that it cannot be
mistaken for one.
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from bia.jev import (
    JEV_MODEL,
    QUESTION_KEY,
    CallBudget,
    JevHttpError,
    JevSelector,
    JevTransportError,
    RehearsalTransport,
    first_response_shape_error,
)
from bia.types import DEFER

from .test_jev_adapter import first_round_inputs


def _scorer():
    import importlib
    import sys

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)
    return importlib.import_module("eval.v1.run_eval")


@contextlib.contextmanager
def _fresh_run(results_dir: str):
    """A run's shared budget and halt flag are process-wide globals; a test that
    leaves them dirty would poison the next one."""
    scorer = _scorer()
    real_results_dir = scorer.RESULTS_DIR
    budget = scorer._JEV_PROCESS_BUDGET
    guard = scorer._JEV_PROCESS_GUARD
    saved = (budget.used, guard.halted, guard.halt_reason, guard.first_response_checked)
    budget.used = 0
    guard.halted = False
    guard.halt_reason = ""
    guard.first_response_checked = False
    scorer.RESULTS_DIR = results_dir
    try:
        yield scorer
    finally:
        scorer.RESULTS_DIR = real_results_dir
        budget.used, guard.halted, guard.halt_reason, guard.first_response_checked = saved


class RehearsalTransportShapeTests(unittest.TestCase):
    """What comes back must satisfy the adapter's own §7-E strict check."""

    def setUp(self):
        self.state, self.candidates = first_round_inputs()
        self.transport = RehearsalTransport()
        self.selector = JevSelector(transport=self.transport, budget=CallBudget(16))

    def test_the_response_passes_the_first_response_shape_check(self):
        request = self.selector.build_request(self.state, self.candidates)
        body = self.transport.send(request).body
        self.assertIsNone(first_response_shape_error(body, QUESTION_KEY))
        self.assertEqual(body["model"], JEV_MODEL)
        answer = body["answers"][QUESTION_KEY]
        self.assertEqual(answer["type"], "choice")
        self.assertIsInstance(answer["choice"], str)
        self.assertIsInstance(answer["confidence"], float)
        self.assertIsInstance(answer["probabilities"], dict)
        self.assertIsInstance(body["usage"]["input_tokens"], int)
        self.assertIsInstance(body["usage"]["output_tokens"], int)

    def test_the_probabilities_map_covers_exactly_the_offered_options(self):
        request = self.selector.build_request(self.state, self.candidates)
        body = self.transport.send(request).body
        offered = set(request["questions"][QUESTION_KEY]["criteria"])
        self.assertEqual(set(body["answers"][QUESTION_KEY]["probabilities"]), offered)

    def test_the_choice_is_always_one_of_the_options_actually_offered(self):
        offered_sets = [
            self.candidates,
            self.candidates[:1],
            self.candidates[1:],
            list(reversed(self.candidates)),
        ]
        for candidates in offered_sets:
            with self.subTest(n=len(candidates)):
                request = self.selector.build_request(self.state, list(candidates))
                body = RehearsalTransport().send(request).body
                offered = set(request["questions"][QUESTION_KEY]["criteria"])
                self.assertIn(body["answers"][QUESTION_KEY]["choice"], offered)

    def test_the_selector_accepts_it_and_the_run_is_not_halted(self):
        returned = self.selector.select_next_evidence(self.state, self.candidates)
        self.assertEqual(returned, self.candidates[0].candidate_id)
        self.assertFalse(self.selector.guard.halted)
        self.assertTrue(self.selector.guard.first_response_checked)
        record = self.selector.records[-1]
        self.assertIsNone(record.failure_reason)
        self.assertEqual(record.response_model, JEV_MODEL)
        self.assertEqual(record.estimated_cost_usd, 0.0)

    def test_a_request_with_no_options_is_an_error_not_a_silent_answer(self):
        with self.assertRaises(JevTransportError):
            RehearsalTransport().send({"state": "{}", "model": JEV_MODEL, "questions": {}})


class ChoicePolicyTests(unittest.TestCase):
    """The policy is exactly 'first option in criteria order', and criteria order
    is the server's fixed cell → product → complaint_type → DEFER."""

    def setUp(self):
        self.state, self.candidates = first_round_inputs()
        self.selector = JevSelector(
            transport=RehearsalTransport(), budget=CallBudget(16)
        )

    def _choice_for(self, candidates):
        request = self.selector.build_request(self.state, list(candidates))
        transport = RehearsalTransport()
        body = transport.send(request).body
        criteria = list(request["questions"][QUESTION_KEY]["criteria"])
        return body["answers"][QUESTION_KEY]["choice"], criteria

    def test_the_first_option_is_a_cell_and_it_is_the_one_chosen(self):
        cells = [c for c in self.candidates if c.kind == "cell"]
        self.assertTrue(cells, "fixture must offer at least one cell candidate")
        choice, criteria = self._choice_for(self.candidates)
        self.assertEqual(choice, criteria[0])
        self.assertEqual(choice, cells[0].candidate_id)

    def test_a_different_offered_set_moves_the_choice_with_it(self):
        broad = [c for c in self.candidates if c.kind != "cell"]
        self.assertTrue(broad, "fixture must offer at least one broad candidate")
        choice, criteria = self._choice_for(broad)
        self.assertEqual(choice, criteria[0])
        self.assertEqual(choice, broad[0].candidate_id)
        cells = [c.candidate_id for c in self.candidates if c.kind == "cell"]
        self.assertNotIn(choice, cells)

    def test_the_escape_option_is_never_first_while_anything_is_offered(self):
        choice, criteria = self._choice_for(self.candidates)
        self.assertEqual(criteria[-1], DEFER)
        self.assertNotEqual(choice, DEFER)

    def test_the_policy_is_named_on_the_class_and_stated_in_the_docstring(self):
        self.assertEqual(RehearsalTransport.policy, "first_option_in_criteria_order")
        self.assertIn("first_option_in_criteria_order", RehearsalTransport.__doc__)

    def test_the_transport_cannot_reach_the_network(self):
        """AST over the class body: no urllib, no socket, no urlopen."""
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bia", "jev.py"
        )
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)
        node = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.ClassDef) and n.name == "RehearsalTransport"
        )
        names = {a.attr for a in ast.walk(node) if isinstance(a, ast.Attribute)} | {
            n.id for n in ast.walk(node) if isinstance(n, ast.Name)
        }
        for forbidden in ("urllib", "urlopen", "socket", "HttpTransport"):
            self.assertNotIn(forbidden, names)


_DRY_RUN = {}


def _dry_run_once():
    """One real pass of `main --selector jev --dry-run`, shared by the two
    classes below. It is a real end-to-end run, not a stub: the loop, the shared
    budget, the halt flag and the file writing are all the production ones."""
    if _DRY_RUN:
        return _DRY_RUN
    tmpdir = tempfile.mkdtemp(prefix="bia-dryrun-")
    buffer = io.StringIO()
    with _fresh_run(tmpdir) as scorer:
        with contextlib.redirect_stdout(buffer):
            code = scorer.main(["--selector", "jev", "--dry-run"])
        calls_spent = scorer._JEV_PROCESS_BUDGET.used
        budget_max = scorer._JEV_PROCESS_BUDGET.maximum
    with open(os.path.join(tmpdir, "jev_dryrun.json"), encoding="utf-8") as handle:
        document = json.load(handle)
    _DRY_RUN.update(
        code=code,
        printed=buffer.getvalue(),
        document=document,
        calls_spent=calls_spent,
        budget_max=budget_max,
        tmpdir=tmpdir,
        listing=sorted(os.listdir(tmpdir)),
    )
    return _DRY_RUN


def tearDownModule():
    if _DRY_RUN:
        shutil.rmtree(_DRY_RUN["tmpdir"], ignore_errors=True)


class _SharedDryRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        run = _dry_run_once()
        cls.code = run["code"]
        cls.printed = run["printed"]
        cls.document = run["document"]
        cls.calls_spent = run["calls_spent"]
        cls.budget_max = run["budget_max"]
        cls.tmpdir = run["tmpdir"]
        cls.listing = run["listing"]


class DryRunCompletesTests(_SharedDryRun):
    """The whole point: all 8 cases run to completion, for free."""

    def test_the_run_completes_all_eight_cases_without_halting(self):
        self.assertEqual(self.code, 0)
        self.assertFalse(self.document["halted"])
        self.assertEqual(self.document["halt_reason"], "")
        self.assertEqual(len(self.document["cases"]), 8)
        self.assertEqual(
            [case["case_id"] for case in self.document["cases"]],
            ["C0%d" % n for n in range(1, 9)],
        )
        self.assertEqual(self.document["cases_completed"], 8)
        self.assertEqual(self.document["summary"]["crashed"], 0)

    def test_the_calls_spent_are_counted_and_inside_the_sixteen_call_budget(self):
        self.assertEqual(self.budget_max, 16)
        self.assertEqual(self.calls_spent, 11)
        self.assertEqual(self.document["calls_spent"], 11)
        self.assertLessEqual(self.document["calls_spent"], 16)
        self.assertEqual(self.document["real_model_calls"], 0)

    def test_no_case_is_marked_incomplete_in_a_clean_run(self):
        for case in self.document["cases"]:
            self.assertNotIn("incomplete", case)

    def test_the_normal_scoring_fields_are_present_so_the_plumbing_is_exercised(self):
        summary = self.document["summary"]
        for key in (
            "V-1_fact_accuracy",
            "V-2_evidence_yield_macro",
            "V-3_evidence_precision_macro",
            "V-5_wasted_retrievals",
            "V-6_total_decision_calls",
        ):
            self.assertIn(key, summary)
        for case in self.document["cases"]:
            self.assertIn("failures", case)
            self.assertIn("retrievals", case)


class DryRunIsNotAMeasurementTests(_SharedDryRun):
    """Same single run; these assert it cannot be read as a JEV result."""

    def test_the_document_is_marked_simulated_and_not_comparable(self):
        self.assertIs(self.document["simulated"], True)
        self.assertIs(self.document["comparable"], False)
        self.assertEqual(self.document["transport"], "rehearsal")
        self.assertEqual(
            self.document["choice_policy"], RehearsalTransport.policy
        )

    def test_the_notice_says_in_korean_what_it_is_and_is_not(self):
        notice = self.document["notice"]
        self.assertIn("리허설", notice)
        self.assertIn("첫 번째 선택지", notice)
        self.assertIn("§7 비교에 쓰지 않는다", notice)

    def test_the_results_filename_is_never_the_measurement_filename(self):
        self.assertTrue(os.path.isfile(os.path.join(self.tmpdir, "jev_dryrun.json")))
        self.assertFalse(
            os.path.isfile(os.path.join(self.tmpdir, "jev.json")),
            "a dry run must never write the results filename",
        )
        self.assertEqual(self.listing, ["jev_dryrun.json"])

    def test_the_console_warns_prominently_and_prints_no_verdict(self):
        self.assertIn("리허설", self.printed)
        self.assertIn("first_option_in_criteria_order", self.printed)
        self.assertIn("실제 모델 호출", self.printed)
        self.assertNotIn("전체: PASS", self.printed)
        self.assertNotIn("전체: FAIL", self.printed)
        self.assertNotIn("== 기준 판정 ==", self.printed)


class DryRunArgumentTests(unittest.TestCase):
    def test_dry_run_with_a_non_jev_selector_fails_clearly(self):
        scorer = _scorer()
        for selector in ("heuristic", "greedy"):
            with self.subTest(selector=selector):
                buffer = io.StringIO()
                with self.assertRaises(SystemExit) as caught:
                    with contextlib.redirect_stdout(buffer):
                        scorer.main(["--selector", selector, "--dry-run"])
                message = str(caught.exception)
                self.assertIn("--dry-run", message)
                self.assertIn("jev", message)
                self.assertIn(selector, message)

    def test_the_rehearsal_factory_reads_no_environment_variable(self):
        """AST: the rehearsal path holds no key and consults no env var, so
        `TYPESAFE_API_KEY` cannot be touched by it."""
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "eval",
            "v1",
            "run_eval.py",
        )
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)
        node = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "build_jev_rehearsal_selector"
        )
        names = {a.attr for a in ast.walk(node) if isinstance(a, ast.Attribute)} | {
            n.id for n in ast.walk(node) if isinstance(n, ast.Name)
        }
        for forbidden in ("environ", "getenv", "os"):
            self.assertNotIn(forbidden, names)

    def test_the_rehearsal_shares_the_process_budget_and_halt_flag(self):
        scorer = _scorer()
        first = scorer.build_jev_rehearsal_selector()
        second = scorer.build_jev_rehearsal_selector()
        self.assertIs(first.budget, scorer._JEV_PROCESS_BUDGET)
        self.assertIs(first.guard, scorer._JEV_PROCESS_GUARD)
        self.assertIs(first.budget, second.budget)
        self.assertIs(first.guard, second.guard)
        self.assertIsInstance(first.transport, RehearsalTransport)

    def test_the_lock_is_untouched_by_the_rehearsal(self):
        """Without --dry-run the locked factory still stops a live run."""
        from unittest import mock

        scorer = _scorer()
        with mock.patch.dict(
            "os.environ",
            {"BIA_JEV_LIVE": "1", "TYPESAFE_API_KEY": "placeholder-not-a-key"},
        ):
            with self.assertRaises(SystemExit) as caught:
                scorer.SELECTORS["jev"]()
        self.assertIn("explicit cost approval", str(caught.exception))
        self.assertNotIn("placeholder-not-a-key", str(caught.exception))


class _FailingSecondRoundTransport(RehearsalTransport):
    """Answers normally until the request shows the SECOND decision of a case is
    under way (`decision_calls_used` is incremented before the provider is
    called, so round 2 shows 2), then fails that call: the §7-E halt happens
    mid-case, after round 1 of that case already succeeded."""

    def __init__(self, fail_this_case: bool) -> None:
        super().__init__()
        self._fail_this_case = fail_this_case

    def send(self, request):
        state = json.loads(request["state"])
        if self._fail_this_case and state.get("decision_calls_used") == 2:
            self.requests.append(request)
            raise JevHttpError(429, None)
        return super().send(request)


class IncompleteCaseTests(unittest.TestCase):
    """A case whose later decision failed is still scored, and is marked."""

    def _run_with_failure_on_case(self, failing_index: int):
        scorer = _scorer()
        tmpdir = tempfile.mkdtemp(prefix="bia-dryrun-halt-")
        seen = {"cases": 0}

        def factory():
            seen["cases"] += 1
            return JevSelector(
                transport=_FailingSecondRoundTransport(
                    seen["cases"] == failing_index
                ),
                budget=scorer._JEV_PROCESS_BUDGET,
                guard=scorer._JEV_PROCESS_GUARD,
            )

        buffer = io.StringIO()
        real_factories = dict(scorer.DRY_RUN_FACTORIES)
        with _fresh_run(tmpdir):
            scorer.DRY_RUN_FACTORIES["jev"] = factory
            try:
                with contextlib.redirect_stdout(buffer):
                    code = scorer.main(["--selector", "jev", "--dry-run"])
            finally:
                scorer.DRY_RUN_FACTORIES.clear()
                scorer.DRY_RUN_FACTORIES.update(real_factories)
        path = os.path.join(tmpdir, "jev_dryrun_PARTIAL.json")
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
        listing = sorted(os.listdir(tmpdir))
        shutil.rmtree(tmpdir, ignore_errors=True)
        return code, document, listing, buffer.getvalue()

    def test_the_case_the_run_halted_during_is_marked_incomplete(self):
        # C05 is the 5th case and spends two decisions under this policy, so the
        # failure lands on its SECOND round: the case is scored, included, and
        # must not read as a clean one.
        code, document, listing, printed = self._run_with_failure_on_case(5)
        self.assertNotEqual(code, 0)
        self.assertTrue(document["halted"])
        self.assertEqual(document["halt_reason"], "http_status:429")
        cases = document["cases"]
        self.assertEqual([c["case_id"] for c in cases], ["C01", "C02", "C03", "C04", "C05"])
        self.assertIs(cases[-1].get("incomplete"), True)
        self.assertEqual(cases[-1]["decision_calls"], 2)
        for clean in cases[:-1]:
            self.assertNotIn("incomplete", clean)
        self.assertIn("중단 중 실행된 사례", printed)
        self.assertIn("C05", printed)

    def test_a_halted_dry_run_writes_neither_results_file(self):
        _, document, listing, _ = self._run_with_failure_on_case(5)
        self.assertEqual(listing, ["jev_dryrun_PARTIAL.json"])
        self.assertIs(document["simulated"], True)
        self.assertIs(document["comparable"], False)
        self.assertNotIn("summary", document)


if __name__ == "__main__":
    unittest.main()
