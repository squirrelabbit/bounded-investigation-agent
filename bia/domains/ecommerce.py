"""전자상거래 도메인 선언. 동작은 여기 두지 않는다."""
from __future__ import annotations

from ..analysis.registry import register
from ..analysis.spec import DomainSpec, MetricSpec

SPEC = DomainSpec(
    name="ecommerce",
    grain=("day", "channel", "device", "category"),
    dimensions=("channel", "device", "category"),
    metrics={
        "revenue": MetricSpec(name="revenue", kind="additive", value="revenue_krw"),
        "orders": MetricSpec(name="orders", kind="additive", value="orders"),
        "conversion_rate": MetricSpec(
            name="conversion_rate", kind="ratio",
            numerator="orders", denominator="sessions",
            numerator_bounded_by_denominator=True,
        ),
    },
)

register(SPEC)
