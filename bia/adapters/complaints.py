"""StructuredAnalysisResult 를 v1 evidence pipeline 이 쓰는 모양으로 옮긴다.

공용 엔진은 여기까지 오지 않는다. 도메인 동작은 adapter 의 책임이다.
"""
from __future__ import annotations

from typing import List, Tuple

from ..analysis.operators import order_groups
from ..analysis.result import STATUS_OK, StructuredAnalysisResult

JOINT_DIMENSIONS = ("product", "complaint_type")


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
    """
    for breakdown in result.breakdowns:
        if breakdown.cross and breakdown.dimensions == JOINT_DIMENSIONS:
            if breakdown.status != STATUS_OK:
                return []
            ordered = order_groups(breakdown.groups, "net_contribution",
                                   breakdown.dimensions)
            return [(g.key["product"], g.key["complaint_type"])
                    for g in ordered if g.net_contribution > 0]
    return []
