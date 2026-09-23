from __future__ import annotations

import unittest
from fractions import Fraction

from bia.analysis.compiler import compile_request
from bia.analysis.decompose import FLOAT_TOL, decompose_ratio, sign_with_tol
from bia.analysis.engine import run_plan
from bia.analysis.errors import AnalysisRefused
from bia.analysis.frame import Observation
from bia.analysis.registry import register
from bia.analysis.request import AnalysisRequest, PeriodComparison
from bia.analysis.spec import DomainSpec, MetricSpec
from bia.types import Period


class SignTests(unittest.TestCase):
    def test_noise_below_tolerance_counts_as_zero(self):
        self.assertEqual(sign_with_tol(1e-16), 0)
        self.assertEqual(sign_with_tol(-1e-16), 0)
        self.assertEqual(sign_with_tol(0.5), 1)
        self.assertEqual(sign_with_tol(-0.5), -1)


class DecompositionTests(unittest.TestCase):
    """손으로 검산 가능한 작은 정수로 짜고 Fraction 으로 기대값을 만든다."""

    def setUp(self):
        # A: 10/100 -> 30/200,  B: 20/100 -> 10/100
        self.current = {("A",): {"n": 30, "d": 200}, ("B",): {"n": 10, "d": 100}}
        self.baseline = {("A",): {"n": 10, "d": 100}, ("B",): {"n": 20, "d": 100}}
        self.universe = (("A",), ("B",))
        self.total_current = {"n": 40, "d": 300}
        self.total_baseline = {"n": 30, "d": 200}

    def _expected_delta(self):
        return Fraction(40, 300) - Fraction(30, 200)

    def test_net_contribution_is_the_numerator_share_change(self):
        groups, _totals, _flags = decompose_ratio(
            self.current, self.baseline, self.universe, "n", "d",
            self.total_current, self.total_baseline,
        )
        by_key = {g.key["dim"]: g for g in groups}
        expected_a = float(Fraction(30, 300) - Fraction(10, 200))
        self.assertAlmostEqual(by_key["A"].net_contribution, expected_a, places=12)

    def test_contributions_sum_exactly_to_the_total_change(self):
        groups, _totals, _flags = decompose_ratio(
            self.current, self.baseline, self.universe, "n", "d",
            self.total_current, self.total_baseline,
        )
        self.assertAlmostEqual(sum(g.net_contribution for g in groups),
                               float(self._expected_delta()), places=12)

    def test_rate_and_mix_sum_to_net_for_comparable_groups(self):
        groups, _totals, _flags = decompose_ratio(
            self.current, self.baseline, self.universe, "n", "d",
            self.total_current, self.total_baseline,
        )
        for group in groups:
            if group.comparable:
                self.assertAlmostEqual(group.rate_effect + group.mix_effect,
                                       group.net_contribution, places=12)

    def test_three_totals_sum_to_the_total_change(self):
        _groups, totals, _flags = decompose_ratio(
            self.current, self.baseline, self.universe, "n", "d",
            self.total_current, self.total_baseline,
        )
        summed = (totals["total_rate_effect"] + totals["total_mix_effect"]
                  + totals["entry_exit_effect"])
        self.assertAlmostEqual(summed, float(self._expected_delta()), places=12)


class EntryExitTests(unittest.TestCase):
    def setUp(self):
        # B 는 baseline 에 없다 (진입)
        self.current = {("A",): {"n": 10, "d": 100}, ("B",): {"n": 5, "d": 100}}
        self.baseline = {("A",): {"n": 20, "d": 100}}
        self.universe = (("A",), ("B",))
        self.total_current = {"n": 15, "d": 200}
        self.total_baseline = {"n": 20, "d": 100}

    def test_an_entering_group_keeps_net_but_loses_the_split(self):
        groups, totals, flags = decompose_ratio(
            self.current, self.baseline, self.universe, "n", "d",
            self.total_current, self.total_baseline,
        )
        entering = [g for g in groups if g.key["dim"] == "B"][0]
        self.assertFalse(entering.comparable)
        self.assertIsNone(entering.rate_effect)
        self.assertIsNone(entering.mix_effect)
        self.assertAlmostEqual(entering.net_contribution, 5 / 200.0, places=12)

    def test_entry_exit_effect_collects_non_comparable_net(self):
        _groups, totals, _flags = decompose_ratio(
            self.current, self.baseline, self.universe, "n", "d",
            self.total_current, self.total_baseline,
        )
        self.assertAlmostEqual(totals["entry_exit_effect"], 5 / 200.0, places=12)

    def test_decomposition_is_not_complete_when_a_group_enters(self):
        _groups, _totals, flags = decompose_ratio(
            self.current, self.baseline, self.universe, "n", "d",
            self.total_current, self.total_baseline,
        )
        self.assertFalse(flags["decomposition_complete"])

    def test_composition_dominant_requires_a_complete_decomposition(self):
        """진입/이탈이 지배하는데 '구성이 지배했다' 고 부르면
        entry_exit_effect 를 따로 만든 이유와 모순된다."""
        _groups, _totals, flags = decompose_ratio(
            self.current, self.baseline, self.universe, "n", "d",
            self.total_current, self.total_baseline,
        )
        self.assertFalse(flags["composition_dominant"])


class FloatTolBoundaryTests(unittest.TestCase):
    """FLOAT_TOL(1e-9) 경계 자체도 양쪽을 확인해 둔다."""

    def test_exactly_at_tolerance_is_zero(self):
        self.assertEqual(sign_with_tol(FLOAT_TOL), 0)

    def test_just_above_tolerance_is_nonzero(self):
        self.assertEqual(sign_with_tol(FLOAT_TOL * 1.1), 1)
        self.assertEqual(sign_with_tol(-FLOAT_TOL * 1.1), -1)


_INTEGRITY_DOMAIN = DomainSpec(
    name="_ratiointegritytest",
    grain=("day", "channel"),
    dimensions=("channel",),
    metrics={
        "rate_free": MetricSpec(
            name="rate_free", kind="ratio", numerator="orders", denominator="sessions",
        ),
        "cvr_bounded": MetricSpec(
            name="cvr_bounded", kind="ratio", numerator="orders", denominator="sessions",
            numerator_bounded_by_denominator=True,
        ),
    },
)
register(_INTEGRITY_DOMAIN)

_I_BASE = Period.of("2026-06-01", "2026-06-01")
_I_CUR = Period.of("2026-07-01", "2026-07-01")


def _iobs(period, channel, sessions, orders):
    return Observation(day=period.start, keys=(("channel", channel),),
                       measures=(("orders", orders), ("sessions", sessions)))


def _iplan(metric):
    return compile_request(AnalysisRequest(
        domain="_ratiointegritytest", metric=metric, breakdowns=("channel",),
        comparison=PeriodComparison(current=_I_CUR, baseline=_I_BASE),
        rank_by="net_contribution",
    ))


class OverallDenominatorZeroRefusalTests(unittest.TestCase):
    """전체 분모가 한쪽 기간이라도 0 이면 계산을 시작하지 않고 거부한다."""

    def test_refused_before_any_branch_runs(self):
        rows = [
            _iobs(_I_BASE, "a", sessions=0, orders=0),
            _iobs(_I_CUR, "a", sessions=10, orders=5),
        ]
        with self.assertRaises(AnalysisRefused) as ctx:
            run_plan(_iplan("rate_free"), rows)
        self.assertEqual(ctx.exception.stage, "aggregate")
        self.assertIn("sessions", ctx.exception.reason)
        self.assertIn("current=10", ctx.exception.reason)
        self.assertIn("baseline=0", ctx.exception.reason)


class BoundedNumeratorRefusalTests(unittest.TestCase):
    """`numerator_bounded_by_denominator` 를 어기면(N_g > D_g) 거부한다.

    overall 분기는 두 채널을 합쳐 70<=150 이라 통과하고, channel 분기에서
    채널 a 하나만 60>50 으로 위반해야 실제로 걸린다."""

    def test_refused_with_group_key_and_both_values(self):
        rows = [
            _iobs(_I_BASE, "a", sessions=100, orders=10),
            _iobs(_I_BASE, "b", sessions=100, orders=10),
            _iobs(_I_CUR, "a", sessions=50, orders=60),
            _iobs(_I_CUR, "b", sessions=100, orders=10),
        ]
        with self.assertRaises(AnalysisRefused) as ctx:
            run_plan(_iplan("cvr_bounded"), rows)
        self.assertEqual(ctx.exception.stage, "integrity")
        reason = ctx.exception.reason
        self.assertIn("'channel': 'a'", reason)
        self.assertIn("orders=60", reason)
        self.assertIn("sessions=50", reason)
        self.assertIn("current", reason)


class GroupDenominatorZeroWithPositiveNumeratorTests(unittest.TestCase):
    """그룹 분모가 0 인데 그 그룹의 분자가 양수면 거부한다.

    함정: 그룹이 하나뿐이면 그 그룹의 분모 0 이 곧 전체 분모 0 이라 앞단의
    overall(aggregate) 검사에 먼저 걸린다. 그룹을 최소 둘 두고 다른 그룹이
    전체 분모를 살려둔 상태여야 이 그룹별(integrity) 검사가 실제로 밟힌다."""

    def test_refused_at_integrity_not_aggregate(self):
        rows = [
            _iobs(_I_BASE, "a", sessions=100, orders=10),
            _iobs(_I_BASE, "b", sessions=100, orders=10),
            _iobs(_I_CUR, "a", sessions=0, orders=5),
            _iobs(_I_CUR, "b", sessions=100, orders=10),
        ]
        with self.assertRaises(AnalysisRefused) as ctx:
            run_plan(_iplan("rate_free"), rows)
        self.assertEqual(ctx.exception.stage, "integrity")
        reason = ctx.exception.reason
        self.assertIn("'channel': 'a'", reason)
        self.assertIn("orders=5", reason)
        self.assertIn("sessions=0", reason)
        self.assertIn("current", reason)


_OVERALL_DOMAIN = DomainSpec(
    name="_ratiooveralltest",
    grain=("day", "channel"),
    dimensions=("channel",),
    metrics={
        "rate_free": MetricSpec(
            name="rate_free", kind="ratio", numerator="orders", denominator="sessions",
        ),
    },
)
register(_OVERALL_DOMAIN)


class OverallBranchInvariantTests(unittest.TestCase):
    """overall 분기(dimensions=()) 도 ratio 경로를 타며, universe 가 그룹 1개짜리
    분해가 되더라도 세 항 합은 여전히 ΔR 과 같아야 한다."""

    def setUp(self):
        rows = [
            Observation(day=_I_BASE.start, keys=(("channel", "a"),),
                       measures=(("orders", 10), ("sessions", 100))),
            Observation(day=_I_CUR.start, keys=(("channel", "a"),),
                       measures=(("orders", 30), ("sessions", 200))),
        ]
        plan = compile_request(AnalysisRequest(
            domain="_ratiooveralltest", metric="rate_free", breakdowns=("channel",),
            comparison=PeriodComparison(current=_I_CUR, baseline=_I_BASE),
            rank_by="net_contribution",
        ))
        self.result = run_plan(plan, rows)

    def test_overall_branch_has_a_single_group(self):
        breakdown = self.result.breakdowns[0]
        self.assertEqual(breakdown.dimensions, ())
        self.assertEqual(len(breakdown.groups), 1)

    def test_overall_delta_is_as_expected(self):
        self.assertAlmostEqual(self.result.comparison["delta"], 0.05, places=12)

    def test_three_terms_sum_to_delta_even_for_a_single_group_universe(self):
        breakdown = self.result.breakdowns[0]
        summed = (breakdown.totals["total_rate_effect"]
                  + breakdown.totals["total_mix_effect"]
                  + breakdown.totals["entry_exit_effect"])
        self.assertLessEqual(abs(summed - self.result.comparison["delta"]), FLOAT_TOL)
