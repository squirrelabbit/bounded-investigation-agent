"""JEV adapter boundaries. Every test here uses a fake transport.

Nothing in this file opens a socket, reads an API key, or constructs a working
HttpTransport. eval/v1/CONTRACT.md §7-C locks live calls behind a cost approval
that has not been granted.
"""
from __future__ import annotations

import json
import unittest
from typing import Dict, List, Sequence, Tuple

from bia import controller as controller_mod
from bia.controller import investigate
from bia.decision import DecisionProvider
from bia.jev import (
    DEFER_OPTION_DESCRIPTION,
    JEV_MODEL,
    QUESTION_KEY,
    CallBudget,
    FakeTransport,
    HttpTransport,
    JevHttpError,
    JevNetworkLocked,
    JevSelector,
    JevTransportError,
    UNDOCUMENTED_REQUEST_KEYS,
)
from bia.types import DEFER, EvidenceCandidate

from . import support


class _Capture(DecisionProvider):
    """Defers, but keeps the exact (state, candidates) pair the Controller passed."""

    name = "capture"

    def __init__(self) -> None:
        self.calls: List[Tuple[Dict[str, object], List[EvidenceCandidate]]] = []

    def select_next_evidence(self, state, candidates):
        self.calls.append((state, list(candidates)))
        return DEFER


def first_round_inputs() -> Tuple[Dict[str, object], List[EvidenceCandidate]]:
    """Real Controller output: the snapshot and candidate set of round 1."""
    capture = _Capture()
    investigate(support.intent(), support.rows(), support.tickets(), capture)
    assert capture.calls, "the Controller did not reach a decision call"
    return capture.calls[0]


def answer_response(
    choice: str,
    probabilities: Dict[str, float] = None,
    cost: str = "0.0012",
    generation_id: str = "gen-001",
) -> Dict[str, object]:
    return {
        "model": JEV_MODEL,
        "answers": {
            QUESTION_KEY: {
                "type": "choice",
                "choice": choice,
                "probabilities": probabilities
                if probabilities is not None
                else {choice: 1.0},
            }
        },
        "usage": {"inputTokens": 120, "outputTokens": 3},
        "providerMetadata": {"gateway": {"cost": cost, "generationId": generation_id}},
    }


def error_response(message: str, type_: str = "invalid_request_error") -> Dict[str, object]:
    return {"error": {"message": message, "type": type_}}


def selector_with(responses: Sequence[object], maximum: int = 16):
    transport = FakeTransport(responses, repeat_last=True)
    return JevSelector(transport=transport, budget=CallBudget(maximum)), transport


def walk_keys(node: object) -> List[str]:
    out: List[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            out.append(str(key))
            out.extend(walk_keys(value))
    elif isinstance(node, (list, tuple)):
        for item in node:
            out.extend(walk_keys(item))
    return out


class ChoiceHandlingTests(unittest.TestCase):
    def test_an_offered_candidate_id_is_returned(self):
        state, candidates = first_round_inputs()
        wanted = candidates[0].candidate_id
        selector, transport = selector_with([answer_response(wanted)])
        self.assertEqual(selector.select_next_evidence(state, candidates), wanted)
        self.assertEqual(transport.call_count, 1)
        record = selector.records[-1]
        self.assertTrue(record.called)
        self.assertIsNone(record.failure_reason)
        self.assertEqual(record.choice, wanted)
        self.assertEqual(record.generation_id, "gen-001")
        self.assertEqual(record.usage, {"inputTokens": 120, "outputTokens": 3})
        self.assertAlmostEqual(selector.total_cost, 0.0012)
        self.assertEqual(selector.call_count, 1)

    def test_defer_is_returned_and_is_not_an_adapter_failure(self):
        state, candidates = first_round_inputs()
        selector, transport = selector_with([answer_response(DEFER)])
        self.assertEqual(selector.select_next_evidence(state, candidates), DEFER)
        self.assertEqual(transport.call_count, 1)
        record = selector.records[-1]
        self.assertIsNone(record.failure_reason)
        self.assertEqual(record.choice, DEFER)
        self.assertEqual(record.returned, DEFER)

    def test_a_choice_outside_the_offered_options_is_recorded_and_deferred(self):
        state, candidates = first_round_inputs()
        selector, transport = selector_with([answer_response("R1-C99")])
        self.assertEqual(selector.select_next_evidence(state, candidates), DEFER)
        self.assertEqual(transport.call_count, 1)
        record = selector.records[-1]
        self.assertEqual(record.failure_reason, "choice_not_offered")
        self.assertEqual(record.choice, "R1-C99")
        self.assertEqual(record.returned, DEFER)

    def test_a_free_text_choice_is_deferred(self):
        state, candidates = first_round_inputs()
        selector, _ = selector_with(
            [answer_response("SELECT * FROM tickets WHERE product='P-Beta'")]
        )
        self.assertEqual(selector.select_next_evidence(state, candidates), DEFER)
        self.assertEqual(selector.records[-1].failure_reason, "choice_not_offered")

    def test_probabilities_never_override_the_choice(self):
        """Option A is chosen while option B carries the higher probability."""
        state, candidates = first_round_inputs()
        self.assertGreaterEqual(len(candidates), 2)
        option_a = candidates[0].candidate_id
        option_b = candidates[1].candidate_id
        selector, _ = selector_with(
            [answer_response(option_a, {option_a: 0.11, option_b: 0.88, DEFER: 0.01})]
        )
        self.assertEqual(selector.select_next_evidence(state, candidates), option_a)
        record = selector.records[-1]
        self.assertEqual(record.probabilities[option_b], 0.88)
        self.assertEqual(record.returned, option_a)

    def test_a_low_probability_defer_choice_is_still_honoured(self):
        state, candidates = first_round_inputs()
        option_a = candidates[0].candidate_id
        selector, _ = selector_with(
            [answer_response(DEFER, {option_a: 0.97, DEFER: 0.03})]
        )
        self.assertEqual(selector.select_next_evidence(state, candidates), DEFER)


class MalformedResponseTests(unittest.TestCase):
    def setUp(self):
        self.state, self.candidates = first_round_inputs()

    def assert_defers(self, responses, expected_reason_prefix, expected_calls=1):
        selector, transport = selector_with(responses)
        self.assertEqual(
            selector.select_next_evidence(self.state, self.candidates), DEFER
        )
        self.assertEqual(transport.call_count, expected_calls)
        reason = selector.records[-1].failure_reason or ""
        self.assertTrue(
            reason.startswith(expected_reason_prefix),
            "reason %r does not start with %r" % (reason, expected_reason_prefix),
        )
        return selector, transport

    def test_transport_exception_does_not_escape(self):
        self.assert_defers([JevTransportError("boom")], "transport_error")

    def test_an_unexpected_exception_type_also_defers(self):
        self.assert_defers([RuntimeError("socket closed")], "transport_error:RuntimeError")

    def test_each_error_status_defers_without_a_retry(self):
        for status in (400, 401, 429, 500):
            with self.subTest(status=status):
                selector, transport = self.assert_defers(
                    [JevHttpError(status, error_response("nope"))],
                    "http_status:%d" % status,
                )
                # zero retries: one attempted call, one record, budget down by one
                self.assertEqual(transport.call_count, 1)
                self.assertEqual(len(selector.records), 1)
                self.assertEqual(selector.budget.used, 1)

    def test_an_error_shaped_body_defers(self):
        self.assert_defers([error_response("bad request")], "api_error:invalid_request_error")

    def test_an_unparseable_body_defers(self):
        self.assert_defers(["not a dict"], "unparseable_response")

    def test_a_body_without_answers_defers(self):
        self.assert_defers([{"model": JEV_MODEL, "usage": {}}], "missing_answers")

    def test_a_missing_question_key_defers(self):
        body = answer_response("DEFER")
        body["answers"] = {"some_other_question": body["answers"][QUESTION_KEY]}
        self.assert_defers([body], "missing_question_key")

    def test_a_missing_choice_defers_but_keeps_the_telemetry(self):
        body = answer_response(DEFER)
        del body["answers"][QUESTION_KEY]["choice"]
        body["answers"][QUESTION_KEY]["probabilities"] = {DEFER: 1.0}
        selector, _ = self.assert_defers([body], "missing_choice")
        self.assertEqual(selector.records[-1].probabilities, {DEFER: 1.0})
        self.assertEqual(selector.records[-1].cost, "0.0012")


class BudgetTests(unittest.TestCase):
    def test_the_third_decision_makes_no_call_when_the_cap_is_two(self):
        state, candidates = first_round_inputs()
        wanted = candidates[0].candidate_id
        selector, transport = selector_with([answer_response(wanted)], maximum=2)

        self.assertEqual(selector.select_next_evidence(state, candidates), wanted)
        self.assertEqual(selector.select_next_evidence(state, candidates), wanted)
        self.assertEqual(selector.select_next_evidence(state, candidates), DEFER)

        self.assertEqual(transport.call_count, 2)
        self.assertEqual(selector.budget.remaining, 0)
        self.assertEqual(selector.call_count, 2)
        last = selector.records[-1]
        self.assertFalse(last.called)
        self.assertEqual(last.failure_reason, "call_budget_exhausted")

    def test_a_failed_call_still_spends_budget(self):
        state, candidates = first_round_inputs()
        selector, transport = selector_with(
            [JevHttpError(429, None), answer_response(candidates[0].candidate_id)],
            maximum=1,
        )
        self.assertEqual(selector.select_next_evidence(state, candidates), DEFER)
        self.assertEqual(selector.select_next_evidence(state, candidates), DEFER)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(selector.records[-1].failure_reason, "call_budget_exhausted")

    def test_the_budget_is_shared_across_selectors(self):
        state, candidates = first_round_inputs()
        budget = CallBudget(1)
        first = JevSelector(
            transport=FakeTransport([answer_response(candidates[0].candidate_id)]),
            budget=budget,
        )
        second_transport = FakeTransport([answer_response(candidates[0].candidate_id)])
        second = JevSelector(transport=second_transport, budget=budget)
        self.assertEqual(
            first.select_next_evidence(state, candidates), candidates[0].candidate_id
        )
        self.assertEqual(second.select_next_evidence(state, candidates), DEFER)
        self.assertEqual(second_transport.call_count, 0)

    def test_no_candidates_costs_no_call(self):
        state, _ = first_round_inputs()
        selector, transport = selector_with([answer_response(DEFER)])
        self.assertEqual(selector.select_next_evidence(state, []), DEFER)
        self.assertEqual(transport.call_count, 0)
        self.assertEqual(selector.budget.used, 0)
        self.assertEqual(selector.records[-1].failure_reason, "no_candidates_offered")


class RequestShapeTests(unittest.TestCase):
    def setUp(self):
        self.state, self.candidates = first_round_inputs()
        self.selector, self.transport = selector_with(
            [answer_response(self.candidates[0].candidate_id)]
        )
        self.selector.select_next_evidence(self.state, self.candidates)
        self.request = self.transport.requests[0]

    def test_model_and_question_shape(self):
        self.assertEqual(self.request["model"], JEV_MODEL)
        questions = self.request["questions"]
        self.assertEqual(list(questions), [QUESTION_KEY])
        question = questions[QUESTION_KEY]
        self.assertEqual(question["type"], "choice")
        self.assertTrue(question["instructions"].strip())

    def test_criteria_are_exactly_the_offered_options(self):
        criteria = self.request["questions"][QUESTION_KEY]["criteria"]
        expected = {c.candidate_id for c in self.candidates} | {DEFER}
        self.assertEqual(set(criteria), expected)
        self.assertEqual(criteria[DEFER], DEFER_OPTION_DESCRIPTION)
        for candidate in self.candidates:
            description = criteria[candidate.candidate_id]
            self.assertIn(candidate.label, description)
            self.assertIn(candidate.server_reason, description)
            self.assertIn(candidate.kind, description)

    def test_no_undocumented_parameter_appears_anywhere_in_the_body(self):
        keys = {k.lower() for k in walk_keys(self.request)}
        for forbidden in UNDOCUMENTED_REQUEST_KEYS:
            self.assertNotIn(forbidden, keys)
        blob = json.dumps(self.request, ensure_ascii=False)
        for forbidden in UNDOCUMENTED_REQUEST_KEYS:
            self.assertNotIn('"%s"' % forbidden, blob)


class StatePayloadTests(unittest.TestCase):
    def setUp(self):
        self.state, self.candidates = first_round_inputs()

    def _sent_state(self, state=None, candidates=None) -> str:
        selector, transport = selector_with(
            [answer_response((candidates or self.candidates)[0].candidate_id)]
        )
        selector.select_next_evidence(
            state if state is not None else self.state,
            candidates or self.candidates,
        )
        sent = transport.requests[0]["state"]
        self.assertIsInstance(sent, str)
        return sent

    def test_no_ticket_id_and_no_ticket_text_is_sent(self):
        tickets = support.tickets()
        self.assertTrue(tickets)
        sent = self._sent_state()
        for ticket in tickets:
            self.assertNotIn(ticket.ticket_id, sent)
        self.assertNotIn(support.SUPPORTING_TEXT, sent)
        self.assertNotIn("found_ticket_ids", sent)
        self.assertNotIn("excerpt", sent)

    def test_the_payload_carries_the_facts_it_is_supposed_to(self):
        payload = json.loads(self._sent_state())
        metrics = self.state["metrics"]
        self.assertEqual(payload["delta"], metrics["delta"])
        self.assertEqual(payload["current_total"], metrics["current_total"])
        self.assertEqual(payload["baseline_total"], metrics["baseline_total"])
        self.assertEqual(
            payload["current_window"]["start"], self.state["current_window"]["start"]
        )
        self.assertTrue(payload["current_period_complete"])
        self.assertEqual(payload["investigated_candidate_ids"], [])
        self.assertEqual(payload["retrievals_left"], self.state["budget_left"]["retrievals"])
        self.assertTrue(payload["top_products"])

    def test_nothing_leaks_once_tickets_have_actually_been_admitted(self):
        """Round 1 has no findings, so a leak of found ids would be invisible
        there. This drives a second decision with evidence already in hand."""
        import datetime as _dt

        from bia.types import Ticket

        mixed: List[Ticket] = []
        for index in range(10):
            supporting = index < 3
            mixed.append(
                Ticket(
                    ticket_id="T-%04d" % (index + 1),
                    day=support.CURRENT.start + _dt.timedelta(days=index % support.CURRENT.days),
                    product=support.SPIKE_PRODUCT,
                    complaint_type=support.SPIKE_TYPE,
                    text=support.SUPPORTING_TEXT if supporting else support.UNSUPPORTING_TEXT,
                    source="web_form",
                )
            )
        transport = FakeTransport(
            [answer_response("R1-C1"), answer_response(DEFER)], repeat_last=False
        )
        selector = JevSelector(transport=transport, budget=CallBudget(16))
        state = investigate(support.intent(), support.rows(), mixed, selector).state

        self.assertEqual(state.decision_calls, 2)
        self.assertTrue(state.found_ticket_ids, "no evidence admitted; the leak test is vacuous")
        self.assertEqual(len(transport.requests), 2)
        second = transport.requests[1]["state"]
        for ticket in mixed:
            self.assertNotIn(ticket.ticket_id, second)
        self.assertNotIn(support.SUPPORTING_TEXT, second)
        payload = json.loads(second)
        self.assertEqual(payload["investigated_candidate_ids"], ["R1-C1"])
        self.assertGreater(payload["coverage_so_far"], 0.0)

    def test_the_same_snapshot_serializes_byte_identically(self):
        first = self._sent_state()
        second_state, second_candidates = first_round_inputs()
        second = self._sent_state(second_state, second_candidates)
        self.assertEqual(first, second)
        self.assertEqual(first, json.dumps(json.loads(first), sort_keys=True, ensure_ascii=False))


class NetworkLockTests(unittest.TestCase):
    def test_construction_without_the_flag_is_refused(self):
        with self.assertRaises(JevNetworkLocked):
            HttpTransport(api_key="not-a-real-key")

    def test_construction_with_an_empty_key_is_refused(self):
        with self.assertRaises(JevNetworkLocked):
            HttpTransport(api_key="", enable_network=True)
        with self.assertRaises(JevNetworkLocked):
            HttpTransport(api_key="   ", enable_network=True)
        with self.assertRaises(JevNetworkLocked):
            HttpTransport(api_key=None, enable_network=True)

    def test_a_truthy_non_true_flag_is_refused(self):
        with self.assertRaises(JevNetworkLocked):
            HttpTransport(api_key="not-a-real-key", enable_network="yes")

    def test_the_selector_has_no_network_default(self):
        with self.assertRaises(TypeError):
            JevSelector()  # type: ignore[call-arg]


class _LeakyJevSelector(JevSelector):
    """Simulates a regression in which the adapter stops validating `choice`.

    The adapter already refuses an unoffered id, so the Controller's own guard
    would never be reached through the real path. This subclass removes the
    adapter's check to show the second layer still holds: the Controller
    downgrades the value and records a violation.
    """

    def _interpret(self, body, options):
        choice = body["answers"][self.question_key]["choice"]
        self._record(options, called=True, choice=choice, returned=choice)
        return choice


class EndToEndTests(unittest.TestCase):
    def test_a_good_choice_runs_the_investigation(self):
        transport = FakeTransport([answer_response("R1-C1")])
        selector = JevSelector(transport=transport, budget=CallBudget(16))
        state = investigate(
            support.intent(), support.rows(), support.tickets(), selector
        ).state
        self.assertEqual(state.violations, [])
        self.assertEqual(state.retrievals, 1)
        self.assertTrue(state.found_ticket_ids)
        self.assertEqual(transport.call_count, 1)

    def test_a_bogus_choice_never_reaches_a_retrieval(self):
        transport = FakeTransport([answer_response("R1-C99")])
        selector = JevSelector(transport=transport, budget=CallBudget(16))
        state = investigate(
            support.intent(), support.rows(), support.tickets(), selector
        ).state
        self.assertEqual(state.finish_reason, controller_mod.FINISH_DEFERRED)
        self.assertEqual(state.retrievals, 0)
        self.assertEqual(state.found_ticket_ids, [])
        self.assertEqual(selector.records[-1].failure_reason, "choice_not_offered")

    def test_the_controller_downgrades_a_bogus_choice_that_slips_past_the_adapter(self):
        transport = FakeTransport([answer_response("R1-C99")])
        selector = _LeakyJevSelector(transport=transport, budget=CallBudget(16))
        state = investigate(
            support.intent(), support.rows(), support.tickets(), selector
        ).state
        self.assertEqual(state.finish_reason, controller_mod.FINISH_DEFERRED)
        self.assertEqual(state.retrievals, 0)
        self.assertEqual(state.found_ticket_ids, [])
        self.assertEqual(len(state.violations), 1)
        self.assertIn(controller_mod.VIOLATION_UNKNOWN_ID, state.violations[0])
        self.assertIn("R1-C99", state.violations[0])

    def test_a_dead_transport_leaves_the_run_intact(self):
        transport = FakeTransport([JevTransportError("connection reset")])
        selector = JevSelector(transport=transport, budget=CallBudget(16))
        state = investigate(
            support.intent(), support.rows(), support.tickets(), selector
        ).state
        self.assertEqual(state.finish_reason, controller_mod.FINISH_DEFERRED)
        self.assertEqual(state.violations, [])
        self.assertEqual(state.found_ticket_ids, [])


class ScorerRegistrationTests(unittest.TestCase):
    """The v1 scorer knows about jev and still cannot start a live run."""

    def _load_scorer(self):
        import os
        import sys

        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from eval.v1 import run_eval as scorer

        return scorer

    def test_jev_is_registered_and_builds_a_fake_transport_by_default(self):
        from unittest import mock

        scorer = self._load_scorer()
        self.assertIn("jev", scorer.SELECTORS)
        with mock.patch.dict("os.environ", {}, clear=False) as _:
            import os

            os.environ.pop("BIA_JEV_LIVE", None)
            os.environ.pop("AI_GATEWAY_API_KEY", None)
            selector = scorer.SELECTORS["jev"]()
        self.assertIsInstance(selector, JevSelector)
        self.assertIsInstance(selector.transport, FakeTransport)

    def test_asking_for_a_live_run_stops_the_scorer(self):
        """The placeholder below is not a key and never leaves this process:
        the factory raises before any transport exists."""
        from unittest import mock

        scorer = self._load_scorer()
        with mock.patch.dict(
            "os.environ",
            {"BIA_JEV_LIVE": "1", "AI_GATEWAY_API_KEY": "placeholder-not-a-key"},
        ):
            with self.assertRaises(SystemExit) as caught:
                scorer.SELECTORS["jev"]()
        self.assertIn("explicit cost approval", str(caught.exception))


if __name__ == "__main__":
    unittest.main()


class ProcessWideBudgetTest(unittest.TestCase):
    """The 16-call cap binds across the whole run, not per case.

    The scorer builds a fresh selector for every case; if each carried its own
    budget the contract's total would never bind and a run could spend 8x it.
    """

    def test_the_scorer_factory_shares_one_budget_across_selectors(self):
        import importlib
        import os
        import sys

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)
        scorer = importlib.import_module("eval.v1.run_eval")

        first = scorer.build_jev_selector()
        second = scorer.build_jev_selector()
        self.assertIs(first.budget, second.budget)
        self.assertIs(first.budget, scorer._JEV_PROCESS_BUDGET)

    def test_the_shared_budget_is_the_contract_maximum(self):
        import importlib
        import os
        import sys

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)
        scorer = importlib.import_module("eval.v1.run_eval")
        self.assertEqual(scorer._JEV_PROCESS_BUDGET.maximum, 16)
