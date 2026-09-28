"""The v2 `suppress_top_contributor` flag reaches the final answer text.

The bundled corpus never sets the flag on a rendered (single-dimension)
breakdown, so it cannot prove this policy — it can only show nothing else
moved. These fixtures are built in the test and run through `investigate()` end
to end. Each one first asserts what the real engine decided, so a renderer test
cannot pass for the wrong reason.

Cancellation ratio of a breakdown = |Δ| / Σ|group Δ|. The pre-registered
threshold is strict: suppress when the ratio is `< 0.20`.
"""
from __future__ import annotations

import copy
import unittest
from typing import Dict, List, Tuple
from unittest import mock

from bia import answer as answer_mod
from bia.adapters.complaints import ComplaintAnalysisView
from bia.analysis.decompose import CANCELLATION_THRESHOLD
from bia.answer import SuppressedShareError, suppressed_share_lines
from bia.controller import RunResult, investigate
from bia.decision import DeterministicHeuristicSelector
from bia.types import DIM_COMPLAINT_TYPE, DIM_PRODUCT, MetricRow, Ticket

from . import support

DELAY = "delivery_delay"
CRASH = "app_crash"
FILLER = ("P-Filler", "billing")

SHARE_100 = "늘어난 그룹 합계의 100%"
PRODUCT_SUPPRESSION = "제품별 증가·감소가 크게 상쇄되어, 늘어난 그룹 합계 대비 기여율은 표시하지 않는다"
TYPE_SUPPRESSION = "불만 유형별 증가·감소가 크게 상쇄되어, 늘어난 그룹 합계 대비 기여율은 표시하지 않는다"
FULL_TAIL = "이므로, 위 비율은 순증가가 아니라 늘어난 그룹 합계를 기준으로 읽어야 한다"

Cells = Dict[Tuple[str, str], Tuple[int, int]]  # (product, type) -> (baseline, current)

# Product: P-Beta +50, P-Alpha -40 -> Δ 10, gross 90, 0.111 (suppressed).
# Type: delivery_delay +10 alone -> ratio 1.0 (not suppressed).
PRODUCT_CANCELS: Cells = {("P-Beta", DELAY): (0, 50), ("P-Alpha", DELAY): (40, 0)}
# Mirror: product P-Beta +10 alone, types delay +50 / crash -40.
TYPE_CANCELS: Cells = {("P-Beta", DELAY): (0, 50), ("P-Beta", CRASH): (40, 0)}
# Product: +60 / -40 -> Δ 20, gross 100, exactly 0.20 (not suppressed: strict <).
AT_THRESHOLD: Cells = {("P-Beta", DELAY): (0, 60), ("P-Alpha", DELAY): (40, 0)}
# Product: +60 / -30 -> Δ 30, gross 90, 0.333.
ABOVE_THRESHOLD: Cells = {("P-Beta", DELAY): (0, 60), ("P-Alpha", DELAY): (30, 0)}

SENTINEL_QUOTE = (
    support.SUPPORTING_TEXT + " 제품별로 증가분이 발생한 위치: P-Beta +50건 (" + SHARE_100 + ")"
)


def rows_for(cells: Cells) -> List[MetricRow]:
    """Every day carries a zero-delta filler row, so both periods are complete;
    the fixture counts sit on the first day of each period."""
    out: List[MetricRow] = []
    for period, index in ((support.BASELINE, 0), (support.CURRENT, 1)):
        for day in period.dates():
            out.append(MetricRow(day, FILLER[0], FILLER[1], 1))
        for (product, complaint_type), counts in sorted(cells.items()):
            if counts[index]:
                out.append(MetricRow(period.start, product, complaint_type, counts[index]))
    return out


def tickets_for(text: str = support.SUPPORTING_TEXT) -> List[Ticket]:
    return support.tickets(count=4, text=text)


def run(cells: Cells, text: str = support.SUPPORTING_TEXT) -> RunResult:
    return investigate(support.intent(), rows_for(cells), tickets_for(text),
                       DeterministicHeuristicSelector())


def run_unsuppressed(cells: Cells) -> RunResult:
    """The same run with the analysis reporting no suppression anywhere."""
    with mock.patch.object(ComplaintAnalysisView, "suppress_top_contributor",
                           lambda self, dimension: False):
        return run(cells)


def location_line(result: RunResult, label: str) -> str:
    prefix = label + "별로 증가분이 발생한 위치: "
    lines = [l for l in result.answer.confirmed if l.startswith(prefix)]
    assert len(lines) == 1, lines
    return lines[0]


def cancellation_line(result: RunResult, label: str) -> str:
    prefix = label + "별로 늘어난 그룹의 합은 "
    lines = [l for l in result.answer.confirmed if l.startswith(prefix)]
    assert len(lines) == 1, lines
    return lines[0]


def ratio(cells: Cells, dimension: str) -> float:
    by: Dict[str, int] = {}
    for (product, complaint_type), (base, cur) in cells.items():
        key = product if dimension == DIM_PRODUCT else complaint_type
        by[key] = by.get(key, 0) + cur - base
    return abs(sum(by.values())) / float(sum(abs(v) for v in by.values()))


class EngineDecisionTests(unittest.TestCase):
    """What the engine decided, before any rendering is looked at."""

    def _flags(self, cells: Cells):
        metrics = run(cells).state.metrics
        return (metrics.suppress_top_contributor(DIM_PRODUCT),
                metrics.suppress_top_contributor(DIM_COMPLAINT_TYPE))

    def test_threshold_is_the_pre_registered_value(self):
        self.assertEqual(CANCELLATION_THRESHOLD, 0.20)

    def test_the_boundary_fixture_sits_exactly_on_the_threshold_in_float(self):
        self.assertTrue(20 / 100 == 0.20)
        self.assertEqual(ratio(AT_THRESHOLD, DIM_PRODUCT), 0.20)
        self.assertFalse(ratio(AT_THRESHOLD, DIM_PRODUCT) < CANCELLATION_THRESHOLD)

    def test_each_fixture_gets_the_flags_it_was_built_for(self):
        self.assertAlmostEqual(ratio(PRODUCT_CANCELS, DIM_PRODUCT), 10 / 90.0)
        self.assertEqual(self._flags(PRODUCT_CANCELS), (True, False))
        self.assertEqual(self._flags(TYPE_CANCELS), (False, True))
        self.assertEqual(self._flags(AT_THRESHOLD), (False, False))
        self.assertEqual(self._flags(ABOVE_THRESHOLD), (False, False))

    def test_the_flag_comes_from_the_single_dimension_breakdown(self):
        # In TYPE_CANCELS the joint (product x type) breakdown is +50 / -40 and
        # cancels, while the product breakdown is +10 alone. Reading the joint
        # flag would suppress the product line.
        self.assertEqual(self._flags(TYPE_CANCELS)[0], False)

    def test_the_flag_is_not_serialized(self):
        result = run(PRODUCT_CANCELS)
        self.assertNotIn("suppress", repr(result.state.metrics.as_dict()))
        self.assertNotIn("suppress", repr(result.state.as_dict()))

    def test_an_unknown_dimension_fails_rather_than_reading_as_not_suppressed(self):
        with self.assertRaises(KeyError):
            run(PRODUCT_CANCELS).state.metrics.suppress_top_contributor("region")


class RenderedPolicyTests(unittest.TestCase):
    def test_below_threshold_the_share_is_removed_and_the_reason_stated(self):
        result = run(PRODUCT_CANCELS)
        self.assertEqual(location_line(result, "제품"), "제품별로 증가분이 발생한 위치: P-Beta +50건")
        confirmed = result.answer.confirmed
        index = confirmed.index(location_line(result, "제품"))
        self.assertEqual(confirmed[index + 1], PRODUCT_SUPPRESSION)
        self.assertEqual(
            cancellation_line(result, "제품"),
            "제품별로 늘어난 그룹의 합은 +50건이고 순증가는 +10건이다. "
            "차이는 같은 구간에 줄어든 그룹이 상쇄한 몫이다",
        )
        self.assertEqual(suppressed_share_lines(result.answer.render(), ["제품"]), [])

    def test_without_suppression_the_same_input_would_show_one_hundred_percent(self):
        result = run_unsuppressed(PRODUCT_CANCELS)
        self.assertEqual(location_line(result, "제품"),
                         "제품별로 증가분이 발생한 위치: P-Beta +50건 (" + SHARE_100 + ")")

    def test_dimensions_are_judged_independently(self):
        result = run(PRODUCT_CANCELS)
        self.assertEqual(location_line(result, "불만 유형"),
                         "불만 유형별로 증가분이 발생한 위치: 배송 지연 +10건 (" + SHARE_100 + ")")
        self.assertNotIn(TYPE_SUPPRESSION, result.answer.confirmed)

        mirror = run(TYPE_CANCELS)
        self.assertIn("(" + SHARE_100 + ")", location_line(mirror, "제품"))
        self.assertNotIn(PRODUCT_SUPPRESSION, mirror.answer.confirmed)
        self.assertNotIn("늘어난 그룹 합계의", location_line(mirror, "불만 유형"))
        self.assertIn(TYPE_SUPPRESSION, mirror.answer.confirmed)
        self.assertTrue(cancellation_line(mirror, "불만 유형").endswith("상쇄한 몫이다"))

    def test_exactly_at_the_threshold_the_share_is_shown(self):
        result = run(AT_THRESHOLD)
        self.assertEqual(location_line(result, "제품"),
                         "제품별로 증가분이 발생한 위치: P-Beta +60건 (" + SHARE_100 + ")")
        self.assertNotIn(PRODUCT_SUPPRESSION, result.answer.confirmed)
        self.assertTrue(cancellation_line(result, "제품").endswith(FULL_TAIL))

    def test_above_the_threshold_the_share_is_shown(self):
        result = run(ABOVE_THRESHOLD)
        self.assertEqual(location_line(result, "제품"),
                         "제품별로 증가분이 발생한 위치: P-Beta +60건 (" + SHARE_100 + ")")
        self.assertNotIn(PRODUCT_SUPPRESSION, result.answer.confirmed)
        self.assertTrue(cancellation_line(result, "제품").endswith(FULL_TAIL))

    def test_non_suppressed_output_matches_the_suppression_off_run(self):
        for cells in (AT_THRESHOLD, ABOVE_THRESHOLD):
            on, off = run(cells), run_unsuppressed(cells)
            self.assertEqual(on.as_dict(), off.as_dict())
            self.assertEqual(on.answer.allowed_numbers, off.answer.allowed_numbers)


class OnlyTheNamedSentencesChangeTests(unittest.TestCase):
    """Suppressed run vs the same input with suppression forced off."""

    def test_confirmed_differs_in_exactly_three_places(self):
        for cells, label, sentence in ((PRODUCT_CANCELS, "제품", PRODUCT_SUPPRESSION),
                                       (TYPE_CANCELS, "불만 유형", TYPE_SUPPRESSION)):
            on, off = run(cells), run_unsuppressed(cells)
            expected: List[str] = []
            for line in off.answer.confirmed:
                if line.startswith(label + "별로 증가분이 발생한 위치: "):
                    stripped = line.replace(" (" + SHARE_100 + ")", "")
                    self.assertNotEqual(stripped, line)
                    expected.extend([stripped, sentence])
                elif line.startswith(label + "별로 늘어난 그룹의 합은 "):
                    self.assertTrue(line.endswith(FULL_TAIL))
                    expected.append(line[: -len(FULL_TAIL)] + "이다")
                else:
                    expected.append(line)
            self.assertEqual(on.answer.confirmed, expected)
            self.assertEqual(len(on.answer.confirmed), len(off.answer.confirmed) + 1)

    def test_everything_else_in_the_run_is_identical(self):
        for cells in (PRODUCT_CANCELS, TYPE_CANCELS):
            on, off = run(cells), run_unsuppressed(cells)
            self.assertEqual(on.answer.evidence, off.answer.evidence)
            self.assertEqual(on.answer.unknown, off.answer.unknown)
            self.assertEqual(on.answer.quotes, off.answer.quotes)
            self.assertEqual(on.answer.status, off.answer.status)
            self.assertEqual(on.state.as_dict(), off.state.as_dict())
            self.assertEqual(on.intent.as_dict(), off.intent.as_dict())
            self.assertEqual(on.provider_name, off.provider_name)
            self.assertTrue(on.state.found_tickets)
            # The only number that leaves is the share of increase itself.
            self.assertEqual(on.answer.allowed_numbers - off.answer.allowed_numbers, set())
            self.assertLessEqual(off.answer.allowed_numbers - on.answer.allowed_numbers, {"100"})


class ShareFormatterIsNotReachedTests(unittest.TestCase):
    """Structural check first: a suppressed dimension never formats a share."""

    def _spied(self, cells: Cells):
        with mock.patch.object(answer_mod, "_format_share",
                               wraps=answer_mod._format_share) as spy:
            result = run(cells)
        return result, [call.args[1] for call in spy.call_args_list]

    def test_suppressed_dimension_makes_zero_calls(self):
        result, groups = self._spied(PRODUCT_CANCELS)
        self.assertEqual([g.dimension for g in groups], [DIM_COMPLAINT_TYPE])
        self.assertEqual(groups, list(result.state.top_complaint_types))

        result, groups = self._spied(TYPE_CANCELS)
        self.assertEqual([g.dimension for g in groups], [DIM_PRODUCT])

    def test_the_spy_sees_every_call_when_nothing_is_suppressed(self):
        result, groups = self._spied(ABOVE_THRESHOLD)
        self.assertEqual(groups, list(result.state.top_products)
                         + list(result.state.top_complaint_types))
        self.assertEqual(sorted({g.dimension for g in groups}),
                         sorted({DIM_PRODUCT, DIM_COMPLAINT_TYPE}))


class _IgnoresSuppression:
    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def suppress_top_contributor(self, dimension):
        return False


class GuardTests(unittest.TestCase):
    """Second line of defence, fed a renderer that forgot the policy."""

    def _regressed_renderer(self):
        original = answer_mod._write_metric_facts

        def regressed(doc, state):
            blind = copy.copy(state)
            blind.metrics = _IgnoresSuppression(state.metrics)
            original(doc, blind)

        return mock.patch.object(answer_mod, "_write_metric_facts", regressed)

    def test_the_error_is_a_runtime_error_not_an_assertion(self):
        self.assertTrue(issubclass(SuppressedShareError, RuntimeError))
        self.assertFalse(issubclass(SuppressedShareError, AssertionError))

    def test_a_renderer_that_ignores_suppression_is_stopped(self):
        with self._regressed_renderer():
            with self.assertRaises(SuppressedShareError) as caught:
                run(PRODUCT_CANCELS)
        message = str(caught.exception)
        self.assertIn("제품", message)
        # The message names the dimension, not the leaked sentence.
        self.assertNotIn("P-Beta", message)
        self.assertNotIn("%", message)

    def test_the_planted_regression_leaves_unsuppressed_runs_alone(self):
        with self._regressed_renderer():
            result = run(ABOVE_THRESHOLD)
        self.assertEqual(result.answer.render(), run(ABOVE_THRESHOLD).answer.render())

    def test_customer_text_that_repeats_the_sentence_is_not_a_claim(self):
        result = run(PRODUCT_CANCELS, text=SENTINEL_QUOTE)
        self.assertTrue(result.state.found_tickets)
        rendered = result.answer.render()
        self.assertIn(SENTINEL_QUOTE, rendered)
        # The matcher itself would fire on the quote: exclusion is claim_text()'s doing.
        self.assertTrue(suppressed_share_lines(rendered, ["제품"]))
        self.assertEqual(suppressed_share_lines(result.answer.claim_text(), ["제품"]), [])

    def test_the_guard_reads_the_analysis_not_the_renderer(self):
        doc = answer_mod.AnswerDocument(status="reported")
        doc.confirmed.append("제품별로 증가분이 발생한 위치: P-Beta %s건 (늘어난 그룹 합계의 %s%%)"
                             % (doc.num("+50"), doc.num("100")))
        doc.check()
        with self.assertRaises(SuppressedShareError):
            doc.check(frozenset({"제품"}))
        doc.check(frozenset({"불만 유형"}))


if __name__ == "__main__":
    unittest.main()
