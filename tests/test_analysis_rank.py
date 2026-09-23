"""RANK 의 동률 처리와 **canonical tie-break 의 결정성**.

이 파일이 고정하는 계약은 둘이다.

1. `rank()` 는 `by` 값 내림차순으로 정렬하되, 두 값의 차이가 `FLOAT_TOL` 이하이면
   동률로 본다(float 동일성이 아니다).
2. 동률의 순서는 **도메인이 선언한 차원 순서**(`DomainSpec.dimensions`)의 값
   오름차순이다. 차원 이름의 알파벳순이 아니다 — 그것은 구현 부산물이고,
   `product` 를 `sku` 로 이름만 바꿔도 출력이 바뀌는 계약은 계약이 아니다.

fixture 하나의 기대 순서를 맞히는 테스트는 결정성을 증명하지 못한다. 그래서 여기의
본체는 **입력 순열 불변성**·**진짜 동률**·**차원 이름 민감도 부재** 세 가지다.
"""
from __future__ import annotations

import itertools
import unittest

from bia.analysis.compiler import compile_request
from bia.analysis.decompose import FLOAT_TOL
from bia.analysis.engine import run_plan
from bia.analysis.frame import Observation
from bia.analysis.operators import order_groups, rank
from bia.analysis.registry import register
from bia.analysis.request import AnalysisRequest, PeriodComparison
from bia.analysis.result import GroupResult
from bia.analysis.spec import DomainSpec, MetricSpec
from bia.types import Period

ONE_DIM = ("channel",)

CROSS_DIMS = ("product", "complaint_type")


def cell(product, complaint_type, net):
    return GroupResult(key={"product": product, "complaint_type": complaint_type},
                       net_contribution=net, group_delta=int(net))


def group(channel, net=None, delta=None, rate=None):
    return GroupResult(key={"channel": channel}, net_contribution=net,
                       group_delta=delta, rate_effect=rate)


def labels(ranking):
    return [g["channel"] for g in ranking["groups"]]


def ranked(groups, by="net_contribution", dimensions=ONE_DIM):
    return labels(rank(groups, by, dimensions))


class TieBreakTests(unittest.TestCase):
    """회귀 방어: 수학적 동률이 1 ulp 로 갈려 tie-break 이 죽는 결함.

    벤치마크 C08 에서 실제로 잠복해 있던 값을 그대로 쓴다. paid 의 net 은
    `50/400 - 20/200`, affiliate 의 net 은 `10/400` 으로 정확 산술에서는 둘 다
    `1/40` 이지만 float 로는 `0.024999999999999994` 와 `0.025` 다.

    고치기 전 `rank()` 는 `-value` 를 무허용오차로 비교해 이 7e-18 차이를 진짜
    차이로 취급했다. 그래서 `beta` 가 먼저 나오고 선언된 tie-break 은 한 번도
    발동하지 않았다. 아래 `assertNotEqual` 이 그 상황을 실제로 만들고 있음을
    보증한다 — 두 값이 float 로 같다면 이 테스트는 옛 구현에서도 통과했을 것이고,
    아무것도 증명하지 못한다.
    """

    SMALLER = 50 / 400 - 20 / 200     # 0.024999999999999994
    LARGER = 10 / 400 - 0.0           # 0.025

    def test_the_two_values_really_are_different_floats(self):
        self.assertNotEqual(self.SMALLER, self.LARGER)
        self.assertLessEqual(abs(self.SMALLER - self.LARGER), FLOAT_TOL)

    def test_a_float_split_tie_is_decided_by_the_declared_order(self):
        # float 내림차순이면 beta 가 먼저다. 선언된 규칙이면 alpha 가 먼저다.
        groups = [group("beta", net=self.LARGER), group("alpha", net=self.SMALLER)]
        self.assertEqual(ranked(groups), ["alpha", "beta"])

    def test_a_real_difference_still_beats_the_tie_break(self):
        # 차이가 허용오차보다 크면 값이 이긴다 — 동률 규칙이 값 순서를 먹지 않는다.
        groups = [group("alpha", net=0.0), group("beta", net=1.0)]
        self.assertEqual(ranked(groups), ["beta", "alpha"])

    def test_a_difference_just_outside_the_tolerance_is_not_a_tie(self):
        groups = [group("alpha", net=0.0), group("beta", net=FLOAT_TOL * 2)]
        self.assertEqual(ranked(groups), ["beta", "alpha"])

    def test_a_difference_exactly_at_the_tolerance_is_a_tie(self):
        groups = [group("beta", net=FLOAT_TOL), group("alpha", net=0.0)]
        self.assertEqual(ranked(groups), ["alpha", "beta"])

    def test_ties_do_not_chain_into_one_unbounded_bucket(self):
        """a≈b, b≈c 인데 a≉c 일 때 셋이 한 묶음이 되면 안 된다.

        묶음은 묶음의 첫 원소(최댓값)와 비교해 정하므로 폭이 `FLOAT_TOL` 로 묶인다.
        직전 원소와 이어 붙이는 구현이면 `aaa` 가 c 그룹이라 맨 앞으로 올라온다.
        """
        step = FLOAT_TOL * 0.8
        groups = [group("mmm", net=1.0),
                  group("zzz", net=1.0 - step),
                  group("aaa", net=1.0 - 2 * step)]
        self.assertEqual(ranked(groups), ["mmm", "zzz", "aaa"])

    def test_a_none_value_ranks_as_zero(self):
        groups = [group("alpha", net=None), group("beta", net=-1.0),
                  group("gamma", net=1.0)]
        self.assertEqual(ranked(groups), ["gamma", "alpha", "beta"])

    def test_none_ties_with_an_exact_zero_and_falls_back_to_the_declared_order(self):
        groups = [group("beta", net=0.0), group("alpha", net=None)]
        self.assertEqual(ranked(groups), ["alpha", "beta"])

    def test_integer_deltas_are_unaffected(self):
        # additive 의 group_delta 는 정수라 1 이상 떨어져 있다. 허용오차가 붙어도
        # 서로 다른 정수가 동률이 되는 일은 없다.
        groups = [group("alpha", delta=1), group("beta", delta=2),
                  group("gamma", delta=-3)]
        self.assertEqual(ranked(groups, "group_delta"), ["beta", "alpha", "gamma"])

    def test_ranking_reports_the_field_it_ranked_by(self):
        self.assertEqual(rank([group("alpha", rate=1.0)], "rate_effect", ONE_DIM)["by"],
                         "rate_effect")


class RealTieTests(unittest.TestCase):
    """비트까지 같은 진짜 동률에서 canonical key 가 2차 순서를 정하는가.

    1 ulp 동률(`TieBreakTests`)은 허용오차 묶기를 시험하고, 여기는 묶기와 무관하게
    tie-break 키 **자체**가 순서를 만드는지를 시험한다. 두 그룹의 값이 정확히 같음을
    `assertEqual` 로 먼저 못박는다 — 값이 다르면 이 테스트는 값 정렬만 확인하는
    빈 테스트가 된다.
    """

    def test_bitwise_equal_scores_are_ordered_by_the_canonical_key(self):
        left, right = 0.25, 1.0 / 4.0
        self.assertEqual(left, right)
        groups = [group("zzz", net=left), group("aaa", net=right)]
        self.assertEqual(ranked(groups), ["aaa", "zzz"])

    def test_bitwise_equal_scores_in_a_cross_breakdown_use_the_declared_order(self):
        """교차 분해에서 선언 순서와 알파벳순이 실제로 갈리는 자리.

        선언 순서는 `(product, complaint_type)` 이므로 product 가 먼저다:
        `(a, y)` 가 `(b, x)` 앞. 차원 이름 알파벳순이면 complaint_type 이 먼저라
        `x < y` 로 `(b, x)` 가 앞선다. 두 답이 다르므로 이 테스트는 규칙을 실제로
        가른다.
        """
        groups = [cell("b", "x", 5.0), cell("a", "y", 5.0)]
        self.assertEqual([g.key["product"] + g.key["complaint_type"]
                          for g in order_groups(groups, "net_contribution",
                                                CROSS_DIMS)],
                         ["ay", "bx"])


class PermutationInvarianceTests(unittest.TestCase):
    """같은 그룹 집합을 **모든 입력 순서**로 넣어도 결과가 하나인가.

    fixture 하나의 기대 순서만 맞히는 테스트는 `sorted` 의 안정성 덕에 우연히 맞을
    수 있다. 전체 순열을 돌려야 "입력 순서에 기대지 않는다" 를 실제로 증명한다.
    """

    def test_every_permutation_of_three_tied_groups_gives_one_order(self):
        base = [group("beta", net=0.25), group("alpha", net=0.25),
                group("gamma", net=0.25)]
        seen = set(tuple(ranked(list(p))) for p in itertools.permutations(base))
        self.assertEqual(len(seen), 1, seen)
        self.assertEqual(seen.pop(), ("alpha", "beta", "gamma"))

    def test_every_permutation_of_a_mixed_tie_and_non_tie_set(self):
        base = [group("beta", net=TieBreakTests.LARGER),
                group("alpha", net=TieBreakTests.SMALLER),
                group("gamma", net=1.0 - FLOAT_TOL * 0.8),
                group("delta", net=1.0)]
        want = ranked(base)
        for permutation in itertools.permutations(base):
            self.assertEqual(ranked(list(permutation)), want)
        self.assertEqual(want, ["delta", "gamma", "alpha", "beta"])

    def test_every_permutation_in_a_cross_breakdown_gives_one_order(self):
        base = [cell("b", "x", 5.0), cell("a", "y", 5.0), cell("a", "x", 5.0)]
        seen = set()
        for permutation in itertools.permutations(base):
            ordered = order_groups(list(permutation), "net_contribution", CROSS_DIMS)
            seen.add(tuple((g.key["product"], g.key["complaint_type"])
                           for g in ordered))
        self.assertEqual(len(seen), 1, seen)
        self.assertEqual(seen.pop(), (("a", "x"), ("a", "y"), ("b", "x")))


class OverallBranchTests(unittest.TestCase):
    """차원이 0개인 overall 분기에서도 tie-break 이 정의되는가.

    키는 빈 튜플이고 모든 그룹이 같은 키를 갖는다. 그래도 순서가 모호해지지 않는
    이유는 규칙이 아니라 **그 분기의 그룹이 하나뿐**이라는 사실이다. 엔진은 이
    분기에 ranking 을 싣지도 않는다(`engine._additive_branch` 의 `if
    branch.dimensions`). 여기서는 연산자 수준에서 호출이 성립함만 고정한다.
    """

    def test_an_empty_dimension_tuple_is_a_valid_tie_break_key(self):
        only = GroupResult(key={}, net_contribution=3.0, group_delta=3)
        ordered = order_groups([only], "net_contribution", ())
        self.assertEqual([g.key for g in ordered], [{}])

    def test_the_overall_branch_carries_no_ranking(self):
        result = run_plan(_plan(DOMAIN_ORIGINAL, CROSS_DIMS), _rows(CROSS_DIMS))
        overall = result.breakdowns[0]
        self.assertEqual(overall.dimensions, ())
        self.assertEqual(overall.ranking, {})
        self.assertEqual(len(overall.groups), 1)


BASE_PERIOD = Period.of("2026-06-01", "2026-06-02")
CUR_PERIOD = Period.of("2026-07-01", "2026-07-02")

# 두 도메인은 **구조가 같고 차원 이름만 다르다.** 선언 순서는 둘 다 "첫 축, 둘째 축".
# 이름 알파벳순은 서로 반대다: (complaint_type < product) 이지만 (sku < topic) 이다.
# 그래서 알파벳순 tie-break 을 쓰면 두 도메인이 **다른 순서**를 낸다.
DOMAIN_ORIGINAL = DomainSpec(
    name="_ranknametest_original",
    grain=("day",) + CROSS_DIMS,
    dimensions=CROSS_DIMS,
    metrics={"count": MetricSpec(name="count", kind="additive", value="count")},
)
RENAMED_DIMS = ("sku", "topic")
DOMAIN_RENAMED = DomainSpec(
    name="_ranknametest_renamed",
    grain=("day",) + RENAMED_DIMS,
    dimensions=RENAMED_DIMS,
    metrics={"count": MetricSpec(name="count", kind="additive", value="count")},
)
register(DOMAIN_ORIGINAL)
register(DOMAIN_RENAMED)

# (a, y) 와 (b, x) 가 동률 5 이고, (a, x) 는 1 이다.
CELLS = (("a", "x", 1), ("a", "y", 5), ("b", "x", 5))


def _rows(dimensions):
    out = []
    for day in BASE_PERIOD.dates():
        for first, second, _ in CELLS:
            out.append(Observation(day=day,
                                   keys=((dimensions[0], first),
                                         (dimensions[1], second)),
                                   measures=(("count", 0),)))
    for day in CUR_PERIOD.dates():
        for first, second, value in CELLS:
            out.append(Observation(day=day,
                                   keys=((dimensions[0], first),
                                         (dimensions[1], second)),
                                   measures=(("count", value),)))
    return out


def _plan(domain, dimensions):
    return compile_request(AnalysisRequest(
        domain=domain.name, metric="count", breakdowns=dimensions,
        comparison=PeriodComparison(current=CUR_PERIOD, baseline=BASE_PERIOD),
        rank_by="net_contribution",
    ))


class DimensionNameSensitivityTests(unittest.TestCase):
    """이번 변경의 핵심 주장: **차원 이름을 바꿔도 group ordering 은 그대로다.**

    이름만 다른 두 DomainSpec 을 엔진에 통과시켜 교차 분해의 순서를 값 튜플로
    비교한다. 알파벳순 tie-break 이 남아 있으면 두 도메인이 갈린다.
    """

    def _cross_order(self, domain, dimensions):
        result = run_plan(_plan(domain, dimensions), _rows(dimensions))
        cross = [b for b in result.breakdowns if b.cross]
        self.assertEqual(len(cross), 1)
        return [(g[dimensions[0]], g[dimensions[1]])
                for g in cross[0].ranking["groups"]]

    def test_the_two_names_really_sort_the_other_way_round(self):
        # 이 대칭이 깨지면 아래 테스트는 아무것도 가르지 못한다.
        self.assertLess(CROSS_DIMS[1], CROSS_DIMS[0])
        self.assertLess(RENAMED_DIMS[0], RENAMED_DIMS[1])

    def test_renaming_the_dimensions_does_not_change_the_order(self):
        self.assertEqual(self._cross_order(DOMAIN_ORIGINAL, CROSS_DIMS),
                         self._cross_order(DOMAIN_RENAMED, RENAMED_DIMS))

    def test_the_order_is_the_declared_one_not_the_alphabetical_one(self):
        # 알파벳순이면 ("b","x") 가 ("a","y") 앞에 온다.
        self.assertEqual(self._cross_order(DOMAIN_ORIGINAL, CROSS_DIMS),
                         [("a", "y"), ("b", "x"), ("a", "x")])


if __name__ == "__main__":
    unittest.main()
