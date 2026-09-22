"""Run telemetry, and the refusal to overwrite a paid result file.

Fakes only. Nothing here opens a socket, reads `TYPESAFE_API_KEY`, or builds an
`HttpTransport`. The one real file these tests touch is
`eval/v1/results/jev.json`, and they touch it only by asserting that the scorer
REFUSES to write it.

Two things are being protected:

* the telemetry that `jev.json` is missing — what answered, how many calls, how
  many tokens, what it cost, how long it took — so the next live run records it;
* `jev.json` itself, which cost real money and cannot be produced again.
"""
from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bia.jev import (  # noqa: E402
    CallBudget,
    FakeTransport,
    JevResponse,
    JevSelector,
    RunGuard,
)
from bia.types import DEFER  # noqa: E402

from .test_jev_adapter import answer_response, first_round_inputs  # noqa: E402


def _scorer():
    return importlib.import_module("eval.v1.run_eval")


@contextlib.contextmanager
def _fresh_run(results_dir: str):
    """The budget and halt flag are process-wide; a dirty one poisons the next
    test. RESULTS_DIR is redirected so no test ever writes into the repository."""
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


def _run_main(results_dir, argv):
    buffer = io.StringIO()
    errors = io.StringIO()
    with _fresh_run(results_dir) as scorer:
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(errors):
            code = scorer.main(argv)
    return code, buffer.getvalue(), errors.getvalue()


def _read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _recorded(transport, decisions=1):
    """Drive the REAL decision path: recorder → JevSelector → transport."""
    scorer = _scorer()
    state, candidates = first_round_inputs()
    selector = JevSelector(
        transport=transport, budget=CallBudget(16), guard=RunGuard()
    )
    recorder = scorer._RecordingSelector(selector)
    returned = []
    for _ in range(decisions):
        returned.append(recorder.select_next_evidence(state, candidates))
    return recorder, returned, candidates


class CodeBaselineTelemetryTests(unittest.TestCase):
    """A run with no model in it must say so, and must still time itself."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bia-telemetry-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_a_code_baseline_reports_zero_calls_and_the_code_marker(self):
        code, _, _ = _run_main(self.tmp, ["--selector", "heuristic"])
        self.assertIn(code, (0, 1), "the run itself must complete")
        telemetry = _read(os.path.join(self.tmp, "heuristic.json"))["telemetry"]

        scorer = _scorer()
        self.assertEqual(telemetry["provider"], scorer.PROVIDER_CODE)
        self.assertEqual(telemetry["model_calls"], 0)
        self.assertEqual(telemetry["real_model_calls"], 0)
        self.assertFalse(telemetry["live_model_run"])
        self.assertEqual(telemetry["decision_strategy"], "heuristic")

        # No model reported tokens, so nothing is invented for them.
        self.assertIsNone(telemetry["input_tokens"])
        self.assertIsNone(telemetry["output_tokens"])
        self.assertIsNone(telemetry["estimated_cost_usd"])

        # Latency is wall clock and is measured even without a model.
        self.assertGreater(telemetry["total_latency_ms"], 0.0)
        latency = telemetry["decision_latency_ms"]
        self.assertGreater(latency["count"], 0)
        self.assertGreater(latency["total"], 0.0)
        self.assertGreater(latency["mean"], 0.0)
        self.assertEqual(len(latency["values"]), latency["count"])
        self.assertTrue(all(value > 0.0 for value in latency["values"]))
        self.assertLess(
            latency["total"],
            telemetry["total_latency_ms"],
            "per-decision time cannot exceed the whole run",
        )

    def test_the_summary_call_count_is_no_longer_a_hardcoded_zero(self):
        scorer = _scorer()
        with open(scorer.__file__, encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn('"real_model_calls": 0,', source)


class RehearsalTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bia-telemetry-dry-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_the_rehearsal_names_a_fake_and_counts_its_calls(self):
        code, _, _ = _run_main(self.tmp, ["--selector", "jev", "--dry-run"])
        self.assertEqual(code, 0)
        document = _read(os.path.join(self.tmp, "jev_dryrun.json"))
        telemetry = document["telemetry"]
        scorer = _scorer()

        self.assertEqual(telemetry["provider"], scorer.PROVIDER_REHEARSAL)
        self.assertFalse(telemetry["live_model_run"])
        # Calls were made — to a fake. Both numbers are true at once and are
        # kept apart so neither can be read as the other.
        self.assertEqual(telemetry["model_calls"], document["calls_spent"])
        self.assertGreater(telemetry["model_calls"], 0)
        self.assertEqual(telemetry["real_model_calls"], 0)
        self.assertEqual(document["real_model_calls"], 0)

        # Whatever the fake reported: it reports zero tokens because zero were
        # spent, so the estimate derived from them is 0.0 rather than invented.
        self.assertEqual(telemetry["input_tokens"], 0)
        self.assertEqual(telemetry["output_tokens"], 0)
        self.assertEqual(telemetry["estimated_cost_usd"], 0.0)
        self.assertTrue(telemetry["estimated_cost_usd_is_an_estimate"])


class DerivedCallCountTests(unittest.TestCase):
    """N calls must yield N. A constant would pass at exactly one value of N."""

    def test_the_count_follows_the_adapter_records(self):
        scorer = _scorer()
        for expected in (1, 2, 3, 5):
            transport = FakeTransport([answer_response(DEFER)], repeat_last=True)
            recorder, _, _ = _recorded(transport, decisions=expected)
            telemetry = scorer.RunTelemetry("jev")
            telemetry.absorb(recorder)
            self.assertEqual(telemetry.model_calls, expected)
            self.assertEqual(transport.call_count, expected)

    def test_a_provider_without_records_derives_zero_and_says_why(self):
        scorer = _scorer()

        class NoRecords(object):
            name = "no-records"

            def select_next_evidence(self, state, candidates):
                return DEFER

        state, candidates = first_round_inputs()
        recorder = scorer._RecordingSelector(NoRecords())
        recorder.select_next_evidence(state, candidates)
        telemetry = scorer.RunTelemetry("no-records")
        telemetry.absorb(recorder)
        self.assertEqual(telemetry.model_calls, 0)
        self.assertEqual(telemetry.provider, scorer.PROVIDER_CODE)

    def test_calls_that_never_answered_do_not_borrow_a_model_name(self):
        scorer = _scorer()
        transport = FakeTransport([JevResponse(body="not json at all")])
        recorder, _, _ = _recorded(transport, decisions=1)
        telemetry = scorer.RunTelemetry("jev")
        telemetry.absorb(recorder)
        self.assertEqual(telemetry.model_calls, 1)
        self.assertEqual(telemetry.models_answered, [])
        self.assertTrue(telemetry.provider.endswith(scorer.NO_RESPONSE_SUFFIX))
        self.assertFalse(scorer.provider_is_live_model(telemetry.provider))
        self.assertEqual(telemetry.real_model_calls, 0)


class PerDecisionTelemetryTests(unittest.TestCase):
    def test_a_full_response_is_recorded_field_by_field(self):
        state, candidates = first_round_inputs()
        chosen = candidates[0].candidate_id
        probabilities = {chosen: 0.61, DEFER: 0.39}
        transport = FakeTransport(
            [
                JevResponse(
                    body=answer_response(
                        chosen,
                        probabilities=probabilities,
                        confidence=0.71,
                        input_tokens=392,
                        output_tokens=65,
                    ),
                    headers={"x-typesafe-request-id": "req-77"},
                )
            ]
        )
        recorder, returned, _ = _recorded(transport, decisions=1)
        item = recorder.telemetry[0].as_dict()

        self.assertEqual(returned, [chosen])
        self.assertEqual(item["selected_candidate"], chosen)
        self.assertEqual(item["kind"], candidates[0].kind)
        self.assertEqual(item["confidence"], 0.71)
        self.assertEqual(item["probabilities"], probabilities)
        self.assertEqual(item["model"], "jev-1.13.0")
        self.assertEqual(item["input_tokens"], 392)
        self.assertEqual(item["output_tokens"], 65)
        self.assertEqual(item["request_id"], "req-77")
        self.assertTrue(item["called"])
        self.assertIsNone(item["failure_reason"])
        self.assertGreater(item["latency_ms"], 0.0)
        self.assertAlmostEqual(
            item["estimated_cost_usd"], 392 * 0.042 / 1_000_000.0, places=12
        )

    def test_absent_values_stay_null_and_are_never_invented(self):
        state, candidates = first_round_inputs()
        chosen = candidates[0].candidate_id
        body = {
            "model": "jev-1.13.0",
            "answers": {"next_evidence": {"type": "choice", "choice": chosen}},
            "usage": {"input_tokens": 100},
        }
        transport = FakeTransport([JevResponse(body=body)])
        recorder, returned, _ = _recorded(transport, decisions=1)
        item = recorder.telemetry[0].as_dict()

        self.assertEqual(returned, [chosen])
        self.assertEqual(item["selected_candidate"], chosen)
        self.assertIsNone(item["confidence"])
        self.assertIsNone(item["probabilities"])
        self.assertIsNone(item["output_tokens"])
        self.assertIsNone(item["request_id"])
        self.assertEqual(item["input_tokens"], 100)

    def test_a_defer_is_recorded_as_defer_with_no_kind(self):
        transport = FakeTransport([answer_response(DEFER)])
        recorder, returned, _ = _recorded(transport, decisions=1)
        item = recorder.telemetry[0].as_dict()
        self.assertEqual(returned, [DEFER])
        self.assertEqual(item["selected_candidate"], DEFER)
        self.assertIsNone(item["kind"])

    def test_the_scored_case_record_carries_the_per_decision_block(self):
        tmp = tempfile.mkdtemp(prefix="bia-telemetry-case-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        _run_main(tmp, ["--selector", "heuristic"])
        cases = _read(os.path.join(tmp, "heuristic.json"))["cases"]
        for case in cases:
            self.assertIn("decision_telemetry", case)
            for item in case["decision_telemetry"]:
                self.assertIn("selected_candidate", item)
                self.assertIn("confidence", item)
                self.assertIn("probabilities", item)
                self.assertGreater(item["latency_ms"], 0.0)


class NothingBranchesOnConfidenceTests(unittest.TestCase):
    """§7-D: confidence and probabilities are recorded, never acted on."""

    def test_the_chosen_option_wins_even_with_the_lowest_confidence(self):
        state, candidates = first_round_inputs()
        chosen = candidates[0].candidate_id
        louder = candidates[1].candidate_id if len(candidates) > 1 else DEFER
        transport = FakeTransport(
            [
                answer_response(
                    chosen,
                    probabilities={chosen: 0.02, louder: 0.97},
                    confidence=0.01,
                )
            ]
        )
        recorder, returned, _ = _recorded(transport, decisions=1)
        self.assertEqual(returned, [chosen])
        item = recorder.telemetry[0].as_dict()
        self.assertEqual(item["confidence"], 0.01)
        self.assertEqual(item["probabilities"][louder], 0.97)

    def test_the_module_docstring_still_states_the_rule(self):
        import bia.jev as jev

        self.assertIn("recorded, never acted on", jev.__doc__)


class OverwriteProtectionTests(unittest.TestCase):
    """The part that matters most: a paid result file is written once."""

    LIVE = {
        "selector": "heuristic",
        "telemetry": {"provider": "jev-1.13.0", "model_calls": 10},
        "summary": {},
        "cases": [],
    }

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bia-overwrite-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.target = os.path.join(self.tmp, "heuristic.json")
        with open(self.target, "w", encoding="utf-8") as handle:
            json.dump(self.LIVE, handle)
        os.utime(self.target, (1_600_000_000, 1_600_000_000))
        self.before_bytes = _read_bytes(self.target)
        self.before_mtime = os.stat(self.target).st_mtime

    def _assert_untouched(self):
        self.assertEqual(_read_bytes(self.target), self.before_bytes)
        self.assertEqual(os.stat(self.target).st_mtime, self.before_mtime)

    def test_a_live_file_is_not_overwritten_and_the_run_exits_non_zero(self):
        code, _, errors = _run_main(self.tmp, ["--selector", "heuristic"])
        self.assertNotEqual(code, 0)
        self.assertIn("--label", errors)
        self._assert_untouched()

    def test_a_label_writes_the_labelled_path_instead(self):
        code, _, _ = _run_main(
            self.tmp, ["--selector", "heuristic", "--label", "rerun"]
        )
        self.assertIn(code, (0, 1))
        labelled = os.path.join(self.tmp, "heuristic_rerun.json")
        self.assertTrue(os.path.isfile(labelled))
        self.assertIn("telemetry", _read(labelled))
        self._assert_untouched()

    def test_a_free_file_is_still_overwritten_as_before(self):
        free = os.path.join(self.tmp, "greedy.json")
        with open(free, "w", encoding="utf-8") as handle:
            json.dump({"selector": "greedy", "summary": {}, "cases": []}, handle)
        code, _, _ = _run_main(self.tmp, ["--selector", "greedy"])
        self.assertIn(code, (0, 1))
        self.assertIn("telemetry", _read(free))

    def test_a_rehearsal_file_is_not_mistaken_for_a_paid_one(self):
        """A rehearsal makes calls, so `model_calls > 0` alone would quarantine
        every rehearsal file. `provider` is what decides."""
        scorer = _scorer()
        path = os.path.join(self.tmp, "jev_dryrun.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "telemetry": {
                        "provider": scorer.PROVIDER_REHEARSAL,
                        "model_calls": 11,
                    }
                },
                handle,
            )
        live, _ = scorer.existing_run_is_live(path)
        self.assertFalse(live)
        scorer.assert_writable(path)

    def test_an_unreadable_existing_file_is_treated_as_paid(self):
        scorer = _scorer()
        broken = os.path.join(self.tmp, "broken.json")
        with open(broken, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        live, reason = scorer.existing_run_is_live(broken)
        self.assertTrue(live)
        self.assertTrue(reason)
        with self.assertRaises(scorer.ResultsOverwriteRefused):
            scorer.assert_writable(broken)

    def test_labels_are_restricted_to_a_safe_character_set(self):
        scorer = _scorer()
        for bad in ("../escape", "a/b", "", ".hidden", "with space", "x" * 65, "-lead"):
            with self.assertRaises(SystemExit):
                scorer.validate_label(bad)
        for good in ("rerun", "2026-09-22", "run_2", "v1.1"):
            self.assertEqual(scorer.validate_label(good), good)

    def test_an_unknown_provider_marker_fails_closed(self):
        scorer = _scorer()
        path = os.path.join(self.tmp, "unknown.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"telemetry": {"provider": "something-new", "model_calls": 0}}, handle)
        live, _ = scorer.existing_run_is_live(path)
        self.assertTrue(live)


class KnownPaidArtifactTests(unittest.TestCase):
    """`eval/v1/results/jev.json` predates telemetry: it cannot be detected, so
    it is named."""

    def test_the_paid_jev_result_is_protected_by_name(self):
        scorer = _scorer()
        path = os.path.join(scorer.REPO_ROOT, "eval", "v1", "results", "jev.json")
        self.assertIn(path, scorer.KNOWN_PAID_ARTIFACTS)
        self.assertTrue(os.path.isfile(path))
        with self.assertRaises(scorer.ResultsOverwriteRefused) as caught:
            scorer.assert_writable(path)
        self.assertIn("KNOWN_PAID_ARTIFACTS", caught.exception.reason)

    def test_it_carries_no_telemetry_so_detection_alone_would_miss_it(self):
        scorer = _scorer()
        path = os.path.join(scorer.REPO_ROOT, "eval", "v1", "results", "jev.json")
        document = _read(path)
        self.assertNotIn("telemetry", document)
        live, _ = scorer.existing_run_is_live(path)
        self.assertFalse(live, "content-only detection cannot see this one")

    def test_the_known_set_points_at_the_real_file_not_a_redirected_one(self):
        """Tests redirect RESULTS_DIR; the protected set must not follow."""
        scorer = _scorer()
        tmp = tempfile.mkdtemp(prefix="bia-paid-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with _fresh_run(tmp):
            for path in scorer.KNOWN_PAID_ARTIFACTS:
                self.assertFalse(path.startswith(tmp))


class ScoredValuesUnchangedTests(unittest.TestCase):
    """Telemetry may be added. Nothing scored may move."""

    SCORED_SUMMARY = (
        "cases",
        "crashed",
        "S-1_wrong_confident_numbers",
        "S-2_unsupported_causal_claims",
        "S-3_partial_period_as_full",
        "S-4_verification_leak",
        "oracle_runtime_cell_disagreement",
        "V-1_fact_accuracy",
        "V-2_evidence_yield_macro",
        "V-2_included_cases",
        "V-3_evidence_precision_macro",
        "V-3_included_cases",
        "V-4_must_not_claim_clean",
        "V-5_wasted_retrievals",
        "V-6_total_decision_calls",
        "V-6_total_retrievals",
        "investigated_kind_counts",
        "budget_exceeded",
        "verdict",
        "passed",
    )
    SCORED_CASE = (
        "case_id",
        "yield",
        "precision",
        "failures",
        "s4_leaks",
        "decision_calls",
        "retrievals",
        "admitted_count",
        "admitted_unique_count",
        "useful_admitted_count",
        "investigated_kinds",
        "investigated_kind_counts",
        "selector_deferred",
        "defer_selections",
        "downgraded_selections",
        "wasted_retrievals",
        "status",
        "finish_reason",
        "numbers",
        "window",
        "top_groups",
        "causal_claims",
        "must_not_claim",
        "crashed",
    )

    def _committed(self, selector):
        scorer = _scorer()
        return _read(
            os.path.join(scorer.REPO_ROOT, "eval", "v1", "results", "%s.json" % selector)
        )

    def _check(self, selector):
        tmp = tempfile.mkdtemp(prefix="bia-scored-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        _run_main(tmp, ["--selector", selector])
        fresh = _read(os.path.join(tmp, "%s.json" % selector))
        committed = self._committed(selector)

        for key in self.SCORED_SUMMARY:
            self.assertEqual(
                fresh["summary"][key],
                committed["summary"][key],
                "%s: summary.%s moved" % (selector, key),
            )
        self.assertEqual(len(fresh["cases"]), len(committed["cases"]))
        for got, want in zip(fresh["cases"], committed["cases"]):
            for key in self.SCORED_CASE:
                self.assertEqual(
                    got.get(key), want.get(key), "%s/%s: %s moved" % (selector, want["case_id"], key)
                )

    def test_heuristic_scores_are_identical(self):
        self._check("heuristic")

    def test_greedy_scores_are_identical(self):
        self._check("greedy")


if __name__ == "__main__":
    unittest.main()
