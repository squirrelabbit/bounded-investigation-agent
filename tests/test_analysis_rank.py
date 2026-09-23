"""RANK 의 동률 처리. 선언된 tie-break 이 부동소수 잡음에 지지 않는가.

이 파일이 고정하는 계약: `rank()` 는 `by` 값 내림차순으로 정렬하되, 두 값의 차이가
`FLOAT_TOL` 이하이면 동률로 보고 선언된 `(차원명, 값)` 오름차순으로 순서를 정한다.
"""
from __future__ import annotations

import itertools
import unittest

from bia.analysis.decompose import FLOAT_TOL
from bia.analysis.operators import rank
from bia.analysis.result import GroupResult


def group(channel, net=None, delta=None, rate=None):
    return GroupResult(key={"channel": channel}, net_contribution=net,
                       group_delta=delta, rate_effect=rate)


def labels(ranking):
    return [g["channel"] for g in ranking["groups"]]


class TieBreakTests(unittest.TestCase):
    """회귀 방어: 수학적 동률이 1 ulp 로 갈려 tie-break 이 죽는 결함.

    벤치마크 C08 에서 실제로 잠복해 있던 값을 그대로 쓴다. paid 의 net 은
    `50/400 - 20/200`, affiliate 의 net 은 `10/400` 으로 정확 산술에서는 둘 다
    `1/40` 이지만 float 로는 `0.024999999999999994` 와 `0.025` 다.

    고치기 전 `rank()` 는 `-value` 를 무허용오차로 비교해 이 7e-18 차이를 진짜
    차이로 취급했다. 그래서 `beta` 가 먼저 나오고 선언된 사전순 tie-break 은
    한 번도 발동하지 않았다. 아래 `assertNotEqual` 이 그 상황을 실제로 만들고
    있음을 보증한다 — 두 값이 float 로 같다면 이 테스트는 옛 구현에서도
    통과했을 것이고, 아무것도 증명하지 못한다.
    """

    SMALLER = 50 / 400 - 20 / 200     # 0.024999999999999994
    LARGER = 10 / 400 - 0.0           # 0.025

    def test_the_two_values_really_are_different_floats(self):
        self.assertNotEqual(self.SMALLER, self.LARGER)
        self.assertLessEqual(abs(self.SMALLER - self.LARGER), FLOAT_TOL)

    def test_a_float_split_tie_is_decided_by_the_declared_order(self):
        # float 내림차순이면 beta 가 먼저다. 선언된 규칙이면 alpha 가 먼저다.
        groups = [group("beta", net=self.LARGER), group("alpha", net=self.SMALLER)]
        self.assertEqual(labels(rank(groups, "net_contribution")),
                         ["alpha", "beta"])

    def test_a_real_difference_still_beats_the_tie_break(self):
        # 차이가 허용오차보다 크면 값이 이긴다 — 동률 규칙이 값 순서를 먹지 않는다.
        groups = [group("alpha", net=0.0), group("beta", net=1.0)]
        self.assertEqual(labels(rank(groups, "net_contribution")),
                         ["beta", "alpha"])

    def test_a_difference_just_outside_the_tolerance_is_not_a_tie(self):
        groups = [group("alpha", net=0.0), group("beta", net=FLOAT_TOL * 2)]
        self.assertEqual(labels(rank(groups, "net_contribution")),
                         ["beta", "alpha"])

    def test_a_difference_exactly_at_the_tolerance_is_a_tie(self):
        groups = [group("beta", net=FLOAT_TOL), group("alpha", net=0.0)]
        self.assertEqual(labels(rank(groups, "net_contribution")),
                         ["alpha", "beta"])

    def test_ties_do_not_chain_into_one_unbounded_bucket(self):
        """a≈b, b≈c 인데 a≉c 일 때 셋이 한 묶음이 되면 안 된다.

        묶음은 묶음의 첫 원소(최댓값)와 비교해 정하므로 폭이 `FLOAT_TOL` 로 묶인다.
        직전 원소와 이어 붙이는 구현이면 `aaa` 가 c 그룹이라 맨 앞으로 올라온다.
        """
        step = FLOAT_TOL * 0.8
        groups = [group("mmm", net=1.0),
                  group("zzz", net=1.0 - step),
                  group("aaa", net=1.0 - 2 * step)]
        self.assertEqual(labels(rank(groups, "net_contribution")),
                         ["mmm", "zzz", "aaa"])

    def test_the_result_does_not_depend_on_input_order(self):
        base = [group("beta", net=self.LARGER), group("alpha", net=self.SMALLER),
                group("gamma", net=1.0 - FLOAT_TOL * 0.8), group("delta", net=1.0)]
        want = labels(rank(base, "net_contribution"))
        for permutation in itertools.permutations(base):
            self.assertEqual(labels(rank(list(permutation), "net_contribution")),
                             want)

    def test_a_none_value_ranks_as_zero(self):
        groups = [group("alpha", net=None), group("beta", net=-1.0),
                  group("gamma", net=1.0)]
        self.assertEqual(labels(rank(groups, "net_contribution")),
                         ["gamma", "alpha", "beta"])

    def test_none_ties_with_an_exact_zero_and_falls_back_to_the_declared_order(self):
        groups = [group("beta", net=0.0), group("alpha", net=None)]
        self.assertEqual(labels(rank(groups, "net_contribution")),
                         ["alpha", "beta"])

    def test_integer_deltas_are_unaffected(self):
        # additive 의 group_delta 는 정수라 1 이상 떨어져 있다. 허용오차가 붙어도
        # 서로 다른 정수가 동률이 되는 일은 없다.
        groups = [group("alpha", delta=1), group("beta", delta=2),
                  group("gamma", delta=-3)]
        self.assertEqual(labels(rank(groups, "group_delta")),
                         ["beta", "alpha", "gamma"])

    def test_ranking_reports_the_field_it_ranked_by(self):
        self.assertEqual(rank([group("alpha", rate=1.0)], "rate_effect")["by"],
                         "rate_effect")


if __name__ == "__main__":
    unittest.main()
