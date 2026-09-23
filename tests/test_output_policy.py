from __future__ import annotations

import unittest

from bia.analysis.decompose import decompose_ratio


def run(current, baseline, total_current, total_baseline):
    universe = tuple(sorted(set(current) | set(baseline)))
    return decompose_ratio(current, baseline, universe, "n", "d",
                           total_current, total_baseline)


class CancellationTests(unittest.TestCase):
    def test_heavy_cancellation_suppresses_share_and_top_contributor(self):
        # A -0.30, B +0.29 -> 순변화는 작고 gross 는 크다
        current = {("A",): {"n": 20, "d": 100}, ("B",): {"n": 79, "d": 100}}
        baseline = {("A",): {"n": 50, "d": 100}, ("B",): {"n": 50, "d": 100}}
        groups, totals, flags = run(current, baseline,
                                    {"n": 99, "d": 200}, {"n": 100, "d": 200})
        self.assertTrue(flags["heavy_cancellation"])
        self.assertTrue(flags["suppress_top_contributor"])
        for group in groups:
            self.assertIsNone(group.contribution_share)


class ShareBoundTests(unittest.TestCase):
    def test_a_share_above_one_is_not_exposed(self):
        """heavy_cancellation 에 걸리지 않으면서 share 가 1 을 넘는 구성.
        '전체 하락의 120%' 는 수학적으로 참이면서 사람에게 거짓말이다."""
        current = {("A",): {"n": 38, "d": 100}, ("B",): {"n": 52, "d": 100}}
        baseline = {("A",): {"n": 50, "d": 100}, ("B",): {"n": 50, "d": 100}}
        groups, totals, flags = run(current, baseline,
                                    {"n": 90, "d": 200}, {"n": 100, "d": 200})
        self.assertFalse(flags["heavy_cancellation"])
        by_key = {g.key["dim"]: g for g in groups}
        self.assertIsNone(by_key["A"].contribution_share)
        self.assertIsNone(by_key["B"].contribution_share)

    def test_a_clean_share_is_exposed(self):
        current = {("A",): {"n": 40, "d": 100}, ("B",): {"n": 50, "d": 100}}
        baseline = {("A",): {"n": 50, "d": 100}, ("B",): {"n": 50, "d": 100}}
        groups, _totals, flags = run(current, baseline,
                                     {"n": 90, "d": 200}, {"n": 100, "d": 200})
        self.assertFalse(flags["suppress_top_contributor"])
        by_key = {g.key["dim"]: g for g in groups}
        self.assertIsNotNone(by_key["A"].contribution_share)
        self.assertAlmostEqual(by_key["A"].contribution_share, 1.0, places=9)


class NearZeroTests(unittest.TestCase):
    def test_a_near_zero_net_suppresses_everything(self):
        current = {("A",): {"n": 5000, "d": 100000}, ("B",): {"n": 5001, "d": 100000}}
        baseline = {("A",): {"n": 5001, "d": 100000}, ("B",): {"n": 5000, "d": 100000}}
        _groups, _totals, flags = run(current, baseline,
                                      {"n": 10001, "d": 200000},
                                      {"n": 10001, "d": 200000})
        self.assertTrue(flags["suppress_top_contributor"])
