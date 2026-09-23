from __future__ import annotations

import unittest
from fractions import Fraction

from bia.analysis.decompose import FLOAT_TOL, decompose_ratio, sign_with_tol


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
