from __future__ import annotations

import datetime as dt
import unittest

from bia.analysis.compiler import compile_request
from bia.analysis.engine import run_plan
from bia.analysis.frame import Observation
from bia.analysis.registry import register
from bia.analysis.request import AnalysisRequest, PeriodComparison
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
        self.result = run_plan(plan(), rows())

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


class ZeroBaselineTests(unittest.TestCase):
    def test_relative_change_is_none_when_baseline_is_zero(self):
        data = [obs(day, "paid", 0) for day in BASE.dates()]
        data += [obs(day, "paid", 7) for day in CUR.dates()]
        result = run_plan(plan(), data)
        self.assertEqual(result.comparison["delta"], 14)
        self.assertIsNone(result.comparison["relative_change"])
