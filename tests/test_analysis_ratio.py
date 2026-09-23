from __future__ import annotations

import unittest
from fractions import Fraction

from bia.analysis.compiler import compile_request
from bia.analysis.decompose import (FLOAT_TOL, decompose_ratio,
                                   sign_with_tol, snap_share_boundary)
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

    def test_simpson_strict_is_false_when_a_group_enters(self):
        """decomposition_complete 가 False 인 상황에서는 simpson_strict 도
        False 여야 한다 (진입/이탈 지배 사례를 simpson 역전으로 부르면 안 된다)."""
        _groups, _totals, flags = decompose_ratio(
            self.current, self.baseline, self.universe, "n", "d",
            self.total_current, self.total_baseline,
        )
        self.assertFalse(flags["simpson_strict"])


class SimpsonStrictTests(unittest.TestCase):
    """진짜 Simpson 역전: 두 그룹 모두 rate_effect 부호가 같은데(둘 다 개선),
    overall delta_r 은 반대 부호(악화)로 나온다. 진입/이탈 그룹이 없어
    decomposition_complete 는 True 여야 한다.

    A: baseline 90/100 (r=0.9) -> current 19/20 (r=0.95)
    B: baseline 1/10 (r=0.1) -> current 18/120 (r=0.15)
    overall: 91/110 ~= 0.827 -> 37/140 ~= 0.264, delta_r < 0
    두 그룹 모두 비율이 올랐으므로 rate_effect 는 둘 다 양수 -> 역전.
    """

    def setUp(self):
        self.current = {("A",): {"n": 19, "d": 20}, ("B",): {"n": 18, "d": 120}}
        self.baseline = {("A",): {"n": 90, "d": 100}, ("B",): {"n": 1, "d": 10}}
        self.universe = (("A",), ("B",))
        self.total_current = {"n": 37, "d": 140}
        self.total_baseline = {"n": 91, "d": 110}

    def test_delta_r_is_negative_by_hand_calculation(self):
        expected = float(Fraction(37, 140) - Fraction(91, 110))
        self.assertLess(expected, 0)
        groups, _totals, _flags = decompose_ratio(
            self.current, self.baseline, self.universe, "n", "d",
            self.total_current, self.total_baseline,
        )
        summed = sum(g.net_contribution for g in groups)
        self.assertAlmostEqual(summed, expected, places=12)

    def test_both_groups_improve_while_overall_worsens_is_flagged_strict(self):
        groups, _totals, flags = decompose_ratio(
            self.current, self.baseline, self.universe, "n", "d",
            self.total_current, self.total_baseline,
        )
        by_key = {g.key["dim"]: g for g in groups}
        self.assertTrue(by_key["A"].rate_effect > 0)
        self.assertTrue(by_key["B"].rate_effect > 0)
        delta_r = sum(g.net_contribution for g in groups)
        self.assertLess(delta_r, 0)
        self.assertTrue(flags["decomposition_complete"])
        self.assertTrue(flags["simpson_strict"])


class FloatTolBoundaryTests(unittest.TestCase):
    """FLOAT_TOL(1e-9) 경계 자체도 양쪽을 확인해 둔다."""

    def test_exactly_at_tolerance_is_zero(self):
        self.assertEqual(sign_with_tol(FLOAT_TOL), 0)

    def test_just_above_tolerance_is_nonzero(self):
        self.assertEqual(sign_with_tol(FLOAT_TOL * 1.1), 1)
        self.assertEqual(sign_with_tol(-FLOAT_TOL * 1.1), -1)


class ShareBoundaryToleranceTests(unittest.TestCase):
    """`0 < share <= 1` 의 경계는 float 표현 오차로 뒤집혀서는 안 된다.

    한 그룹이 전체 변화를 **정확히** 혼자 설명하는 배치(candidate 가 정확 산술로 딱 1)를
    두 벌 만든다. 같은 수학인데 float 로는 하나는 1 을 밑돌고 하나는 1 을 웃돈다 —
    스냅이 없으면 웃도는 쪽만 share 를 잃는다. 진짜로 1 을 넘는 배치는 계속 버려야 한다.
    """

    def _shares(self, moving_d, static_d, static_n):
        """A 만 움직이고 B 는 양 기간 동일한 2 그룹 비율 분해를 돌린다."""
        total_d = moving_d + static_d
        current = {("A",): {"n": 1, "d": moving_d},
                   ("B",): {"n": static_n, "d": static_d}}
        baseline = {("A",): {"n": 0, "d": moving_d},
                    ("B",): {"n": static_n, "d": static_d}}
        groups, _totals, _flags = decompose_ratio(
            current, baseline, (("A",), ("B",)), "n", "d",
            {"n": 1 + static_n, "d": total_d}, {"n": static_n, "d": total_d},
        )
        return dict((g.key["dim"], g) for g in groups)

    def _raw_candidate(self, group, moving_d, static_d, static_n):
        total_d = float(moving_d + static_d)
        delta = (1 + static_n) / total_d - static_n / total_d
        return group.net_contribution / delta

    def test_a_candidate_that_lands_just_below_one_reports_exactly_one(self):
        # A: 0/1 -> 1/1, B: 2/2 고정. 정확 산술로 candidate = 1.
        group = self._shares(1, 2, 2)["A"]
        raw = self._raw_candidate(group, 1, 2, 2)
        self.assertLess(raw, 1.0)
        self.assertGreater(raw, 1.0 - FLOAT_TOL)
        self.assertEqual(group.contribution_share, 1.0)

    def test_a_candidate_that_lands_just_above_one_reports_exactly_one(self):
        # A: 0/1 -> 1/1, B: 2/4 고정. 같은 수학인데 float 는 1 을 웃돈다.
        group = self._shares(1, 4, 2)["A"]
        raw = self._raw_candidate(group, 1, 4, 2)
        self.assertGreater(raw, 1.0)
        self.assertLess(raw, 1.0 + FLOAT_TOL)
        self.assertEqual(group.contribution_share, 1.0)

    def test_a_candidate_genuinely_above_one_is_still_dropped(self):
        # A 가 +2/10, B 가 -1/10 이라 delta 는 +1/10 이고 A 의 candidate 는 2 다.
        current = {("A",): {"n": 3, "d": 5}, ("B",): {"n": 1, "d": 5}}
        baseline = {("A",): {"n": 1, "d": 5}, ("B",): {"n": 2, "d": 5}}
        groups, _totals, flags = decompose_ratio(
            current, baseline, (("A",), ("B",)), "n", "d",
            {"n": 4, "d": 10}, {"n": 3, "d": 10},
        )
        by_key = dict((g.key["dim"], g) for g in groups)
        self.assertFalse(flags["suppress_top_contributor"])
        self.assertAlmostEqual(
            by_key["A"].net_contribution / (Fraction(4, 10) - Fraction(3, 10)), 2.0)
        self.assertIsNone(by_key["A"].contribution_share)

    def test_a_group_with_no_movement_still_reports_no_share(self):
        group = self._shares(1, 2, 2)["B"]
        self.assertEqual(group.net_contribution, 0.0)
        self.assertIsNone(group.contribution_share)


class SnapShareBoundaryTests(unittest.TestCase):
    """스냅 규칙 자체의 네 갈래. 0 쪽 띠는 정수 사례로 만들 수 없어 여기서 고정한다."""

    def test_within_tolerance_of_one_snaps_to_one(self):
        # `1.0 + FLOAT_TOL` 은 double 로 반올림되면서 차이가 1e-9 를 아주 살짝 넘는다.
        # 띠의 **안쪽**을 보는 것이 목적이므로 절반 폭을 쓴다.
        for value in (1.0 - FLOAT_TOL / 2, 1.0, 1.0 + FLOAT_TOL / 2):
            with self.subTest(value=value):
                self.assertEqual(snap_share_boundary(value), 1.0)

    def test_beyond_tolerance_of_one_is_left_alone_and_therefore_dropped(self):
        value = 1.0 + FLOAT_TOL * 10
        self.assertEqual(snap_share_boundary(value), value)
        self.assertFalse(0 < snap_share_boundary(value) <= 1)

    def test_within_tolerance_of_zero_snaps_to_zero_and_is_dropped(self):
        for value in (FLOAT_TOL, -FLOAT_TOL, 0.0, 1e-12):
            with self.subTest(value=value):
                self.assertEqual(snap_share_boundary(value), 0.0)
                self.assertFalse(0 < snap_share_boundary(value) <= 1)

    def test_an_ordinary_share_is_untouched(self):
        self.assertEqual(snap_share_boundary(0.25), 0.25)


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
