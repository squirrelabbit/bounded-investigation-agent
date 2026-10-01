"""complaints 호환 뷰의 교차 한도 계약. 실제 compiler 와 engine 을 돌린다.

`status="omitted"` 인 `BreakdownResult` 를 손으로 만들면 어댑터가 플래그를 읽는다는 것만
증명하고, 엔진이 1000 에서는 내지 않고 1001 에서 낸다는 것은 증명하지 못한다. 그래서
입력 행을 만들어 compile·run 한 뒤 어댑터에 넘긴다.
"""
from __future__ import annotations

import datetime as dt
import unittest
from unittest import mock

from bia import metrics as v1_metrics
from bia.adapters.complaints import (REASON_JOINT_MISSING, REASON_JOINT_OMITTED,
                                     ComplaintAnalysisCompatibilityError,
                                     required_joint_breakdown,
                                     top_contributor_cells)
from bia.analysis import operators
from bia.analysis.compiler import compile_request
from bia.analysis.engine import execute
from bia.analysis.errors import AnalysisRefused
from bia.analysis.frame import Frame, Observation
from bia.analysis.request import AnalysisRequest, PeriodComparison
from bia.analysis.result import STATUS_OK, STATUS_OMITTED
from bia.domains import complaints as complaints_domain  # noqa: F401  (등록 부작용)
from bia.types import Period

BASELINE = Period.of("2026-06-01", "2026-06-01")
CURRENT = Period.of("2026-06-08", "2026-06-08")
TYPES = ("delay", "defect", "billing")


def _obs(day, product, complaint_type, count):
    return Observation(day=day,
                       keys=(("complaint_type", complaint_type), ("product", product)),
                       measures=(("count", count),))


def _rows(cells):
    """관측 (product, complaint_type) 쌍이 정확히 `cells` 개인 행.

    쌍 i 는 product 마다 하나라 전체 곱(product 수 × 유형 수 = 3·cells)은 관측 셀 수보다
    훨씬 크다. 쌍의 1/3 은 current 에만, 1/3 은 baseline 에만, 나머지는 양쪽에 둔다 —
    한 기간만 세거나 전체 곱을 세면 경계가 어긋난다.
    """
    out = []
    for i in range(cells):
        product = "p%04d" % i
        complaint_type = TYPES[i % 3]
        if i % 3 != 1:
            out.append(_obs(CURRENT.start, product, complaint_type, 1 + i % 5))
        if i % 3 != 0:
            out.append(_obs(BASELINE.start, product, complaint_type, 1 + i % 4))
    return out


def _run(rows, breakdowns=("product", "complaint_type")):
    plan = compile_request(AnalysisRequest(
        domain="complaints", metric="complaint_count", breakdowns=breakdowns,
        comparison=PeriodComparison(current=CURRENT, baseline=BASELINE),
        rank_by="net_contribution",
    ))
    return execute(plan, Frame.of(rows))


class CrossCellLimitBoundaryTests(unittest.TestCase):
    def test_the_limit_is_the_engine_constant(self):
        self.assertEqual(operators.CROSS_CELL_LIMIT, 1000)

    def test_the_fixture_counts_the_union_not_one_period_nor_the_full_product(self):
        rows = _rows(1001)
        current_pairs = set((r.key_of("product"), r.key_of("complaint_type"))
                            for r in rows if CURRENT.contains(r.day))
        baseline_pairs = set((r.key_of("product"), r.key_of("complaint_type"))
                             for r in rows if BASELINE.contains(r.day))
        products = set(r.key_of("product") for r in rows)
        types = set(r.key_of("complaint_type") for r in rows)
        self.assertEqual(len(current_pairs | baseline_pairs), 1001)
        self.assertLessEqual(len(current_pairs), operators.CROSS_CELL_LIMIT)
        self.assertLessEqual(len(baseline_pairs), operators.CROSS_CELL_LIMIT)
        self.assertGreater(len(products) * len(types), 1001)

    def _assert_view_builds(self, cells):
        result = _run(_rows(cells))
        joint = required_joint_breakdown(result)
        self.assertEqual(joint.status, STATUS_OK)
        self.assertEqual(joint.dimensions, ("product", "complaint_type"))
        self.assertEqual(len(joint.groups), cells)
        increased = top_contributor_cells(result)
        # 정수 delta 라 정확 정렬로 독립 재진술할 수 있다: delta 내림차순, product·유형 오름차순.
        expected = sorted(((g.group_delta, g.key["product"], g.key["complaint_type"])
                           for g in joint.groups if g.group_delta > 0),
                          key=lambda t: (-t[0], t[1], t[2]))
        self.assertEqual(increased, [(p, t) for _d, p, t in expected])
        self.assertTrue(increased)

    def test_999_cells_build_the_view(self):
        self._assert_view_builds(999)

    def test_exactly_1000_cells_build_the_view(self):
        self._assert_view_builds(1000)

    def test_1001_cells_is_a_normal_engine_omission(self):
        """엔진 쪽은 실패가 아니다 — 거부도 예외도 없이 생략을 기록한다."""
        try:
            result = _run(_rows(1001))
        except AnalysisRefused as refused:  # pragma: no cover - 실패 경로
            self.fail("the engine refused instead of omitting: %s" % refused)
        joint = [b for b in result.breakdowns if b.cross]
        self.assertEqual(len(joint), 1)
        self.assertEqual(joint[0].status, STATUS_OMITTED)
        self.assertEqual(joint[0].reason, "cross_cell_limit_exceeded")
        self.assertEqual(joint[0].observed_cells, 1001)
        # 단일 차원 분기는 한도 대상이 아니다.
        singles = [b for b in result.breakdowns if b.dimensions and not b.cross]
        self.assertEqual([b.status for b in singles], [STATUS_OK, STATUS_OK])

    def _assert_omitted_error(self, raised):
        error = raised.exception
        self.assertIs(type(error), ComplaintAnalysisCompatibilityError)
        self.assertEqual(error.reason, "required_joint_breakdown_omitted")
        self.assertEqual(error.reason, REASON_JOINT_OMITTED)
        self.assertEqual(error.cause, "cross_cell_limit_exceeded")
        self.assertEqual(error.observed_cells, 1001)
        self.assertEqual(error.limit, 1000)

    def test_1001_cells_fail_the_joint_breakdown_explicitly(self):
        result = _run(_rows(1001))
        with self.assertRaises(ComplaintAnalysisCompatibilityError) as raised:
            required_joint_breakdown(result)
        self._assert_omitted_error(raised)

    def test_1001_cells_fail_the_cell_view_instead_of_returning_empty(self):
        result = _run(_rows(1001))
        with self.assertRaises(ComplaintAnalysisCompatibilityError) as raised:
            top_contributor_cells(result)
        self._assert_omitted_error(raised)

    def test_the_failure_path_never_falls_back_to_v1_metrics(self):
        """fallback 이 있으면 patch 한 예외가 호환 오류 대신 나온다."""
        class FellBack(Exception):
            pass

        def boom(*_args, **_kwargs):
            raise FellBack("metrics.compute was called on the failure path")

        with mock.patch.object(v1_metrics, "compute", side_effect=boom) as patched:
            result = _run(_rows(1001))
            with self.assertRaises(ComplaintAnalysisCompatibilityError) as raised:
                top_contributor_cells(result)
            with self.assertRaises(ComplaintAnalysisCompatibilityError):
                required_joint_breakdown(result)
        self._assert_omitted_error(raised)
        self.assertEqual(patched.call_count, 0)


class CompatibilityErrorShapeTests(unittest.TestCase):
    def test_it_is_not_an_assertion_error_nor_an_engine_refusal(self):
        self.assertFalse(issubclass(ComplaintAnalysisCompatibilityError, AssertionError))
        self.assertFalse(issubclass(ComplaintAnalysisCompatibilityError, AnalysisRefused))

    def test_a_request_without_the_joint_breakdown_fails_distinctly(self):
        """교차를 만들지 않는 요청. 빈 목록이 아니라 '없다' 로 실패한다."""
        rows = _rows(10)
        for breakdowns in (("product",), ("complaint_type",),
                           ("complaint_type", "product")):
            with self.subTest(breakdowns=breakdowns):
                result = _run(rows, breakdowns)
                for view in (required_joint_breakdown, top_contributor_cells):
                    with self.assertRaises(ComplaintAnalysisCompatibilityError) as raised:
                        view(result)
                    self.assertEqual(raised.exception.reason, REASON_JOINT_MISSING)
                    self.assertNotEqual(raised.exception.reason, REASON_JOINT_OMITTED)
                    self.assertIsNone(raised.exception.cause)
                    self.assertIsNone(raised.exception.observed_cells)
                    self.assertEqual(raised.exception.limit, 1000)

    def test_no_increase_is_still_an_empty_list_not_an_error(self):
        """조용한 실패와 정상적인 '증가 없음' 은 구분된다."""
        rows = []
        for day in (BASELINE.start, CURRENT.start):
            rows.append(_obs(day, "alpha", "delay", 3))
        self.assertEqual(top_contributor_cells(_run(rows)), [])


if __name__ == "__main__":
    unittest.main()
