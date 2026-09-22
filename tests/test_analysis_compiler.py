from __future__ import annotations

import unittest

from bia.analysis.compiler import compile_request
from bia.analysis.errors import RequestError
from bia.analysis.registry import get_domain, register
from bia.analysis.request import AnalysisRequest, PeriodComparison
from bia.analysis.spec import DomainSpec, MetricSpec
from bia.types import Period

DOMAIN = DomainSpec(
    name="_compilertest",
    grain=("day", "channel", "device"),
    dimensions=("channel", "device"),
    metrics={
        "revenue": MetricSpec(name="revenue", kind="additive", value="revenue_krw"),
        "conversion_rate": MetricSpec(
            name="conversion_rate", kind="ratio",
            numerator="orders", denominator="sessions",
            numerator_bounded_by_denominator=True,
        ),
    },
)
register(DOMAIN)

COMPARISON = PeriodComparison(
    current=Period.of("2026-07-01", "2026-07-30"),
    baseline=Period.of("2026-06-01", "2026-06-30"),
)


def request(**overrides) -> AnalysisRequest:
    base = dict(domain="_compilertest", metric="revenue",
                breakdowns=("channel",), comparison=COMPARISON,
                rank_by="net_contribution")
    base.update(overrides)
    return AnalysisRequest(**base)


class CompilerValidationTests(unittest.TestCase):
    def test_unknown_domain_is_rejected(self):
        with self.assertRaises(RequestError):
            compile_request(request(domain="nope"))

    def test_unknown_metric_is_rejected(self):
        with self.assertRaises(RequestError):
            compile_request(request(metric="nope"))

    def test_breakdown_must_be_a_declared_dimension(self):
        with self.assertRaises(RequestError):
            compile_request(request(breakdowns=("category",)))

    def test_duplicate_breakdowns_are_rejected(self):
        with self.assertRaises(RequestError):
            compile_request(request(breakdowns=("channel", "channel")))

    def test_empty_breakdowns_are_rejected(self):
        with self.assertRaises(RequestError):
            compile_request(request(breakdowns=()))

    def test_rate_effect_rank_is_rejected_for_an_additive_metric(self):
        with self.assertRaises(RequestError):
            compile_request(request(rank_by="rate_effect"))

    def test_group_delta_rank_is_rejected_for_a_ratio_metric(self):
        with self.assertRaises(RequestError):
            compile_request(request(metric="conversion_rate", rank_by="group_delta"))

    def test_current_must_start_after_baseline_ends(self):
        flipped = PeriodComparison(
            current=Period.of("2026-06-01", "2026-06-30"),
            baseline=Period.of("2026-07-01", "2026-07-30"),
        )
        with self.assertRaises(RequestError):
            compile_request(request(comparison=flipped))


class PlanShapeTests(unittest.TestCase):
    def test_one_dimension_yields_overall_and_one_marginal(self):
        plan = compile_request(request(breakdowns=("channel",)))
        self.assertEqual(
            [(b.dimensions, b.cross) for b in plan.branches],
            [((), False), (("channel",), False)],
        )

    def test_two_dimensions_yield_marginals_plus_one_joint(self):
        plan = compile_request(request(breakdowns=("channel", "device")))
        self.assertEqual(
            [(b.dimensions, b.cross) for b in plan.branches],
            [((), False), (("channel",), False), (("device",), False),
             (("channel", "device"), True)],
        )

    def test_no_intermediate_combinations_are_generated(self):
        """3 축이면 marginal 3 + joint 1 이고, 중간 조합은 만들지 않는다."""
        wide = DomainSpec(
            name="_wide", grain=("day", "a", "b", "c"), dimensions=("a", "b", "c"),
            metrics={"m": MetricSpec(name="m", kind="additive", value="v")},
        )
        register(wide)
        plan = compile_request(
            AnalysisRequest(domain="_wide", metric="m", breakdowns=("a", "b", "c"),
                            comparison=COMPARISON, rank_by="net_contribution")
        )
        shapes = [b.dimensions for b in plan.branches]
        self.assertEqual(shapes, [(), ("a",), ("b",), ("c",), ("a", "b", "c")])

    def test_the_domain_and_metric_travel_with_the_plan(self):
        plan = compile_request(request())
        self.assertIs(plan.domain, get_domain("_compilertest"))
        self.assertEqual(plan.metric.name, "revenue")
