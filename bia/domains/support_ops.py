"""`day` 는 접수 cohort 날짜다. '오늘 해결 / 오늘 접수' 로 두면 동일 cohort 의
비율이 아니고 resolved > received 가 정상적으로 나올 수 있어, 비율 의미론을
검증하려는 목적과 어긋난다."""
from __future__ import annotations

from ..analysis.registry import register
from ..analysis.spec import DomainSpec, MetricSpec

SPEC = DomainSpec(
    name="support_ops",
    grain=("day", "queue", "priority"),
    dimensions=("queue", "priority"),
    metrics={
        "tickets_received": MetricSpec(
            name="tickets_received", kind="additive", value="received"),
        "sla_resolution_rate": MetricSpec(
            name="sla_resolution_rate", kind="ratio",
            numerator="resolved_within_sla", denominator="received",
            numerator_bounded_by_denominator=True,
        ),
    },
)

register(SPEC)
