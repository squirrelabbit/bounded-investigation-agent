"""StructuredAnalysisResult 를 v1 evidence pipeline 이 쓰는 모양으로 옮긴다.

공용 엔진은 여기까지 오지 않는다. 도메인 동작은 adapter 의 책임이다.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from ..analysis.operators import CROSS_CELL_LIMIT, order_groups
from ..analysis.result import (STATUS_OK, BreakdownResult,
                               StructuredAnalysisResult)

JOINT_DIMENSIONS = ("product", "complaint_type")

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
