from __future__ import annotations

import unittest

from bia.analysis.decompose import CANCELLATION_THRESHOLD, SHARE_EPSILON, decompose_ratio


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
        groups, _totals, flags = run(current, baseline,
                                      {"n": 10001, "d": 200000},
                                      {"n": 10001, "d": 200000})
        self.assertTrue(flags["suppress_top_contributor"])
        for group in groups:
            self.assertIsNone(group.contribution_share)


class CancellationThresholdBoundaryTests(unittest.TestCase):
    """CANCELLATION_THRESHOLD = 0.20 의 양쪽. gross 는 두 경우 모두 0.2 로 고정하고
    |delta_r| 만 0.038(비율 0.19) 과 0.042(비율 0.21) 로 갈라 경계를 사이에 둔다.
    CANCELLATION_THRESHOLD 상수 자체는 건드리지 않는다."""

    def test_ratio_just_below_threshold_is_heavy_cancellation(self):
        # A: +0.119, B: -0.081 -> gross=0.2, delta_r=0.038, ratio=0.19 (< 0.20)
        current = {("A",): {"n": 219, "d": 500}, ("B",): {"n": 19, "d": 500}}
        baseline = {("A",): {"n": 100, "d": 500}, ("B",): {"n": 100, "d": 500}}
        _groups, _totals, flags = run(current, baseline,
                                      {"n": 238, "d": 1000}, {"n": 200, "d": 1000})
        self.assertTrue(flags["heavy_cancellation"])

    def test_ratio_just_above_threshold_is_not_heavy_cancellation(self):
        # A: +0.121, B: -0.079 -> gross=0.2, delta_r=0.042, ratio=0.21 (>= 0.20)
        current = {("A",): {"n": 221, "d": 500}, ("B",): {"n": 21, "d": 500}}
        baseline = {("A",): {"n": 100, "d": 500}, ("B",): {"n": 100, "d": 500}}
        _groups, _totals, flags = run(current, baseline,
                                      {"n": 242, "d": 1000}, {"n": 200, "d": 1000})
        self.assertFalse(flags["heavy_cancellation"])

    def test_ratio_exactly_at_threshold_is_not_heavy_cancellation(self):
        """A: +0.096, B: -0.064 -> gross=0.16, delta_r=0.032, ratio=0.032/0.16.
        이 정수 조합은 부동소수 나눗셈 결과가 정확히 0.20 리터럴과 같은 double 로
        반올림된다(직접 검증됨). heavy_cancellation 비교는 '<' 이므로 정확히
        임계값과 같으면 heavy_cancellation 이 아니어야 한다 (>= 판정 고정)."""
        current = {("A",): {"n": 196, "d": 500}, ("B",): {"n": 36, "d": 500}}
        baseline = {("A",): {"n": 100, "d": 500}, ("B",): {"n": 100, "d": 500}}
        _groups, totals, flags = run(current, baseline,
                                     {"n": 232, "d": 1000}, {"n": 200, "d": 1000})
        delta_r = (totals["total_rate_effect"] + totals["total_mix_effect"]
                   + totals["entry_exit_effect"])
        ratio = abs(delta_r) / totals["gross_movement"]
        self.assertEqual(ratio, CANCELLATION_THRESHOLD)
        self.assertFalse(flags["heavy_cancellation"])


class ShareEpsilonBoundaryTests(unittest.TestCase):
    """SHARE_EPSILON = 0.0001 의 양쪽. 그룹을 하나만 둬 gross == |delta_r| 이 되게
    만들어(ratio 는 항상 1.0) heavy_cancellation 이 절대 끼어들지 못하게 하고
    delta_r 자체를 임계값 바로 아래/위로 둔다. SHARE_EPSILON 상수는 건드리지 않는다."""

    def test_delta_just_below_epsilon_suppresses(self):
        current = {("A",): {"n": 5000900, "d": 10000000}}
        baseline = {("A",): {"n": 5000000, "d": 10000000}}
        groups, _totals, flags = run(current, baseline,
                                      {"n": 5000900, "d": 10000000},
                                      {"n": 5000000, "d": 10000000})
        self.assertTrue(flags["suppress_top_contributor"])
        for group in groups:
            self.assertIsNone(group.contribution_share)

    def test_delta_just_above_epsilon_does_not_suppress(self):
        current = {("A",): {"n": 5001100, "d": 10000000}}
        baseline = {("A",): {"n": 5000000, "d": 10000000}}
        groups, _totals, flags = run(current, baseline,
                                     {"n": 5001100, "d": 10000000},
                                     {"n": 5000000, "d": 10000000})
        self.assertFalse(flags["suppress_top_contributor"])
        self.assertAlmostEqual(groups[0].contribution_share, 1.0, places=9)
