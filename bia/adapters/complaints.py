"""StructuredAnalysisResult 를 v1 evidence pipeline 이 쓰는 모양으로 옮긴다.

공용 엔진은 여기까지 오지 않는다. 도메인 동작은 adapter 의 책임이다.
"""
from __future__ import annotations

from typing import List, Tuple

from ..analysis.result import STATUS_OK, StructuredAnalysisResult

JOINT_DIMENSIONS = ("product", "complaint_type")


def top_contributor_cells(result: StructuredAnalysisResult) -> List[Tuple[str, str]]:
    """증가가 발생한 (제품, 불만 유형) 셀을 delta 내림차순으로 준다.

    v1 의 `metrics.compute(...).cells` 가 내던 순서와 같아야 한다 —
    delta 내림차순, 동률이면 product, complaint_type 오름차순.
    """
    for breakdown in result.breakdowns:
        if breakdown.cross and breakdown.dimensions == JOINT_DIMENSIONS:
            if breakdown.status != STATUS_OK:
                return []
            ordered = sorted(
                breakdown.groups,
                key=lambda g: (-g.net_contribution, g.key["product"], g.key["complaint_type"]),
            )
            return [(g.key["product"], g.key["complaint_type"])
                    for g in ordered if g.net_contribution > 0]
    return []
