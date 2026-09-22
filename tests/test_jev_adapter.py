"""JEV adapter boundaries. Every test here uses a fake transport.

Nothing in this file opens a socket, reads an API key, or constructs a working
HttpTransport. eval/v1/CONTRACT.md §7-D locks live calls on the TypeSafe direct
path behind an approval that has not been granted, and explicitly states that
the cancelled Vercel path's approval does not transfer.
"""
from __future__ import annotations

import ast
import dataclasses
import json
import os
import unittest
from typing import Dict, List, Sequence, Tuple

from bia import controller as controller_mod
from bia import jev as jev_mod
from bia.controller import investigate
from bia.decision import DecisionProvider
from bia.jev import (
    COST_BASIS,
    DEFER_OPTION_DESCRIPTION,
    DOCUMENTED_REQUEST_KEYS,
    INPUT_USD_PER_MILLION_TOKENS,
    JEV_ENDPOINT,
    JEV_MODEL,
    JEV_MODEL_ALIASES,
    OUTPUT_USD_PER_MILLION_TOKENS,
    QUESTION_KEY,
    REQUEST_ID_HEADER,
    CallBudget,
    FakeTransport,
    HttpTransport,
    JevHttpError,
    JevNetworkLocked,
    JevResponse,
    JevSelector,
    JevTransportError,
    REASON_FIRST_RESPONSE_SHAPE,
    RunGuard,
    UNDOCUMENTED_REQUEST_KEYS,
    estimate_cost_usd,
    first_response_shape_error,
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
    confidence: float = 0.78,
    input_tokens: int = 392,
    output_tokens: int = 65,
    model: str = JEV_MODEL,
) -> Dict[str, object]:
    """The §7-D response shape: snake_case usage, no cost, no request id."""
    return {
        "model": model,
        "answers": {
            QUESTION_KEY: {
                "type": "choice",
                "choice": choice,
                "confidence": confidence,
                "probabilities": probabilities
                if probabilities is not None
                else {choice: 1.0},
            }
        },
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def with_request_id(body: Dict[str, object], request_id: str = "req-abc-123"):
    """§7-D: the request identifier is a header, and header names are not
    case-sensitive, so the fixture deliberately uses mixed case."""
    return JevResponse(body=body, headers={"X-TypeSafe-Request-Id": request_id})


def selector_with(responses: Sequence[object], maximum: int = 16, repeat: bool = True):
    transport = FakeTransport(responses, repeat_last=repeat)
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


class ModelPinTests(unittest.TestCase):
    """A moving alias would let the same test paper be answered by a different
    model, which voids the comparison (§7-D 모델 버전 고정)."""

    def test_the_constant_is_the_pinned_version(self):
        self.assertEqual(JEV_MODEL, "jev-1.13.0")
        self.assertNotIn(JEV_MODEL, JEV_MODEL_ALIASES)

    def test_no_alias_string_appears_in_any_request_body(self):
        state, candidates = first_round_inputs()
        bodies: List[str] = []

        selector, transport = selector_with([answer_response(candidates[0].candidate_id)])
        selector.select_next_evidence(state, candidates)
        selector.select_next_evidence(state, candidates[:1])
        selector.select_next_evidence(state, candidates)
        self.assertEqual(len(transport.requests), 3)
        for request in transport.requests:
            bodies.append(json.dumps(request, ensure_ascii=False))

        self.assertTrue(bodies)
        for blob in bodies:
            for alias in JEV_MODEL_ALIASES:
                self.assertNotIn(alias, blob)
            self.assertIn('"model": "jev-1.13.0"', blob)

    def test_constructing_a_selector_on_an_alias_is_refused(self):
        transport = FakeTransport([answer_response(DEFER)])
        for alias in JEV_MODEL_ALIASES:
            with self.subTest(alias=alias):
                with self.assertRaises(ValueError):
                    JevSelector(transport=transport, model=alias)

    def test_the_answering_model_version_is_recorded_per_call(self):
        state, candidates = first_round_inputs()
        wanted = candidates[0].candidate_id
        # a version we did not pin must still be recorded, not silently dropped
        selector, _ = selector_with([answer_response(wanted, model="jev-1.12.0")])
        self.assertEqual(selector.select_next_evidence(state, candidates), wanted)
        self.assertEqual(selector.records[-1].response_model, "jev-1.12.0")
        self.assertEqual(selector.telemetry()["model_requested"], JEV_MODEL)
        self.assertEqual(selector.telemetry()["models_answered"], ["jev-1.12.0"])


class EndpointTests(unittest.TestCase):
    """Exactly one live path may exist in the code."""

    def test_the_endpoint_constant_is_the_direct_api(self):
        self.assertEqual(JEV_ENDPOINT, "https://api.typesafe.ai/v1/systemone")

    def test_the_module_holds_no_other_url(self):
        """AST-based, not grep: prose in the docstring is allowed to name the
        cancelled path, but no second URL literal may survive."""
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bia", "jev.py"
        )
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)

        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                body = getattr(node, "body", None) or []
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    docstrings.add(id(body[0].value))

        urls = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
                and "://" in node.value
            ):
                urls.add(node.value)
        self.assertEqual(urls, {JEV_ENDPOINT})

    def test_the_transport_default_endpoint_is_the_direct_api(self):
        import inspect

        signature = inspect.signature(HttpTransport.__init__)
        self.assertEqual(signature.parameters["endpoint"].default, JEV_ENDPOINT)


class ChoiceHandlingTests(unittest.TestCase):
    def test_an_offered_candidate_id_is_returned(self):
        state, candidates = first_round_inputs()
        wanted = candidates[0].candidate_id
        selector, transport = selector_with(
            [with_request_id(answer_response(wanted))]
        )
        self.assertEqual(selector.select_next_evidence(state, candidates), wanted)
        self.assertEqual(transport.call_count, 1)
        record = selector.records[-1]
        self.assertTrue(record.called)
        self.assertIsNone(record.failure_reason)
        self.assertEqual(record.choice, wanted)
        self.assertEqual(record.response_model, JEV_MODEL)
        self.assertEqual(record.request_id, "req-abc-123")
        self.assertEqual(record.usage, {"input_tokens": 392, "output_tokens": 65})
        self.assertEqual(record.input_tokens, 392)
        self.assertEqual(record.output_tokens, 65)
        self.assertEqual(record.confidence, 0.78)
        self.assertEqual(selector.call_count, 1)

    def test_a_missing_request_id_header_is_simply_absent(self):
        """§7-D says the header may be absent; that is not a failure."""
        state, candidates = first_round_inputs()
        wanted = candidates[0].candidate_id
        selector, _ = selector_with([answer_response(wanted)])
        self.assertEqual(selector.select_next_evidence(state, candidates), wanted)
        record = selector.records[-1]
        self.assertIsNone(record.request_id)
        self.assertIsNone(record.failure_reason)

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


class ConfidenceAndProbabilityAreInertTests(unittest.TestCase):
    """§7-D: confidence is treated exactly like probabilities — recorded only."""

    def test_a_low_confidence_choice_beaten_on_probability_is_still_returned(self):
        state, candidates = first_round_inputs()
        self.assertGreaterEqual(len(candidates), 2)
        option_a = candidates[0].candidate_id
        option_b = candidates[1].candidate_id
        selector, _ = selector_with(
            [
                answer_response(
                    option_a,
                    probabilities={option_a: 0.05, option_b: 0.9, DEFER: 0.05},
                    confidence=0.10,
                )
            ]
        )
        self.assertEqual(selector.select_next_evidence(state, candidates), option_a)
        record = selector.records[-1]
        self.assertEqual(record.returned, option_a)
        self.assertEqual(record.confidence, 0.10)
        self.assertEqual(record.probabilities[option_b], 0.9)
        self.assertIsNone(record.failure_reason)

    def test_probabilities_never_override_the_choice(self):
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
            [answer_response(DEFER, {option_a: 0.97, DEFER: 0.03}, confidence=0.01)]
        )
        self.assertEqual(selector.select_next_evidence(state, candidates), DEFER)
        self.assertIsNone(selector.records[-1].failure_reason)

    def test_a_missing_or_nonsense_confidence_changes_nothing(self):
        state, candidates = first_round_inputs()
        wanted = candidates[0].candidate_id
        body = answer_response(wanted)
        body["answers"][QUESTION_KEY]["confidence"] = "very sure"
        selector, _ = selector_with([body])
        self.assertEqual(selector.select_next_evidence(state, candidates), wanted)
        self.assertIsNone(selector.records[-1].confidence)
        self.assertIsNone(selector.records[-1].failure_reason)


class EstimatedCostTests(unittest.TestCase):
    """§7-D 비용 기록: the body has no cost, so ours is an estimate and says so."""

    def test_the_estimate_comes_from_input_tokens_and_the_named_rate(self):
        state, candidates = first_round_inputs()
        wanted = candidates[0].candidate_id
        selector, _ = selector_with(
            [answer_response(wanted, input_tokens=1_000_000, output_tokens=500_000)]
        )
        selector.select_next_evidence(state, candidates)
        record = selector.records[-1]
        self.assertAlmostEqual(
            record.estimated_cost_usd, INPUT_USD_PER_MILLION_TOKENS, places=12
        )
        self.assertEqual(OUTPUT_USD_PER_MILLION_TOKENS, 0.0)
        self.assertAlmostEqual(
            selector.total_estimated_cost_usd, INPUT_USD_PER_MILLION_TOKENS, places=12
        )

    def test_the_helper_is_output_insensitive_and_none_safe(self):
        self.assertIsNone(estimate_cost_usd(None, 10))
        self.assertAlmostEqual(estimate_cost_usd(392, 65), 392 * 0.042 / 1e6, places=15)
        self.assertEqual(estimate_cost_usd(392, 65), estimate_cost_usd(392, 999_999))

    def test_nothing_is_named_so_it_could_be_read_as_a_billed_figure(self):
        state, candidates = first_round_inputs()
        selector, _ = selector_with([answer_response(candidates[0].candidate_id)])
        selector.select_next_evidence(state, candidates)
        record = selector.records[-1]
        self.assertFalse(hasattr(record, "cost"))
        self.assertNotIn("cost", record.as_dict())
        self.assertIn("estimated_cost_usd", record.as_dict())
        telemetry = selector.telemetry()
        self.assertNotIn("total_cost", telemetry)
        self.assertIn("total_estimated_cost_usd", telemetry)
        self.assertTrue(telemetry["cost_is_estimate"])
        self.assertIn("estimate", COST_BASIS)
        self.assertIn("no cost field", COST_BASIS)

    def test_a_cost_field_invented_by_the_response_is_ignored(self):
        """No cost field is documented; if one appeared we must not adopt it."""
        state, candidates = first_round_inputs()
        wanted = candidates[0].candidate_id
        body = answer_response(wanted, input_tokens=392, output_tokens=65)
        body["cost"] = "999.99"
        body["usage"]["cost"] = "999.99"
        selector, _ = selector_with([body])
        selector.select_next_evidence(state, candidates)
        record = selector.records[-1]
        self.assertAlmostEqual(
            record.estimated_cost_usd, 392 * 0.042 / 1e6, places=15
        )
        self.assertNotIn("999.99", json.dumps(record.as_dict()["estimated_cost_usd"]))

    def test_a_response_without_usage_records_no_estimate(self):
        """A LATER call without usage: the estimate is simply absent.

        The FIRST completed call of a run is a different matter — §7-E verifies
        `usage.input_tokens` there and halts the run if it is missing. That is
        covered in FirstResponseShapeTests, so this selector starts with the
        first-response check already spent.
        """
        state, candidates = first_round_inputs()
        wanted = candidates[0].candidate_id
        body = answer_response(wanted)
        del body["usage"]
        selector, _ = selector_with([body])
        selector.guard.first_response_checked = True
        self.assertEqual(selector.select_next_evidence(state, candidates), wanted)
        self.assertFalse(selector.guard.halted)
        record = selector.records[-1]
        self.assertIsNone(record.input_tokens)
        self.assertIsNone(record.estimated_cost_usd)
        self.assertEqual(selector.total_estimated_cost_usd, 0.0)


class ZeroRetryTests(unittest.TestCase):
    """One send per decision, on every documented status and on an exception."""

    def setUp(self):
        self.state, self.candidates = first_round_inputs()

    def assert_defers_after_exactly_one_send(self, item, expected_reason_prefix):
        selector, transport = selector_with([item])
        self.assertEqual(
            selector.select_next_evidence(self.state, self.candidates), DEFER
        )
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(len(selector.records), 1)
        self.assertEqual(selector.budget.used, 1)
        reason = selector.records[-1].failure_reason or ""
        self.assertTrue(
            reason.startswith(expected_reason_prefix),
            "reason %r does not start with %r" % (reason, expected_reason_prefix),
        )
        return selector, transport

    def test_every_documented_error_status_sends_exactly_once(self):
        # 401 auth, 422 validation, 429 rate limit, 529 overloaded (§7-D),
        # plus an undocumented 500 which must be treated identically.
        for status in (401, 422, 429, 529, 500):
            with self.subTest(status=status):
                self.assert_defers_after_exactly_one_send(
                    JevHttpError(status, {"whatever": "undocumented"}),
                    "http_status:%d" % status,
                )

    def test_an_error_body_of_an_unknown_shape_is_never_field_accessed(self):
        """The error schema is undocumented (§7-D): nothing may be read out of
        it, and no shape may make the adapter raise."""
        for body in (None, "plain text", [], {"error": "a string, not an object"}, 17):
            with self.subTest(body=type(body).__name__):
                self.assert_defers_after_exactly_one_send(
                    JevHttpError(422, body), "http_status:422"
                )

    def test_a_connection_exception_sends_exactly_once(self):
        self.assert_defers_after_exactly_one_send(
            ConnectionResetError("connection reset by peer"),
            "transport_error:ConnectionResetError",
        )

    def test_a_transport_error_sends_exactly_once(self):
        self.assert_defers_after_exactly_one_send(
            JevTransportError("boom"), "transport_error"
        )

    def test_a_malformed_body_sends_exactly_once(self):
        self.assert_defers_after_exactly_one_send("not a dict", "unparseable_response")


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

    def test_an_unexpected_exception_type_also_defers(self):
        self.assert_defers([RuntimeError("socket closed")], "transport_error:RuntimeError")

    def test_a_body_without_answers_defers(self):
        self.assert_defers(
            [{"model": JEV_MODEL, "usage": {"input_tokens": 5, "output_tokens": 0}}],
            "missing_answers",
        )

    def test_an_error_shaped_two_hundred_body_defers_without_guessing_its_schema(self):
        selector, _ = self.assert_defers(
            [{"error": {"message": "bad request"}}], "missing_answers"
        )
        self.assertEqual(selector.records[-1].returned, DEFER)

    def test_a_missing_question_key_defers(self):
        body = answer_response("DEFER")
        body["answers"] = {"some_other_question": body["answers"][QUESTION_KEY]}
        self.assert_defers([body], "missing_question_key")

    def test_a_missing_choice_defers_but_keeps_the_telemetry(self):
        body = answer_response(DEFER)
        del body["answers"][QUESTION_KEY]["choice"]
        body["answers"][QUESTION_KEY]["probabilities"] = {DEFER: 1.0}
        selector, _ = self.assert_defers([with_request_id(body)], "missing_choice")
        record = selector.records[-1]
        self.assertEqual(record.probabilities, {DEFER: 1.0})
        self.assertEqual(record.confidence, 0.78)
        self.assertEqual(record.request_id, "req-abc-123")
        self.assertAlmostEqual(record.estimated_cost_usd, 392 * 0.042 / 1e6, places=15)

    def test_a_non_dict_answer_entry_defers(self):
        body = answer_response(DEFER)
        body["answers"][QUESTION_KEY] = "DEFER"
        self.assert_defers([body], "missing_question_key")

    def test_a_transport_that_returns_a_bare_body_still_fails_closed(self):
        """Defensive: a transport ignoring the JevResponse contract must not
        crash the Controller."""
        state, candidates = self.state, self.candidates

        class _BareTransport(FakeTransport):
            def send(self, request):
                self.requests.append(request)
                return answer_response(candidates[0].candidate_id)  # not a JevResponse

        transport = _BareTransport([])
        selector = JevSelector(transport=transport, budget=CallBudget(16))
        result = selector.select_next_evidence(state, candidates)
        self.assertEqual(result, candidates[0].candidate_id)
        self.assertEqual(transport.call_count, 1)


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

    def test_the_default_budget_is_sixteen(self):
        self.assertEqual(CallBudget().maximum, 16)

    def test_a_failed_call_still_spends_budget(self):
        state, candidates = first_round_inputs()
        selector, transport = selector_with(
            [JevHttpError(429, None), answer_response(candidates[0].candidate_id)],
            maximum=1,
        )
        self.assertEqual(selector.select_next_evidence(state, candidates), DEFER)
        self.assertEqual(selector.budget.used, 1)
        self.assertEqual(selector.budget.remaining, 0)
        # The 429 spent the budget AND halted the run (§7-E), so the second
        # decision is refused by the halt, which is checked first precisely so a
        # halted run cannot consume a call it will never send.
        self.assertEqual(selector.select_next_evidence(state, candidates), DEFER)
        self.assertEqual(transport.call_count, 1)
        self.assertTrue(
            selector.records[-1].failure_reason.startswith("skipped_run_halted")
        )

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

    def test_more_than_the_documented_option_ceiling_costs_no_call(self):
        """§7-D: a choice question may offer at most 255 options."""
        state, candidates = first_round_inputs()
        template = candidates[0]
        many = [
            dataclasses.replace(template, candidate_id="X-%03d" % index)
            for index in range(255)
        ]
        selector, transport = selector_with([answer_response(DEFER)])
        self.assertEqual(selector.select_next_evidence(state, many), DEFER)
        self.assertEqual(transport.call_count, 0)
        self.assertEqual(selector.budget.used, 0)
        self.assertEqual(selector.records[-1].failure_reason, "too_many_options")


class RequestShapeTests(unittest.TestCase):
    # Every key the documented request may contain, outside `criteria`, whose
    # keys are the option ids and are checked separately.
    ALLOWED_STRUCTURAL_KEYS = {
        "state",
        "model",
        "questions",
        QUESTION_KEY,
        "type",
        "instructions",
        "criteria",
    }

    def setUp(self):
        self.state, self.candidates = first_round_inputs()
        self.selector, self.transport = selector_with(
            [answer_response(self.candidates[0].candidate_id)]
        )
        self.selector.select_next_evidence(self.state, self.candidates)
        self.request = self.transport.requests[0]

    def test_exactly_the_three_documented_top_level_keys(self):
        self.assertEqual(sorted(self.request), sorted(DOCUMENTED_REQUEST_KEYS))
        self.assertEqual(sorted(DOCUMENTED_REQUEST_KEYS), ["model", "questions", "state"])
        for key in DOCUMENTED_REQUEST_KEYS:
            self.assertIsNotNone(self.request[key])

    def test_model_and_question_shape(self):
        self.assertEqual(self.request["model"], JEV_MODEL)
        questions = self.request["questions"]
        self.assertEqual(list(questions), [QUESTION_KEY])
        question = questions[QUESTION_KEY]
        self.assertEqual(question["type"], "choice")
        self.assertIsInstance(question["instructions"], dict)
        self.assertEqual(
            sorted(question["instructions"]), ["focus", "not_your_job", "question"]
        )
        for value in question["instructions"].values():
            self.assertTrue(value.strip())

    def test_a_choice_question_carries_no_options_field(self):
        """The options ARE the criteria keys (§7-D)."""
        question = self.request["questions"][QUESTION_KEY]
        self.assertEqual(sorted(question), ["criteria", "instructions", "type"])
        self.assertNotIn("options", walk_keys(self.request))

    def test_criteria_are_exactly_the_offered_options(self):
        criteria = self.request["questions"][QUESTION_KEY]["criteria"]
        expected = {c.candidate_id for c in self.candidates} | {DEFER}
        self.assertEqual(set(criteria), expected)
        self.assertLessEqual(len(criteria), 255)
        self.assertEqual(criteria[DEFER], DEFER_OPTION_DESCRIPTION)
        for candidate in self.candidates:
            description = criteria[candidate.candidate_id]
            self.assertIsInstance(description, dict)
            self.assertIn(candidate.label, description["what"])
            self.assertIn(candidate.server_reason, description["what"])

    def test_every_option_uses_the_same_field_names(self):
        """Uniform fields are what let the model compare options directly."""
        criteria = self.request["questions"][QUESTION_KEY]["criteria"]
        shapes = {tuple(sorted(value)) for value in criteria.values()}
        self.assertEqual(shapes, {("not_for", "what")})

    def test_no_option_description_is_empty_or_null(self):
        """`null` is only defensible when the option name speaks for itself;
        an opaque id like R1-C1 never does."""
        criteria = self.request["questions"][QUESTION_KEY]["criteria"]
        for option, value in criteria.items():
            self.assertIsNotNone(value, option)
            for field, text in value.items():
                self.assertTrue(text and text.strip(), "%s.%s" % (option, field))

    def test_a_broad_option_says_what_it_is_not_for(self):
        """A narrow cell shares the list with the product and type containing it."""
        criteria = self.request["questions"][QUESTION_KEY]["criteria"]
        broad = [c for c in self.candidates if c.kind in ("product", "complaint_type")]
        self.assertTrue(broad, "fixture must offer at least one broad candidate")
        for candidate in broad:
            not_for = criteria[candidate.candidate_id]["not_for"]
            self.assertIn("조합", not_for)
            self.assertIn("증가하지 않은", not_for)

    def test_option_order_is_fixed_narrow_first_escape_last(self):
        """Sibling options are asked in the order they appear, so order is part
        of the question rather than presentation."""
        criteria = self.request["questions"][QUESTION_KEY]["criteria"]
        order = list(criteria)
        self.assertEqual(order[-1], DEFER)
        kinds = {c.candidate_id: c.kind for c in self.candidates}
        rank = {"cell": 0, "product": 1, "complaint_type": 2}
        seen = [rank[kinds[option]] for option in order if option in kinds]
        self.assertEqual(seen, sorted(seen), "narrow options must come first")

    def test_no_undocumented_key_appears_anywhere_in_the_body(self):
        criteria_keys = set(self.request["questions"][QUESTION_KEY]["criteria"])
        # Our own description/instruction field names are ours to choose and are
        # not API parameters; the point of this test is undocumented API keys.
        ours = {"what", "not_for", "question", "focus", "not_your_job"}
        structural = {
            k for k in walk_keys(self.request) if k not in criteria_keys and k not in ours
        }
        self.assertEqual(structural, self.ALLOWED_STRUCTURAL_KEYS)

        lowered = {k.lower() for k in walk_keys(self.request)}
        for forbidden in UNDOCUMENTED_REQUEST_KEYS:
            self.assertNotIn(forbidden, lowered)
        blob = json.dumps(self.request, ensure_ascii=False)
        for forbidden in UNDOCUMENTED_REQUEST_KEYS:
            self.assertNotIn('"%s"' % forbidden, blob)

    def test_the_reproducibility_controls_are_the_ones_the_docs_omit(self):
        self.assertEqual(
            sorted(UNDOCUMENTED_REQUEST_KEYS),
            ["max_tokens", "seed", "temperature", "top_p"],
        )


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

    def test_the_twenty_preregistered_keys_are_exactly_what_is_sent(self):
        payload = json.loads(self._sent_state())
        self.assertEqual(
            sorted(payload),
            [
                "baseline_period_complete",
                "baseline_period_completeness",
                "baseline_total",
                "baseline_window",
                "comparability_mode",
                "coverage_so_far",
                "current_period_complete",
                "current_period_completeness",
                "current_total",
                "current_window",
                "decision_calls_left",
                "decision_calls_used",
                "delta",
                "evidence_sufficient",
                "investigated_candidate_ids",
                "pct_change",
                "retrievals_left",
                "retrievals_used",
                "top_complaint_types",
                "top_products",
            ],
        )
        self.assertEqual(len(payload), 20)

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

    def test_the_transport_never_reads_the_environment(self):
        """AST-based: no os.environ / getenv reference exists in the module, so
        a key can only arrive as an argument from the call site."""
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bia", "jev.py"
        )
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)
        names = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        } | {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        for forbidden in ("environ", "getenv", "os"):
            self.assertNotIn(forbidden, names)

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

    def _interpret(self, response, options):
        body = response.body if isinstance(response, jev_mod.JevResponse) else response
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
        import sys

        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from eval.v1 import run_eval as scorer

        return scorer

    def test_jev_is_registered_and_builds_a_fake_transport_by_default(self):
        from unittest import mock

        scorer = self._load_scorer()
        self.assertIn("jev", scorer.SELECTORS)
        with mock.patch.dict("os.environ", {}, clear=False):
            os.environ.pop("BIA_JEV_LIVE", None)
            os.environ.pop("TYPESAFE_API_KEY", None)
            selector = scorer.SELECTORS["jev"]()
        self.assertIsInstance(selector, JevSelector)
        self.assertIsInstance(selector.transport, FakeTransport)

    def test_a_live_request_with_a_key_builds_the_network_transport(self):
        """Unlocked on the account owner's approval. Constructing the transport
        opens no socket; the placeholder below is not a key and must not surface
        anywhere the selector can print or serialize."""
        from unittest import mock

        scorer = self._load_scorer()
        with mock.patch.dict(
            "os.environ",
            {"BIA_JEV_LIVE": "1", "TYPESAFE_API_KEY": "placeholder-not-a-key"},
        ):
            selector = scorer.SELECTORS["jev"]()
        self.assertIsInstance(selector.transport, HttpTransport)
        for rendered in (repr(selector.transport), repr(selector), str(selector.telemetry())):
            self.assertNotIn("placeholder-not-a-key", rendered)

    def test_a_live_request_without_a_key_refuses_loudly(self):
        """Falling back to a fake here would write a `jev` result that never
        called the model — worse than stopping."""
        from unittest import mock

        scorer = self._load_scorer()
        with mock.patch.dict("os.environ", {"BIA_JEV_LIVE": "1"}):
            os.environ.pop("TYPESAFE_API_KEY", None)
            with self.assertRaises(SystemExit) as caught:
                scorer.SELECTORS["jev"]()
        self.assertIn("TYPESAFE_API_KEY", str(caught.exception))

    def test_a_key_without_the_live_flag_stays_offline(self):
        from unittest import mock

        scorer = self._load_scorer()
        with mock.patch.dict("os.environ", {"TYPESAFE_API_KEY": "placeholder-not-a-key"}):
            os.environ.pop("BIA_JEV_LIVE", None)
            selector = scorer.SELECTORS["jev"]()
        self.assertIsInstance(selector.transport, FakeTransport)

    def test_the_old_gateway_variable_no_longer_unlocks_anything(self):
        from unittest import mock

        scorer = self._load_scorer()
        with mock.patch.dict(
            "os.environ",
            {"BIA_JEV_LIVE": "1", "AI_GATEWAY_API_KEY": "placeholder-not-a-key"},
        ):
            os.environ.pop("TYPESAFE_API_KEY", None)
            with self.assertRaises(SystemExit) as caught:
                scorer.SELECTORS["jev"]()
        self.assertIn("TYPESAFE_API_KEY", str(caught.exception))
        self.assertNotIn("placeholder-not-a-key", str(caught.exception))


class ProcessWideBudgetTest(unittest.TestCase):
    """The 16-call cap binds across the whole run, not per case.

    The scorer builds a fresh selector for every case; if each carried its own
    budget the contract's total would never bind and a run could spend 8x it.
    """

    def _scorer(self):
        import importlib
        import sys

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)
        return importlib.import_module("eval.v1.run_eval")

    def test_the_scorer_factory_shares_one_budget_across_selectors(self):
        scorer = self._scorer()
        first = scorer.build_jev_selector()
        second = scorer.build_jev_selector()
        self.assertIs(first.budget, second.budget)
        self.assertIs(first.budget, scorer._JEV_PROCESS_BUDGET)

    def test_the_shared_budget_is_the_contract_maximum(self):
        scorer = self._scorer()
        self.assertEqual(scorer._JEV_PROCESS_BUDGET.maximum, 16)


class RunHaltTests(unittest.TestCase):
    """§7-E: any call failure stops the whole run.

    Both invariants are asserted together throughout: the provider still returns
    DEFER and raises nothing, AND the run-wide flag goes up.
    """

    def setUp(self):
        self.state, self.candidates = first_round_inputs()

    def assert_halts_after_one_send(self, item, expected_reason_prefix):
        selector, transport = selector_with([item])
        self.assertFalse(selector.guard.halted, "guard must start down")
        self.assertEqual(
            selector.select_next_evidence(self.state, self.candidates), DEFER
        )
        self.assertEqual(transport.call_count, 1, "exactly one send, never a retry")
        self.assertTrue(selector.guard.halted, "the run must be halted")
        reason = selector.guard.halt_reason
        self.assertTrue(
            reason.startswith(expected_reason_prefix),
            "halt_reason %r does not start with %r" % (reason, expected_reason_prefix),
        )
        self.assertEqual(selector.records[-1].failure_reason, reason)
        return selector, transport

    def test_a_well_formed_first_response_does_not_halt_and_the_run_continues(self):
        wanted = self.candidates[0].candidate_id
        selector, transport = selector_with([answer_response(wanted)])
        self.assertEqual(
            selector.select_next_evidence(self.state, self.candidates), wanted
        )
        self.assertFalse(selector.guard.halted)
        self.assertEqual(selector.guard.halt_reason, "")
        self.assertTrue(selector.guard.first_response_checked)
        # and the run really does continue: a second decision still calls out
        self.assertEqual(
            selector.select_next_evidence(self.state, self.candidates), wanted
        )
        self.assertEqual(transport.call_count, 2)
        self.assertFalse(selector.guard.halted)
        self.assertFalse(selector.telemetry()["halted"])

    def test_a_transport_exception_halts(self):
        self.assert_halts_after_one_send(
            ConnectionResetError("connection reset by peer"),
            "transport_error:ConnectionResetError",
        )

    def test_a_timeout_halts(self):
        self.assert_halts_after_one_send(
            TimeoutError("timed out"), "transport_error:TimeoutError"
        )

    def test_every_non_200_status_halts(self):
        for status in (401, 422, 429, 529, 500):
            with self.subTest(status=status):
                self.assert_halts_after_one_send(
                    JevHttpError(status, {"undocumented": "shape"}),
                    "http_status:%d" % status,
                )

    def test_an_unparseable_body_halts(self):
        self.assert_halts_after_one_send("not a json object", "unparseable_response")

    def test_a_missing_answers_map_halts(self):
        self.assert_halts_after_one_send(
            {"model": JEV_MODEL, "usage": {"input_tokens": 5, "output_tokens": 0}},
            "missing_answers",
        )

    def test_a_missing_question_key_halts(self):
        body = answer_response(self.candidates[0].candidate_id)
        body["answers"] = {"another_question": body["answers"][QUESTION_KEY]}
        self.assert_halts_after_one_send(body, "missing_question_key")

    def test_an_answer_without_a_choice_halts(self):
        body = answer_response(self.candidates[0].candidate_id)
        del body["answers"][QUESTION_KEY]["choice"]
        self.assert_halts_after_one_send(body, "missing_choice")

    def test_a_choice_outside_the_offered_options_halts(self):
        selector, _ = self.assert_halts_after_one_send(
            answer_response("R1-C99"), "choice_not_offered"
        )
        self.assertEqual(selector.records[-1].choice, "R1-C99")
        self.assertEqual(selector.records[-1].returned, DEFER)

    def test_budget_exhaustion_is_not_a_halt(self):
        """A spent budget says nothing about whether the endpoint works, so it
        must not be confused with a failure that stops the run."""
        wanted = self.candidates[0].candidate_id
        selector, transport = selector_with([answer_response(wanted)], maximum=1)
        self.assertEqual(
            selector.select_next_evidence(self.state, self.candidates), wanted
        )
        self.assertEqual(
            selector.select_next_evidence(self.state, self.candidates), DEFER
        )
        self.assertEqual(selector.records[-1].failure_reason, "call_budget_exhausted")
        self.assertFalse(selector.guard.halted)

    def test_an_empty_candidate_list_is_not_a_halt(self):
        selector, transport = selector_with([answer_response(DEFER)])
        self.assertEqual(selector.select_next_evidence(self.state, []), DEFER)
        self.assertEqual(transport.call_count, 0)
        self.assertFalse(selector.guard.halted)


class HaltStopsFurtherCallsTests(unittest.TestCase):
    """Once halted: no send, no budget, and the skip is written down."""

    def test_after_a_halt_no_call_is_made_and_no_budget_is_spent(self):
        state, candidates = first_round_inputs()
        selector, transport = selector_with(
            [JevHttpError(529, None), answer_response(candidates[0].candidate_id)],
            maximum=16,
        )
        self.assertEqual(selector.select_next_evidence(state, candidates), DEFER)
        self.assertTrue(selector.guard.halted)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(selector.budget.used, 1)
        self.assertEqual(selector.budget.remaining, 15)

        for _ in range(3):
            self.assertEqual(selector.select_next_evidence(state, candidates), DEFER)

        self.assertEqual(transport.call_count, 1, "a halted run must not call again")
        self.assertEqual(selector.budget.used, 1, "a halted run must not spend budget")
        self.assertEqual(selector.budget.remaining, 15)
        self.assertEqual(selector.call_count, 1)
        skipped = selector.records[-1]
        self.assertFalse(skipped.called)
        self.assertEqual(skipped.returned, DEFER)
        self.assertTrue(skipped.failure_reason.startswith("skipped_run_halted"))
        self.assertIn("http_status:529", skipped.failure_reason)

    def test_the_first_halt_reason_is_the_one_kept(self):
        guard = RunGuard()
        guard.halt("http_status:429")
        guard.halt("transport_error:RuntimeError")
        self.assertEqual(guard.halt_reason, "http_status:429")

    def test_the_halt_flag_is_shared_across_selectors(self):
        state, candidates = first_round_inputs()
        guard = RunGuard()
        first = JevSelector(
            transport=FakeTransport([JevHttpError(401, None)]),
            budget=CallBudget(16),
            guard=guard,
        )
        second_transport = FakeTransport(
            [answer_response(candidates[0].candidate_id)]
        )
        second = JevSelector(
            transport=second_transport, budget=CallBudget(16), guard=guard
        )
        self.assertEqual(first.select_next_evidence(state, candidates), DEFER)
        self.assertEqual(second.select_next_evidence(state, candidates), DEFER)
        self.assertEqual(second_transport.call_count, 0)
        self.assertTrue(second.guard.halted)


class FirstResponseShapeTests(unittest.TestCase):
    """§7-E: the first completed response is checked against the documented
    shape, because HttpTransport has never run and the first call is the probe."""

    def setUp(self):
        self.state, self.candidates = first_round_inputs()
        self.wanted = self.candidates[0].candidate_id

    def assert_shape_halt(self, body, offending_field):
        selector, transport = selector_with([body])
        self.assertEqual(
            selector.select_next_evidence(self.state, self.candidates), DEFER
        )
        self.assertEqual(transport.call_count, 1)
        self.assertTrue(selector.guard.halted)
        reason = selector.guard.halt_reason
        self.assertTrue(
            reason.startswith(REASON_FIRST_RESPONSE_SHAPE),
            "halt_reason %r is not a shape mismatch" % reason,
        )
        self.assertEqual(
            reason, "%s:%s" % (REASON_FIRST_RESPONSE_SHAPE, offending_field)
        )
        self.assertEqual(selector.records[-1].failure_reason, reason)
        return selector

    def test_a_missing_top_level_model_halts_and_names_the_field(self):
        body = answer_response(self.wanted)
        del body["model"]
        self.assert_shape_halt(body, "model")

    def test_a_non_string_model_halts_and_names_the_field(self):
        body = answer_response(self.wanted)
        body["model"] = 113
        self.assert_shape_halt(body, "model")

    def test_a_missing_usage_halts_and_names_the_field(self):
        body = answer_response(self.wanted)
        del body["usage"]
        self.assert_shape_halt(body, "usage")

    def test_a_missing_input_tokens_halts_and_names_the_field(self):
        body = answer_response(self.wanted)
        del body["usage"]["input_tokens"]
        self.assert_shape_halt(body, "usage.input_tokens")

    def test_an_input_tokens_of_the_wrong_type_halts_and_names_the_field(self):
        for value in ("392", None, True, [392]):
            with self.subTest(value=repr(value)):
                body = answer_response(self.wanted)
                body["usage"]["input_tokens"] = value
                self.assert_shape_halt(body, "usage.input_tokens")

    def test_the_shape_helper_names_the_answer_fields_too(self):
        """The helper states the whole documented shape. `answers`, the question
        key and `choice` are also enforced on every call by the fail-closed
        path, which halts as well — so either route stops the run."""
        good = answer_response(self.wanted)
        self.assertIsNone(first_response_shape_error(good, QUESTION_KEY))
        self.assertEqual(first_response_shape_error("nope", QUESTION_KEY), "body")

        no_answers = answer_response(self.wanted)
        del no_answers["answers"]
        self.assertEqual(first_response_shape_error(no_answers, QUESTION_KEY), "answers")

        wrong_key = answer_response(self.wanted)
        wrong_key["answers"] = {"other": wrong_key["answers"][QUESTION_KEY]}
        self.assertEqual(
            first_response_shape_error(wrong_key, QUESTION_KEY),
            "answers.%s" % QUESTION_KEY,
        )

        no_choice = answer_response(self.wanted)
        del no_choice["answers"][QUESTION_KEY]["choice"]
        self.assertEqual(
            first_response_shape_error(no_choice, QUESTION_KEY),
            "answers.%s.choice" % QUESTION_KEY,
        )

    def test_a_shape_verified_good_call_records_the_envelope_and_does_not_halt(self):
        selector, transport = selector_with(
            [answer_response(self.wanted, input_tokens=392, output_tokens=65)]
        )
        self.assertEqual(
            selector.select_next_evidence(self.state, self.candidates), self.wanted
        )
        self.assertFalse(selector.guard.halted)
        self.assertEqual(selector.guard.halt_reason, "")
        self.assertTrue(selector.guard.first_response_checked)
        record = selector.records[-1]
        self.assertIsNone(record.failure_reason)
        self.assertEqual(record.response_model, JEV_MODEL)
        self.assertEqual(record.input_tokens, 392)
        self.assertEqual(record.output_tokens, 65)
        self.assertEqual(transport.call_count, 1)

    def test_the_strict_check_is_spent_on_the_first_completed_call_only(self):
        """§7-E (A) keeps the probe inside the 16 calls. A later response that
        omits `model` is recorded as absent, not halted — the endpoint has
        already been proven once."""
        later = answer_response(self.wanted)
        del later["model"]
        selector, transport = selector_with(
            [answer_response(self.wanted), later], repeat=False
        )
        self.assertEqual(
            selector.select_next_evidence(self.state, self.candidates), self.wanted
        )
        self.assertEqual(
            selector.select_next_evidence(self.state, self.candidates), self.wanted
        )
        self.assertEqual(transport.call_count, 2)
        self.assertFalse(selector.guard.halted)
        self.assertIsNone(selector.records[-1].response_model)


class HaltIsNotTheControllerPathTests(unittest.TestCase):
    """Both invariants at once: the Controller sees nothing, the run stops."""

    def test_the_controller_still_returns_normally_with_a_failing_transport(self):
        transport = FakeTransport([JevHttpError(529, {"error": "overloaded"})])
        selector = JevSelector(transport=transport, budget=CallBudget(16))
        result = investigate(
            support.intent(), support.rows(), support.tickets(), selector
        )
        state = result.state
        self.assertEqual(state.finish_reason, controller_mod.FINISH_DEFERRED)
        self.assertEqual(state.violations, [], "no exception, no violation")
        self.assertEqual(state.found_ticket_ids, [], "zero admitted evidence")
        self.assertEqual(state.retrievals, 0)
        # and the second device did fire
        self.assertTrue(selector.guard.halted)
        self.assertEqual(selector.guard.halt_reason, "http_status:529")
        # the Controller may try again within its own budget; the halt stops the
        # provider from calling out a second time
        self.assertEqual(transport.call_count, 1)

    def test_the_controller_module_knows_nothing_about_the_halt(self):
        """AST-based: `bia/controller.py` must not reference the guard at all."""
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "bia",
            "controller.py",
        )
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)
        names = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        } | {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        for forbidden in ("guard", "RunGuard", "halted", "halt_reason"):
            self.assertNotIn(forbidden, names)


class ScorerHaltTests(unittest.TestCase):
    """The scorer shares the flag and stops between cases (§7-E)."""

    def _scorer(self):
        import importlib
        import sys

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)
        return importlib.import_module("eval.v1.run_eval")

    def _reset_guard(self, scorer):
        scorer._JEV_PROCESS_GUARD.halted = False
        scorer._JEV_PROCESS_GUARD.halt_reason = ""
        scorer._JEV_PROCESS_GUARD.first_response_checked = False

    def test_the_scorer_factory_shares_one_halt_flag_across_selectors(self):
        scorer = self._scorer()
        try:
            first = scorer.build_jev_selector()
            second = scorer.build_jev_selector()
            self.assertIs(first.guard, second.guard)
            self.assertIs(first.guard, scorer._JEV_PROCESS_GUARD)
            first.guard.halt("http_status:401")
            self.assertTrue(second.guard.halted)
            self.assertEqual(second.guard.halt_reason, "http_status:401")
        finally:
            self._reset_guard(scorer)

    def test_main_stops_between_cases_and_writes_a_clearly_partial_file(self):
        import contextlib
        import io
        import shutil
        import tempfile

        scorer = self._scorer()
        real_run_case = scorer.run_case
        real_results_dir = scorer.RESULTS_DIR
        tmpdir = tempfile.mkdtemp(prefix="bia-halt-")
        seen = []

        def failing_run_case(case_id, selector, **kwargs):
            seen.append(case_id)
            observation, error = real_run_case(case_id, selector, **kwargs)
            if len(seen) == 2:
                scorer._JEV_PROCESS_GUARD.halt("http_status:429")
            return observation, error

        buffer = io.StringIO()
        try:
            scorer.run_case = failing_run_case
            scorer.RESULTS_DIR = tmpdir
            with contextlib.redirect_stdout(buffer):
                code = scorer.main(["--selector", "heuristic"])
        finally:
            scorer.run_case = real_run_case
            scorer.RESULTS_DIR = real_results_dir
            self._reset_guard(scorer)

        printed = buffer.getvalue()
        self.assertNotEqual(code, 0, "a halted run must exit non-zero")
        self.assertEqual(len(seen), 2, "the remaining cases must not be run")

        partial_path = os.path.join(tmpdir, "heuristic_PARTIAL.json")
        self.assertTrue(os.path.isfile(partial_path))
        self.assertFalse(
            os.path.isfile(os.path.join(tmpdir, "heuristic.json")),
            "a partial run must never be written to the completed results name",
        )
        with open(partial_path, encoding="utf-8") as handle:
            document = json.load(handle)
        shutil.rmtree(tmpdir, ignore_errors=True)

        self.assertTrue(document["halted"])
        self.assertTrue(document["partial"])
        self.assertFalse(document["comparable"])
        self.assertEqual(document["halt_reason"], "http_status:429")
        self.assertEqual(document["cases_completed"], 2)
        self.assertEqual(document["cases_total"], 8)
        self.assertEqual(document["cases_not_run"], 6)
        self.assertIn("calls_spent", document)
        self.assertNotIn("summary", document, "a stopped run has no verdict")
        self.assertNotIn("passed", document)
        self.assertEqual(len(document["cases"]), 2)

        self.assertIn("중단", printed)
        self.assertIn("http_status:429", printed)
        self.assertIn("사용한 호출 수", printed)
        self.assertNotIn("전체: PASS", printed)


if __name__ == "__main__":
    unittest.main()
