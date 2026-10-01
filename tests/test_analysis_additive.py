from __future__ import annotations

import datetime as dt
import unittest

from bia.analysis.compiler import compile_request
from bia.analysis.engine import execute
from bia.analysis.frame import Frame, Observation
from bia.analysis.registry import register
from bia.analysis.request import AnalysisRequest, PeriodComparison
from bia.analysis.result import GroupResult
from bia.analysis.spec import DomainSpec, MetricSpec
from bia.types import Period

DOMAIN = DomainSpec(
    name="_additivetest",
    grain=("day", "channel"),
    dimensions=("channel",),
    metrics={"orders": MetricSpec(name="orders", kind="additive", value="orders")},
)
register(DOMAIN)

BASE = Period.of("2026-06-01", "2026-06-02")
CUR = Period.of("2026-07-01", "2026-07-02")


def obs(day, channel, orders):
    return Observation(day=day, keys=(("channel", channel),),
                       measures=(("orders", orders),))


def rows():
    out = []
    for day in BASE.dates():
        out.append(obs(day, "paid", 10))
        out.append(obs(day, "organic", 5))
    for day in CUR.dates():
        out.append(obs(day, "paid", 20))
        out.append(obs(day, "organic", 5))
    return out


def plan():
    return compile_request(AnalysisRequest(
        domain="_additivetest", metric="orders", breakdowns=("channel",),
        comparison=PeriodComparison(current=CUR, baseline=BASE),
        rank_by="net_contribution",
    ))


class AdditiveArithmeticTests(unittest.TestCase):
    def setUp(self):
        self.result = execute(plan(), Frame.of(rows()))

    def test_totals_and_delta(self):
        """baseline 30, current 50 을 손으로 계산해 둔다."""
        self.assertEqual(self.result.comparison["baseline"], 30)
        self.assertEqual(self.result.comparison["current"], 50)
        self.assertEqual(self.result.comparison["delta"], 20)

    def test_relative_change_is_present_when_baseline_is_non_zero(self):
        self.assertAlmostEqual(self.result.comparison["relative_change"], 20 / 30.0)

    def test_group_deltas_sum_to_the_total_delta(self):
        breakdown = self.result.breakdowns[1]
        self.assertEqual(sum(g.group_delta for g in breakdown.groups), 20)

    def test_net_contribution_equals_group_delta_for_additive(self):
        breakdown = self.result.breakdowns[1]
        for group in breakdown.groups:
            self.assertEqual(group.net_contribution, group.group_delta)

    def test_additive_groups_carry_no_rate_or_mix(self):
        breakdown = self.result.breakdowns[1]
        for group in breakdown.groups:
            self.assertIsNone(group.rate_effect)
            self.assertIsNone(group.mix_effect)

    def test_ranking_is_breakdown_local(self):
        breakdown = self.result.breakdowns[1]
        self.assertEqual(breakdown.ranking["by"], "net_contribution")
        self.assertEqual(breakdown.ranking["groups"][0], {"channel": "paid"})


class AdditiveGroupValueTests(unittest.TestCase):
    """합 분해 그룹은 기간별 합계를 버리지 않는다. 비율 분해에는 이 필드가 없다."""

    def test_group_values_are_the_period_sums(self):
        """baseline paid 20 / organic 10, current paid 40 / organic 10 을 손으로 계산해 둔다."""
        groups = dict((g.key["channel"], g) for g in execute(plan(), Frame.of(rows())).breakdowns[1].groups)
        self.assertEqual((groups["paid"].current_value, groups["paid"].baseline_value), (40, 20))
        self.assertEqual((groups["organic"].current_value, groups["organic"].baseline_value),
                         (10, 10))
        for group in groups.values():
            self.assertIs(type(group.current_value), int)
            self.assertIs(type(group.baseline_value), int)
            self.assertEqual(group.current_value - group.baseline_value, group.group_delta)
            out = group.as_dict()
            self.assertEqual(out["current_value"], group.current_value)
            self.assertEqual(out["baseline_value"], group.baseline_value)

    def test_a_group_absent_from_one_period_has_value_zero_there(self):
        data = [obs(day, "paid", 3) for day in BASE.dates()]
        data += [obs(day, "organic", 4) for day in CUR.dates()]
        groups = dict((g.key["channel"], g) for g in execute(plan(), Frame.of(data)).breakdowns[1].groups)
        self.assertEqual((groups["paid"].current_value, groups["paid"].baseline_value), (0, 6))
        self.assertEqual((groups["organic"].current_value, groups["organic"].baseline_value),
                         (8, 0))
        self.assertIn("current_value", groups["paid"].as_dict())

    def test_as_dict_omits_the_fields_when_unset(self):
        out = GroupResult(key={"channel": "paid"}, net_contribution=1.0).as_dict()
        self.assertNotIn("current_value", out)
        self.assertNotIn("baseline_value", out)


class ZeroBaselineTests(unittest.TestCase):
    def test_relative_change_is_none_when_baseline_is_zero(self):
        data = [obs(day, "paid", 0) for day in BASE.dates()]
        data += [obs(day, "paid", 7) for day in CUR.dates()]
        result = execute(plan(), Frame.of(data))
        self.assertEqual(result.comparison["delta"], 14)
        self.assertIsNone(result.comparison["relative_change"])


class AdditiveCancellationFlagTests(unittest.TestCase):
    """합 metric 의 상쇄 판정. 전에는 이 자리가 무조건 False 를 실어 보냈다.

    비율 전용 개념인 `composition_dominant`·`simpson_strict` 는 여기 **없어야**
    한다 — 합 분해에는 rate/mix 분해 자체가 없으므로 False 로 적는 것은
    "검사했고 아니다" 라는 거짓 신호다.
    """

    RATIO_ONLY = ("composition_dominant", "simpson_strict")

    def _flags(self, data):
        return execute(plan(), Frame.of(data)).breakdowns[1].flags

    def test_a_textbook_cancellation_is_flagged(self):
        """+100 과 -100 으로 Δ=0 인 분해다. gross=200, |Δ|/gross=0."""
        data = [obs(day, "paid", 0) for day in BASE.dates()]
        data += [obs(day, "organic", 50) for day in BASE.dates()]
        data += [obs(day, "paid", 50) for day in CUR.dates()]
        data += [obs(day, "organic", 0) for day in CUR.dates()]
        flags = self._flags(data)
        self.assertEqual(flags["heavy_cancellation"], True)
        self.assertEqual(flags["suppress_top_contributor"], True)
        self.assertEqual(flags["decomposition_complete"], True)

    def test_a_clean_move_is_not_flagged(self):
        """paid 만 +20, organic 은 그대로. gross=20, |Δ|/gross=1.0."""
        flags = self._flags(rows())
        self.assertEqual(flags["heavy_cancellation"], False)
        self.assertEqual(flags["suppress_top_contributor"], False)

    def test_the_threshold_is_the_same_one_the_ratio_branch_uses(self):
        """|Δ|/gross 가 0.20 바로 아래면 heavy, 바로 위면 아니다.

        baseline paid 100 / organic 0, current paid 0 / organic X 로 두면
        gross = 100 + X, |Δ| = |X - 100| 이다. X=140 이면 40/240=0.167 (<0.20),
        X=250 이면 150/350=0.429 (>0.20).
        """
        def data_for(x):
            out = [obs(day, "paid", 50) for day in BASE.dates()]
            out += [obs(day, "organic", 0) for day in BASE.dates()]
            out += [obs(day, "paid", 0) for day in CUR.dates()]
            out += [obs(day, "organic", x // 2) for day in CUR.dates()]
            return out

        self.assertTrue(self._flags(data_for(140))["heavy_cancellation"])
        self.assertFalse(self._flags(data_for(250))["heavy_cancellation"])

    def test_the_ratio_only_flags_are_absent(self):
        for flags in (self._flags(rows()),):
            for name in self.RATIO_ONLY:
                self.assertNotIn(name, flags)


class GroupTransitionTests(unittest.TestCase):
    """활동 여부는 값이 0 이 아닌가로 본다. 그룹 범위는 유효 구간 안의 합집합."""

    def test_counts_and_invariant(self):
        result = execute(plan(), Frame.of(rows()))
        for breakdown in result.breakdowns:
            if breakdown.status != "ok":
                continue
            t = breakdown.group_transition
            self.assertEqual(set(t), {"entered", "exited", "persisted", "inactive"})
            self.assertEqual(sum(t.values()), len(breakdown.groups))
            expected = {"entered": 0, "exited": 0, "persisted": 0, "inactive": 0}
            for g in breakdown.groups:
                b, c = g.baseline_value != 0, g.current_value != 0
                expected["persisted" if b and c else "exited" if b else "entered" if c else "inactive"] += 1
            self.assertEqual(t, expected)
            self.assertEqual(breakdown.as_dict()["group_transition"], t)
