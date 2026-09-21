"""Core value types. Immutable where possible; no I/O here."""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

METRIC_COMPLAINT_COUNT = "complaint_count"
EVIDENCE_SOURCE_SUPPORT_TICKETS = "support_tickets"
CLAIM_POLICY_ASSOCIATION_ONLY = "association_only"

DIM_PRODUCT = "product"
DIM_COMPLAINT_TYPE = "complaint_type"
ALLOWED_BREAKDOWNS = (DIM_PRODUCT, DIM_COMPLAINT_TYPE)

ALLOWED_TICKET_SOURCES = ("web_form", "email", "in_app")

DEFER = "DEFER"


def parse_day(value: str) -> _dt.date:
    return _dt.date.fromisoformat(value)


@dataclass(frozen=True)
class Period:
    start: _dt.date
    end: _dt.date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("period end is before start: %s..%s" % (self.start, self.end))

    @staticmethod
    def of(start: str, end: str) -> "Period":
        return Period(parse_day(start), parse_day(end))

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def contains(self, day: _dt.date) -> bool:
        return self.start <= day <= self.end

    def dates(self) -> List[_dt.date]:
        return [self.start + _dt.timedelta(days=i) for i in range(self.days)]

    def sub(self, offset_start: int, offset_end: int) -> "Period":
        """Window by day-offset from the period start, inclusive."""
        return Period(
            self.start + _dt.timedelta(days=offset_start),
            self.start + _dt.timedelta(days=offset_end),
        )

    def as_dict(self) -> Dict[str, str]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat(), "days": self.days}

    def __str__(self) -> str:
        return "%s..%s" % (self.start.isoformat(), self.end.isoformat())


@dataclass(frozen=True)
class AnalysisIntent:
    """The only question shape this system accepts."""

    current_period: Period
    baseline_period: Period
    metric: str = METRIC_COMPLAINT_COUNT
    breakdowns: Tuple[str, ...] = ALLOWED_BREAKDOWNS
    evidence_source: str = EVIDENCE_SOURCE_SUPPORT_TICKETS
    claim_policy: str = CLAIM_POLICY_ASSOCIATION_ONLY

    def validate(self) -> None:
        if self.metric != METRIC_COMPLAINT_COUNT:
            raise UnsupportedIntent("metric must be %r, got %r" % (METRIC_COMPLAINT_COUNT, self.metric))
        if self.evidence_source != EVIDENCE_SOURCE_SUPPORT_TICKETS:
            raise UnsupportedIntent("evidence_source must be %r" % EVIDENCE_SOURCE_SUPPORT_TICKETS)
        if self.claim_policy != CLAIM_POLICY_ASSOCIATION_ONLY:
            raise UnsupportedIntent("claim_policy must be %r" % CLAIM_POLICY_ASSOCIATION_ONLY)
        if tuple(self.breakdowns) != ALLOWED_BREAKDOWNS:
            raise UnsupportedIntent("breakdowns must be %r" % (ALLOWED_BREAKDOWNS,))
        if self.current_period.start <= self.baseline_period.end:
            raise UnsupportedIntent("current_period must start after baseline_period ends")

    def as_dict(self) -> Dict[str, object]:
        return {
            "metric": self.metric,
            "current_period": self.current_period.as_dict(),
            "baseline_period": self.baseline_period.as_dict(),
            "breakdowns": list(self.breakdowns),
            "evidence_source": self.evidence_source,
            "claim_policy": self.claim_policy,
        }


class UnsupportedIntent(ValueError):
    """Raised when a question is outside the one supported question shape."""


@dataclass(frozen=True)
class MetricRow:
    day: _dt.date
    product: str
    complaint_type: str
    count: int

    @property
    def key(self) -> Tuple[str, str, str]:
        return (self.day.isoformat(), self.product, self.complaint_type)


@dataclass(frozen=True)
class Ticket:
    ticket_id: str
    day: _dt.date
    product: str
    complaint_type: str
    text: str
    source: str

    def as_dict(self) -> Dict[str, str]:
        return {
            "ticket_id": self.ticket_id,
            "day": self.day.isoformat(),
            "product": self.product,
            "complaint_type": self.complaint_type,
            "text": self.text,
            "source": self.source,
        }


@dataclass(frozen=True)
class EvidenceFilter:
    """A closed, server-built retrieval condition. The model never writes one."""

    window: Period
    product: Optional[str] = None
    complaint_type: Optional[str] = None

    def matches(self, ticket: Ticket) -> bool:
        if not self.window.contains(ticket.day):
            return False
        if self.product is not None and ticket.product != self.product:
            return False
        if self.complaint_type is not None and ticket.complaint_type != self.complaint_type:
            return False
        return True

    def identity(self) -> str:
        return "%s|%s|%s" % (self.product or "*", self.complaint_type or "*", self.window)

    def as_dict(self) -> Dict[str, object]:
        return {
            "product": self.product,
            "complaint_type": self.complaint_type,
            "window": self.window.as_dict(),
        }


@dataclass(frozen=True)
class EvidenceCandidate:
    """Server-generated. The DecisionProvider may only return one of these ids, or DEFER."""

    candidate_id: str
    kind: str
    label: str
    evidence_filter: EvidenceFilter
    group_delta: int
    available_tickets: int
    server_reason: str

    def as_dict(self) -> Dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "kind": self.kind,
            "label": self.label,
            "filter": self.evidence_filter.as_dict(),
            "group_delta": self.group_delta,
            "available_tickets": self.available_tickets,
            "server_reason": self.server_reason,
        }


@dataclass
class GroupDelta:
    dimension: str
    value: str
    current: int
    baseline: int
    delta: int
    share_of_increase: float

    def as_dict(self) -> Dict[str, object]:
        return {
            "dimension": self.dimension,
            "value": self.value,
            "current": self.current,
            "baseline": self.baseline,
            "delta": self.delta,
            "share_of_increase": self.share_of_increase,
        }


@dataclass
class CellDelta:
    product: str
    complaint_type: str
    current: int
    baseline: int
    delta: int

    def as_dict(self) -> Dict[str, object]:
        return {
            "product": self.product,
            "complaint_type": self.complaint_type,
            "current": self.current,
            "baseline": self.baseline,
            "delta": self.delta,
        }
