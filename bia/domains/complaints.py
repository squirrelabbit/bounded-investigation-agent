"""v1 의 고객불만 분석을 선언 하나로 재표현한다. 동작은 여기 두지 않는다."""
from __future__ import annotations

from ..analysis.registry import register
from ..analysis.spec import DomainSpec, MetricSpec

SPEC = DomainSpec(
    name="complaints",
    grain=("day", "product", "complaint_type"),
    dimensions=("product", "complaint_type"),
    metrics={"complaint_count": MetricSpec(
        name="complaint_count", kind="additive", value="count")},
    partial_period_policy="align_common_window",
    min_comparable_days=7,
    min_comparable_ratio=0.5,
    null_dimension_policy="unknown_group",
)

register(SPEC)
