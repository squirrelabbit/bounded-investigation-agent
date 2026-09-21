"""Verification. Always run by the server, never skippable, never delegated.

Every retrieved ticket is re-checked against the store, the allowed sources, the
comparison window, the filter it was supposed to satisfy, and whether its own
text supports the label it carries.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Sequence, Set, Tuple

from .evidence import MIN_ADMITTED_TICKETS, MIN_COVERAGE, AdmittedTicket
from .lexicon import supports_label
from .types import ALLOWED_TICKET_SOURCES, EvidenceFilter, Ticket

EXCERPT_CHARS = 120

REJECT_UNKNOWN_ID = "unknown_ticket_id"
REJECT_BAD_SOURCE = "source_not_allowed"
REJECT_OUT_OF_WINDOW = "outside_comparison_window"
REJECT_FILTER_MISMATCH = "does_not_match_requested_filter"
REJECT_UNSUPPORTED_TEXT = "text_does_not_support_its_own_label"


@dataclass
class VerificationResult:
    admitted: List[AdmittedTicket] = field(default_factory=list)
    rejected: List[Dict[str, str]] = field(default_factory=list)
    pool_size: int = 0

    @property
    def coverage(self) -> float:
        if self.pool_size <= 0:
            return 0.0
        return round(len(self.admitted) / float(self.pool_size), 4)

    @property
    def sufficient(self) -> bool:
        return len(self.admitted) >= MIN_ADMITTED_TICKETS and self.coverage >= MIN_COVERAGE

    def as_dict(self) -> Dict[str, object]:
        return {
            "admitted": [a.as_dict() for a in self.admitted],
            "rejected": list(self.rejected),
            "pool_size": self.pool_size,
            "coverage": self.coverage,
            "sufficient": self.sufficient,
        }


def _excerpt(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= EXCERPT_CHARS:
        return collapsed
    return collapsed[: EXCERPT_CHARS - 1] + "…"


def verify(
    hits: Sequence[Ticket],
    evidence_filter: EvidenceFilter,
    known_ticket_ids: Set[str],
    pool_size: int,
) -> VerificationResult:
    result = VerificationResult(pool_size=pool_size)
    for ticket in hits:
        reason = _reject_reason(ticket, evidence_filter, known_ticket_ids)
        if reason is not None:
            result.rejected.append({"ticket_id": ticket.ticket_id, "reason": reason})
            continue
        result.admitted.append(
            AdmittedTicket(
                ticket_id=ticket.ticket_id,
                day=ticket.day.isoformat(),
                product=ticket.product,
                complaint_type=ticket.complaint_type,
                source=ticket.source,
                excerpt=_excerpt(ticket.text),
            )
        )
    result.admitted.sort(key=lambda a: (a.day, a.ticket_id))
    result.rejected.sort(key=lambda r: r["ticket_id"])
    return result


def _reject_reason(ticket: Ticket, evidence_filter: EvidenceFilter, known: Set[str]):
    if ticket.ticket_id not in known:
        return REJECT_UNKNOWN_ID
    if ticket.source not in ALLOWED_TICKET_SOURCES:
        return REJECT_BAD_SOURCE
    if not evidence_filter.window.contains(ticket.day):
        return REJECT_OUT_OF_WINDOW
    if evidence_filter.product is not None and ticket.product != evidence_filter.product:
        return REJECT_FILTER_MISMATCH
    if evidence_filter.complaint_type is not None and ticket.complaint_type != evidence_filter.complaint_type:
        return REJECT_FILTER_MISMATCH
    if not supports_label(ticket.text, ticket.complaint_type):
        return REJECT_UNSUPPORTED_TEXT
    return None
