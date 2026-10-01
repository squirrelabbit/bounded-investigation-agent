"""StructuredAnalysisResult 를 v1 evidence pipeline 이 쓰는 모양으로 옮긴다.

공용 엔진은 여기까지 오지 않는다. 도메인 동작은 adapter 의 책임이다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..analysis.operators import CROSS_CELL_LIMIT, order_groups
from ..analysis.qualification import (
    ACTION_ACCEPT, REASON_CONFLICTING_DUPLICATE, REASON_EMPTY_PERIOD,
    REASON_INSUFFICIENT_COMMON_WINDOW, SCOPE_BASELINE, SCOPE_CURRENT, WINDOW_NO_OVERLAP,
    ExecutableQualification, RejectedQualification,
)
from ..analysis.request import RANK_GROUP_DELTA
from ..analysis.result import (STATUS_OK, BreakdownResult, GroupResult,
                               StructuredAnalysisResult)
from ..analysis.spec import KIND_ADDITIVE
from ..integrity import MODE_ALIGNED_WINDOW, MODE_BLOCKED, MODE_FULL, Comparability, PeriodIntegrity
from ..types import (DIM_COMPLAINT_TYPE, DIM_PRODUCT, METRIC_COMPLAINT_COUNT,
                     CellDelta, GroupDelta)

JOINT_DIMENSIONS = ("product", "complaint_type")

# v1 `bia.metrics` 의 선택 규칙을 다시 진술한다. import 하면 legacy 모듈이 제품 경로에
# 남는다. 두 값이 legacy 와 같은지는 테스트가 legacy 를 import 해 확인한다.
TOP_SHARE_TARGET = 0.8
TOP_MAX = 3

REASON_JOINT_OMITTED = "required_joint_breakdown_omitted"
REASON_JOINT_MISSING = "required_joint_breakdown_missing"


class ComplaintAnalysisCompatibilityError(Exception):
    """공용 분석은 성공했지만 complaints 호환 뷰를 만들 수 없다.

    엔진 실패가 아니다 — 교차 한도 초과 생략은 엔진의 정상 산출이다. 그래서
    `AnalysisRefused` 가 아니고, 호출자의 `except AssertionError` 에 삼켜지지 않도록
    `AssertionError` 도 상속하지 않는다. v1 은 셀 상한이 없으므로 이것은 v1 대비
    의도한 호환 변화다. 빈 `cells` 를 돌려주거나 `metrics.compute` 로 되돌아가지 않는다.
    """

    def __init__(self, reason: str, cause: Optional[str],
                 observed_cells: Optional[int], limit: int = CROSS_CELL_LIMIT) -> None:
        super().__init__(
            "complaints view needs the %s cross breakdown: %s (cause=%s, "
            "observed_cells=%s, limit=%d)"
            % ("x".join(JOINT_DIMENSIONS), reason, cause, observed_cells, limit))
        self.reason = reason
        self.cause = cause
        self.observed_cells = observed_cells
        self.limit = limit


def required_joint_breakdown(result: StructuredAnalysisResult) -> BreakdownResult:
    """(product, complaint_type) 교차 분해를 돌려준다. 생략됐거나 없으면 위 오류."""
    for breakdown in result.breakdowns:
        if breakdown.cross and breakdown.dimensions == JOINT_DIMENSIONS:
            if breakdown.status != STATUS_OK:
                raise ComplaintAnalysisCompatibilityError(
                    REASON_JOINT_OMITTED, breakdown.reason, breakdown.observed_cells)
            return breakdown
    raise ComplaintAnalysisCompatibilityError(REASON_JOINT_MISSING, None, None)


def top_contributor_cells(result: StructuredAnalysisResult) -> List[Tuple[str, str]]:
    """증가가 발생한 (제품, 불만 유형) 셀을 delta 내림차순으로 준다.

    v1 의 `metrics.compute(...).cells` 가 내던 순서와 같아야 한다 —
    delta 내림차순, 동률이면 product, complaint_type 오름차순.

    정렬은 `RANK` 와 **같은 함수**를 지난다. 손으로 쓴 `sorted(-net, ...)` 은 동률을
    float 동일성으로 보고, 그래서 수학적으로 같은 두 셀이 1 ulp 로 갈리면 선언한
    tie-break 이 발동하지 못한다 — `rank()` 에서 고친 바로 그 결함이다. 오늘 이
    어댑터가 안전한 이유(합 metric 의 net 은 정수 delta 의 float 이라 비트 동일)는
    입력 데이터의 성질이지 코드의 성질이 아니므로, 로직을 복사하지 않고 한 곳에서
    나오게 한다.

    tie-break 을 여기서 따로 지정하지 않는다. 공용 canonical 규칙이 곧 선언된 차원
    순서이므로, 이 어댑터가 필요로 하던 순서(product 먼저, 그다음 complaint_type)가
    기본값이다. 어댑터 예외가 있으면 "공용 경로가 무엇을 보장하는가" 가 흐려진다.

    교차 분해가 생략됐거나 없으면 `ComplaintAnalysisCompatibilityError` 다. 증가 셀이
    하나도 없는 정상 결과(`[]`)와 구분할 수 없는 빈 목록은 돌려주지 않는다.
    """
    breakdown = required_joint_breakdown(result)
    ordered = order_groups(breakdown.groups, "net_contribution", breakdown.dimensions)
    return [(g.key["product"], g.key["complaint_type"])
            for g in ordered if g.net_contribution > 0]


@dataclass
class ComplaintAnalysisView:
    """`ComplaintAnalysis` 계약을 v2 결과로 채운 v1 호환 뷰.

    `as_dict()` 는 키 삽입 순서까지 v1 `MetricResult.as_dict()` 와 같다 — 정렬 없이
    덤프되는 결과 파일이 바이트 비교 대상이다.
    """

    current_total: int
    baseline_total: int
    delta: int
    pct_change: Optional[float]
    by_product: List[GroupDelta]
    by_complaint_type: List[GroupDelta]
    cells: List[CellDelta]
    # 단일 차원 분해별 v2 `suppress_top_contributor`. 교차 분해의 플래그가 아니다 —
    # 답변이 렌더링하는 것은 단일 차원 분해다. `as_dict()` 에 싣지 않는다(legacy/JEV 계약).
    suppressed: Dict[str, bool] = field(default_factory=dict)

    @property
    def increased(self) -> bool:
        return self.delta > 0

    def top(self, dimension: str) -> List[GroupDelta]:
        groups = self.by_product if dimension == DIM_PRODUCT else self.by_complaint_type
        return top_contributors(groups)

    def suppress_top_contributor(self, dimension: str) -> bool:
        # 모르는 차원은 KeyError 다. 빠진 판정을 "억제 안 함" 으로 읽으면 정책이 조용히 꺼진다.
        return self.suppressed[dimension]

    def as_dict(self) -> Dict[str, object]:
        return {
            "current_total": self.current_total,
            "baseline_total": self.baseline_total,
            "delta": self.delta,
            "pct_change": self.pct_change,
            "by_product": [g.as_dict() for g in self.by_product],
            "by_complaint_type": [g.as_dict() for g in self.by_complaint_type],
        }


def top_contributors(groups: List[GroupDelta]) -> List[GroupDelta]:
    """증가분의 TOP_SHARE_TARGET 을 덮는 양의 그룹, 최대 TOP_MAX 개. 들어온 순서대로 누적한다."""
    positives = [g for g in groups if g.delta > 0]
    picked: List[GroupDelta] = []
    cumulative = 0.0
    for group in positives:
        picked.append(group)
        cumulative += group.share_of_increase
        if cumulative >= TOP_SHARE_TARGET or len(picked) >= TOP_MAX:
            break
    return picked


def _exact_int(value: object, what: str) -> int:
    # bool 은 int 의 하위형이고 float 는 json 에서 "1903.0" 이 된다. 둘 다 v1 바이트를 깬다.
    if type(value) is not int:
        raise TypeError("complaints view needs an int for %s, got %r (%s)"
                        % (what, value, type(value).__name__))
    return value


def _group_values(group: GroupResult) -> Tuple[int, int, int]:
    label = "group %r" % (group.key,)
    current = _exact_int(group.current_value, label + " current_value")
    baseline = _exact_int(group.baseline_value, label + " baseline_value")
    delta = _exact_int(group.group_delta, label + " group_delta")
    if current - baseline != delta:
        raise ValueError("%s: current_value - baseline_value = %d but group_delta = %d"
                         % (label, current - baseline, delta))
    return current, baseline, delta


def _single_breakdown(result: StructuredAnalysisResult, dimension: str) -> BreakdownResult:
    # 교차 한도는 단일 차원 분기에 걸리지 않고 요청은 두 차원을 모두 싣는다. 그래서
    # 여기서 실패하면 엔진·요청 계약이 깨진 것이다 — 호환 오류로 위장하지 않는다.
    for breakdown in result.breakdowns:
        if not breakdown.cross and breakdown.dimensions == (dimension,):
            if breakdown.status != STATUS_OK:
                raise ValueError("the %s breakdown is %s (%s)"
                                 % (dimension, breakdown.status, breakdown.reason))
            return breakdown
    raise ValueError("the result has no %s breakdown" % dimension)


def _suppress_flag(breakdown: BreakdownResult) -> bool:
    value = breakdown.flags.get("suppress_top_contributor")
    if type(value) is not bool:
        raise ValueError("the %s breakdown carries no boolean suppress_top_contributor flag: %r"
                         % ("x".join(breakdown.dimensions), value))
    return value


def _group_deltas(breakdown: BreakdownResult, dimension: str) -> List[GroupDelta]:
    ordered = order_groups(breakdown.groups, RANK_GROUP_DELTA, breakdown.dimensions)
    values = [(g.key[dimension],) + _group_values(g) for g in ordered]
    # v1 의미: 그 차원에서 늘어난 그룹들의 합 대비 비율. v2 의 순기여 비율과 다른 양이다.
    total_increase = sum(delta for _v, _c, _b, delta in values if delta > 0)
    out = []
    for value, current, baseline, delta in values:
        share = (round(delta / float(total_increase), 4)
                 if (delta > 0 and total_increase) else 0.0)
        out.append(GroupDelta(dimension, value, current, baseline, delta, share))
    return out


def _cell_deltas(breakdown: BreakdownResult) -> List[CellDelta]:
    ordered = order_groups(breakdown.groups, RANK_GROUP_DELTA, breakdown.dimensions)
    out = []
    for group in ordered:
        current, baseline, delta = _group_values(group)
        out.append(CellDelta(group.key["product"], group.key["complaint_type"],
                             current, baseline, delta))
    return out


def complaint_view(result: StructuredAnalysisResult) -> ComplaintAnalysisView:
    """v2 결과를 v1 호환 뷰로 옮긴다. 값은 엔진이 낸 정수만 쓰고 다시 합산하지 않는다."""
    if result.metric.get("name") != METRIC_COMPLAINT_COUNT or result.metric.get("kind") != KIND_ADDITIVE:
        raise ValueError("complaints view needs the additive %r metric, got %r"
                         % (METRIC_COMPLAINT_COUNT, result.metric))
    current_total = _exact_int(result.comparison.get("current"), "comparison current")
    baseline_total = _exact_int(result.comparison.get("baseline"), "comparison baseline")
    delta = _exact_int(result.comparison.get("delta"), "comparison delta")
    if current_total - baseline_total != delta:
        raise ValueError("comparison: current - baseline = %d but delta = %d"
                         % (current_total - baseline_total, delta))

    cells = _cell_deltas(required_joint_breakdown(result))
    by_product = _group_deltas(_single_breakdown(result, DIM_PRODUCT), DIM_PRODUCT)
    by_type = _group_deltas(_single_breakdown(result, DIM_COMPLAINT_TYPE), DIM_COMPLAINT_TYPE)

    suppressed = {dimension: _suppress_flag(_single_breakdown(result, dimension))
                  for dimension in (DIM_PRODUCT, DIM_COMPLAINT_TYPE)}

    pct = (round(100.0 * (current_total - baseline_total) / float(baseline_total), 2)
           if baseline_total else None)
    return ComplaintAnalysisView(current_total, baseline_total, delta, pct,
                                 by_product, by_type, cells, suppressed)


def _period_integrity(coverage, conflicts) -> PeriodIntegrity:
    return PeriodIntegrity(
        period=coverage.period,
        observed_days=coverage.observed_days,
        missing_days=list(coverage.missing_days),
        duplicate_rows_removed=coverage.duplicate_rows_removed,
        conflicting_keys=sorted(v.label for v in conflicts),
        present_offsets=list(coverage.observed_offsets),
    )


def legacy_views(q):
    """qualification 결과를 v1 제품 경로가 직렬화하는 두 객체로 글자 그대로 되돌린다.
    reason 문장은 complaints 의 제품 문구라 공용 qualification 에 두지 않고 여기서 만든다."""
    if isinstance(q, RejectedQualification) and q.reason_code not in (
            REASON_CONFLICTING_DUPLICATE, REASON_EMPTY_PERIOD, REASON_INSUFFICIENT_COMMON_WINDOW):
        raise ValueError("complaints declares unknown_group and align_common_window; "
                         "reason %r cannot occur on this path" % q.reason_code)
    facts = q.facts
    current = _period_integrity(facts.current, facts.conflicts_in(SCOPE_CURRENT))
    baseline = _period_integrity(facts.baseline, facts.conflicts_in(SCOPE_BASELINE))
    if isinstance(q, ExecutableQualification):
        if q.action == ACTION_ACCEPT:
            comparability = Comparability(MODE_FULL, "both periods are complete and equal in length",
                                          q.effective.current, q.effective.baseline)
        else:
            d = dict(q.detail)
            comparability = Comparability(
                MODE_ALIGNED_WINDOW,
                "periods are not equally complete; compared on the aligned day-offset window %d..%d"
                % (d["start_offset"] + 1, d["end_offset"] + 1),
                q.effective.current, q.effective.baseline)
        return current, baseline, comparability
    if not isinstance(q, RejectedQualification):
        raise TypeError("expected a qualification result, got %r" % type(q))
    if q.reason_code == REASON_CONFLICTING_DUPLICATE:
        reason = ("conflicting_duplicate_rows: the same (day, product, complaint_type) key carries "
                  "two different counts, so no count can be trusted")
    elif q.reason_code == REASON_EMPTY_PERIOD:
        reason = "empty_period: one of the periods has no rows"
    else:
        d = dict(q.detail)
        if d["kind"] == WINDOW_NO_OVERLAP:
            reason = "no_comparable_window: no day-offset is present in both periods"
        else:
            reason = ("no_comparable_window: longest aligned window is %d day(s), below the required %d"
                      % (d["length"], d["threshold"]))
    return current, baseline, Comparability(MODE_BLOCKED, reason)
