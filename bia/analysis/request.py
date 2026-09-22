"""이번 실행에서 무엇을 분석할 것인가. 정적 선언과 별개 타입이다."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from ..types import Period

RANK_NET_CONTRIBUTION = "net_contribution"
RANK_GROUP_DELTA = "group_delta"
RANK_RATE_EFFECT = "rate_effect"
RANK_MIX_EFFECT = "mix_effect"

RANK_BY_KIND = {
    "additive": (RANK_NET_CONTRIBUTION, RANK_GROUP_DELTA),
    "ratio": (RANK_NET_CONTRIBUTION, RANK_RATE_EFFECT, RANK_MIX_EFFECT),
}


@dataclass(frozen=True)
class PeriodComparison:
    current: Period
    baseline: Period


@dataclass(frozen=True)
class AnalysisRequest:
    domain: str
    metric: str
    breakdowns: Tuple[str, ...]
    comparison: PeriodComparison
    rank_by: str = RANK_NET_CONTRIBUTION
